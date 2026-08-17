from __future__ import annotations

import argparse
import csv
import fcntl
import hashlib
import json
import logging
import os
import random
import re
import shutil
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from html import unescape
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests
import yaml

try:
    from auth.wechat_mp_tools import (
        ensure_login as ensure_wechat_login,
        remove_legacy_auth_file,
        service_environment,
    )
    from auth.storage import ensure_auth_layout
    from auth.dingtalk_notify import send_auth_status, send_crawl_status, send_login_qr
except ModuleNotFoundError:
    from src.auth.wechat_mp_tools import (
        ensure_login as ensure_wechat_login,
        remove_legacy_auth_file,
        service_environment,
    )
    from src.auth.storage import ensure_auth_layout
    from src.auth.dingtalk_notify import send_auth_status, send_crawl_status, send_login_qr
from .content import (
    article_url_key,
    normalize_article_url,
    read_json,
    safe_component,
    validate_article,
)


ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "config.yaml"
ACCOUNT_SOURCES_PATH = ROOT / "account_sources.csv"
RESULTS_ROOT = ROOT / "results"
ARTICLES_ROOT = RESULTS_ROOT / "articles"
RECORD_ROOT = RESULTS_ROOT / "record"
LOCK_PATH = RECORD_ROOT / ".crawler.lock"
MP_PROJECT = ROOT / "third_party" / "wechat-mp-tools"
MP_PYTHON = MP_PROJECT / ".venv" / "bin" / "python"
RSS_PROJECT = ROOT / "third_party" / "we-mp-rss"
RSS_PYTHON = RSS_PROJECT / ".venv" / "bin" / "python"
RSS_WORKER = Path(__file__).with_name("fallback_worker.py")
AUTH_CONFIG_DIR = ROOT / "src" / "auth" / "config"
WECHAT_AUTH_PATH = AUTH_CONFIG_DIR / "wechat-mp-tools.yaml"
LEGACY_WECHAT_AUTH_PATH = MP_PROJECT / "data" / "wechat_mp_config.json"
API_BASE = "http://127.0.0.1:5200"
PAGE_SIZE = 10
MAX_EMPTY_PAGES = 2
AUTH_WAIT_SECONDS = 300
LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")
AUTH_ERROR_MARKERS = (
    "账号池中无可用账号",
    "暂无可用微信读书账号",
    "凭证已失效",
    "登录已过期",
    "session expired",
)


class AuthenticationRequiredError(RuntimeError):
    """The crawler cannot continue until WeChat Reading QR login succeeds."""


def is_authentication_error(message: str) -> bool:
    text = str(message).lower()
    return any(marker.lower() in text for marker in AUTH_ERROR_MARKERS)


WECHAT_ARTICLE_URL_RE = re.compile(
    r"https?://mp\.weixin\.qq\.com/s(?:/|\?)[^\s\"'<>]+",
    re.IGNORECASE,
)


def ensure_runtime() -> None:
    target = MP_PYTHON.resolve()
    current = Path(sys.executable).resolve()
    if current == target:
        return
    if not target.is_file():
        raise RuntimeError(f"wechat-mp-tools 环境不存在: {target}")
    os.execv(str(target), [str(target), str(ROOT / "src" / "crawl.py"), *sys.argv[1:]])


def setup_logging(verbose: bool, tools_log_dir: Path | None = None) -> logging.Logger:
    logger = logging.getLogger("wx-crawl")
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    for handler in logger.handlers:
        handler.close()
    logger.handlers.clear()
    if tools_log_dir is not None:
        tools_log_dir.mkdir(parents=True, exist_ok=True)
        formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
        file_handler = RotatingFileHandler(
            tools_log_dir / "crawler.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(message)s"))
    console.setLevel(logging.INFO if verbose else logging.WARNING)
    logger.addHandler(console)
    logger.propagate = False
    return logger


@contextmanager
def single_instance():
    RECORD_ROOT.mkdir(parents=True, exist_ok=True)
    handle = LOCK_PATH.open("w", encoding="utf-8")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise RuntimeError("已有一个爬取任务正在运行") from exc
    handle.write(str(os.getpid()))
    handle.flush()
    try:
        yield
    finally:
        fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()


@dataclass(frozen=True)
class CrawlConfig:
    input_csv: Path
    mode: str
    articles_per_account: int
    incremental_max_days: int = 1


@dataclass
class RegisteredAccount:
    number: int
    mp_id: str
    name: str
    sample_url: str

    @property
    def label(self) -> str:
        return (
            f"{self.number}_{safe_component(self.mp_id, 80)}_"
            f"{safe_component(self.name, 70)}"
        )


@dataclass
class CrawledArticle:
    title: str
    publish_time: int
    size_bytes: int


@dataclass(frozen=True)
class ExistingArticle:
    key: str
    title: str
    publish_time: int
    path: Path


@dataclass
class AccountTiming:
    label: str
    name: str
    started_at: datetime
    started_monotonic: float
    ended_at: datetime | None = None
    duration_seconds: float = 0.0
    status: str = "运行中"
    articles: list[CrawledArticle] = field(default_factory=list)

    @property
    def new_articles(self) -> int:
        return len(self.articles)

    @property
    def new_size_bytes(self) -> int:
        return sum(article.size_bytes for article in self.articles)

    def add_article(self, title: str, publish_time: int, size_bytes: int) -> None:
        self.articles.append(
            CrawledArticle(title=title, publish_time=publish_time, size_bytes=size_bytes)
        )

    def finish(self, status: str) -> None:
        self.ended_at = datetime.now(LOCAL_TIMEZONE)
        self.duration_seconds = time.monotonic() - self.started_monotonic
        self.status = status


@dataclass
class RunReport:
    config: CrawlConfig
    started_at: datetime = field(default_factory=lambda: datetime.now(LOCAL_TIMEZONE))
    started_monotonic: float = field(default_factory=time.monotonic)
    accounts: list[AccountTiming] = field(default_factory=list)
    ended_at: datetime | None = None
    duration_seconds: float = 0.0
    status: str = "运行中"

    @property
    def stamp(self) -> str:
        return self.started_at.strftime("%Y_%m_%d_%H_%M_%S")

    def start_account(self, account: RegisteredAccount) -> AccountTiming:
        timing = AccountTiming(
            label=account.label,
            name=account.name,
            started_at=datetime.now(LOCAL_TIMEZONE),
            started_monotonic=time.monotonic(),
        )
        self.accounts.append(timing)
        return timing

    def finish(self, status: str) -> None:
        now = datetime.now(LOCAL_TIMEZONE)
        for timing in self.accounts:
            if timing.ended_at is None:
                timing.ended_at = now
                timing.duration_seconds = time.monotonic() - timing.started_monotonic
                timing.status = status
        self.ended_at = now
        self.duration_seconds = time.monotonic() - self.started_monotonic
        self.status = status


def format_wall_time(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else "-"


def format_duration(seconds: float) -> str:
    total_milliseconds = max(0, round(seconds * 1000))
    total_seconds, milliseconds = divmod(total_milliseconds, 1000)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{milliseconds:03d}"


def render_run_report(report: RunReport) -> str:
    mode = (
        f"增量模式（最多回溯 {report.config.incremental_max_days} 天）"
        if report.config.mode == "incremental"
        else (
            f"窗口模式（最新 {report.config.articles_per_account} 篇）"
        )
    )
    lines = [
        "微信公众号爬取时间报告",
        f"任务状态：{report.status}",
        f"抓取模式：{mode}",
        f"开始时间：{format_wall_time(report.started_at)}",
        f"结束时间：{format_wall_time(report.ended_at)}",
        f"总耗时：{format_duration(report.duration_seconds)}",
        f"公众号数量：{len(report.accounts)}",
        "",
        "各公众号耗时：",
    ]
    for index, timing in enumerate(report.accounts, start=1):
        lines.extend(
            [
                f"{index}. {timing.label}",
                f"   状态：{timing.status}",
                f"   开始时间：{format_wall_time(timing.started_at)}",
                f"   结束时间：{format_wall_time(timing.ended_at)}",
                f"   耗时：{format_duration(timing.duration_seconds)}",
                f"   本次新增文章：{timing.new_articles}",
            ]
        )
    return "\n".join(lines) + "\n"


def print_run_report(report: RunReport) -> None:
    text = render_run_report(report)
    print("\n" + text, end="", flush=True, file=sys.stderr)


def emit_json(payload: dict[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), flush=True)


def directory_size(path: Path) -> int:
    total = 0
    if not path.is_dir():
        return total
    for item in path.rglob("*"):
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:
            continue
    return total


def timestamp_seconds(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
        if value.isdigit():
            numeric = int(value)
        else:
            try:
                numeric_float = float(value)
            except ValueError:
                return None
            if numeric_float <= 0:
                return None
            numeric = int(numeric_float)
    elif isinstance(value, (int, float)):
        if value <= 0:
            return None
        numeric = int(value)
    else:
        return None

    if numeric <= 0:
        return None
    if numeric >= 10_000_000_000:
        numeric = int(numeric / 1000)
    return numeric if numeric > 0 else None


def publish_datetime(value: object) -> datetime | None:
    timestamp = timestamp_seconds(value)
    if timestamp is None:
        return None
    try:
        return datetime.fromtimestamp(timestamp, LOCAL_TIMEZONE)
    except (OSError, OverflowError, ValueError):
        return None


def extract_source_publish_time(raw_html: str) -> int | None:
    for pattern in (
        r'\bct\s*=\s*["\']?(\d+)["\']?',
        r'\bpublish_time\s*=\s*["\']?(\d+)["\']?',
    ):
        match = re.search(pattern, raw_html or "")
        if match:
            return timestamp_seconds(match.group(1))
    return None


def source_publication_timestamp(url: str, logger: logging.Logger) -> int | None:
    if str(MP_PROJECT) not in sys.path:
        sys.path.insert(0, str(MP_PROJECT))
    try:
        from backend.config import DEFAULT_HEADERS, get_proxies_dict, report_proxy_status
    except Exception as exc:
        logger.debug("无法加载 wechat-mp-tools 网络配置，跳过源头时间补充：%s", exc)
        return None

    proxy_url = None
    try:
        proxies = get_proxies_dict()
        if proxies:
            proxy_url = proxies.get("http")
        response = requests.get(
            url,
            headers={
                "User-Agent": DEFAULT_HEADERS["User-Agent"],
                "Referer": "https://mp.weixin.qq.com/",
            },
            proxies=proxies,
            timeout=30,
        )
        response.raise_for_status()
    except Exception as exc:
        try:
            report_proxy_status(proxy_url, success=False)
        except Exception:
            pass
        logger.debug("无法从文章源头确认发布时间 %s：%s", url, exc)
        return None

    try:
        report_proxy_status(proxy_url, success=True)
    except Exception:
        pass

    response.encoding = "utf-8"
    return extract_source_publish_time(response.text)


def format_publish_time(timestamp: int) -> str:
    published_at = publish_datetime(timestamp)
    if published_at is None:
        return ""
    return published_at.strftime("%Y_%m_%d_%H_%M_%S")


def article_directory_info(article_dir: Path) -> tuple[int, str]:
    timestamp = 0
    title = ""
    for metadata_name in ("data.json", "metadata.json", "fallback_metadata.json"):
        metadata = read_json(article_dir / metadata_name)
        if not title:
            title = unescape(str(metadata.get("title") or "")).strip()
        if timestamp <= 0:
            timestamp = timestamp_seconds(metadata.get("publish_time")) or 0

    name = article_dir.name
    if timestamp <= 0 and len(name) >= 19:
        try:
            parsed = datetime.strptime(name[:19], "%Y_%m_%d_%H_%M_%S").replace(
                tzinfo=LOCAL_TIMEZONE
            )
            timestamp = int(parsed.timestamp())
        except ValueError:
            pass
    if not title and len(name) > 20:
        title = name[20:]
    return timestamp, title


def write_csv_atomic(path: Path, headers: list[str], rows: list[list[object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.tmp"
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        writer.writerows(rows)
    os.replace(temporary, path)


def save_run_result_records(report: RunReport) -> Path:
    record_dir = RECORD_ROOT / report.stamp
    account_rows: list[list[object]] = []
    article_rows: list[list[object]] = []
    for timing in report.accounts:
        account_rows.append(
            [
                timing.name,
                timing.new_articles,
                f"{timing.duration_seconds / 60:.1f}",
                f"{timing.new_size_bytes / (1024 ** 2):.1f}",
            ]
        )
        for article in timing.articles:
            article_rows.append(
                [timing.name, article.title, format_publish_time(article.publish_time)]
            )
    account_rows.append(
        [
            "总计",
            sum(timing.new_articles for timing in report.accounts),
            f"{report.duration_seconds / 60:.1f}",
            f"{sum(timing.new_size_bytes for timing in report.accounts) / (1024 ** 2):.1f}",
        ]
    )

    write_csv_atomic(
        record_dir / "account_summary.csv",
        ["公众号名称", "新爬取文章数", "本次爬取耗时（mins）", "本次爬取文章存储空间（M）"],
        account_rows,
    )
    write_csv_atomic(
        record_dir / "article_details.csv",
        ["公众号名称", "爬取的文章名称", "文章发布时间"],
        article_rows,
    )
    return record_dir


def load_registry() -> list[RegisteredAccount]:
    if not ACCOUNT_SOURCES_PATH.is_file():
        return []
    with ACCOUNT_SOURCES_PATH.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"编号", "公众号唯一ID", "公众号名称", "示例文章链接"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise RuntimeError(f"公众号注册表字段不完整：{ACCOUNT_SOURCES_PATH}")
        accounts: list[RegisteredAccount] = []
        numbers: set[int] = set()
        mp_ids: set[str] = set()
        for row_number, row in enumerate(reader, start=2):
            try:
                number = int(row.get("编号") or 0)
            except ValueError:
                number = 0
            mp_id = str(row.get("公众号唯一ID") or "").strip()
            name = str(row.get("公众号名称") or "").strip()
            sample_url = normalize_article_url(row.get("示例文章链接") or "")
            if number < 1 or not mp_id or not name or not article_url_key(sample_url):
                raise RuntimeError(
                    f"公众号注册表第 {row_number} 行存在无效字段：{ACCOUNT_SOURCES_PATH}"
                )
            if number in numbers or mp_id in mp_ids:
                raise RuntimeError(
                    f"公众号注册表第 {row_number} 行存在重复编号或公众号 ID"
                )
            numbers.add(number)
            mp_ids.add(mp_id)
            accounts.append(RegisteredAccount(number, mp_id, name, sample_url))
    return sorted(accounts, key=lambda account: account.number)


def save_registry(registry: list[RegisteredAccount]) -> Path:
    rows = [
        [account.number, account.mp_id, account.name, account.sample_url]
        for account in sorted(registry, key=lambda item: item.number)
    ]
    write_csv_atomic(
        ACCOUNT_SOURCES_PATH,
        ["编号", "公众号唯一ID", "公众号名称", "示例文章链接"],
        rows,
    )
    return ACCOUNT_SOURCES_PATH


def account_directory(account: RegisteredAccount) -> Path:
    return ARTICLES_ROOT / account.label


def reconcile_account_directory(account: RegisteredAccount) -> Path:
    expected = account_directory(account)
    if expected.exists() or not ARTICLES_ROOT.is_dir():
        return expected
    stable_prefix = f"{account.number}_{safe_component(account.mp_id, 80)}_"
    matches = [
        path
        for path in ARTICLES_ROOT.iterdir()
        if path.is_dir() and path.name.startswith(stable_prefix)
    ]
    if len(matches) == 1:
        matches[0].rename(expected)
    elif len(matches) > 1:
        raise RuntimeError(f"公众号目录存在多个相同编号和 mp_id：{account.mp_id}")
    return expected


def update_global_summary(registry: list[RegisteredAccount] | None = None) -> Path:
    accounts = sorted(registry if registry is not None else load_registry(), key=lambda item: item.number)
    rows: list[list[object]] = []
    for account in accounts:
        account_dir = reconcile_account_directory(account)
        article_dirs = (
            [path for path in account_dir.iterdir() if path.is_dir()]
            if account_dir.is_dir()
            else []
        )
        latest_timestamp = 0
        latest_title = ""
        for article_dir in article_dirs:
            timestamp, title = article_directory_info(article_dir)
            if timestamp > latest_timestamp:
                latest_timestamp = timestamp
                latest_title = title
        rows.append(
            [
                account.number,
                account.mp_id,
                account.name,
                len(article_dirs),
                f"{directory_size(account_dir) / (1024 ** 2):.1f}",
                format_publish_time(latest_timestamp),
                latest_title,
            ]
        )

    write_csv_atomic(
        RESULTS_ROOT / "summary.csv",
        [
            "编号",
            "公众号唯一ID",
            "公众号名称",
            "文章总数",
            "文章总存储（M）",
            "最新文章时间",
            "最新文章标题",
        ],
        rows,
    )
    return RESULTS_ROOT / "summary.csv"


def load_config() -> CrawlConfig:
    data = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    crawl = data.get("crawl") or {}
    input_value = str(crawl.get("input_csv") or "").strip()
    if not input_value:
        raise RuntimeError("config.yaml 缺少 crawl.input_csv")
    input_csv = Path(input_value)
    if not input_csv.is_absolute():
        input_csv = ROOT / input_csv
    mode = str(crawl.get("mode") or "").strip().lower()
    if mode not in {"incremental", "window"}:
        raise RuntimeError("crawl.mode 必须是 incremental 或 window")
    try:
        value = int(crawl.get("articles_per_account"))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("config.yaml 缺少有效的 crawl.articles_per_account") from exc
    if value < 1:
        raise RuntimeError("crawl.articles_per_account 必须大于 0")
    max_days_raw = crawl.get("incremental_max_days", 1)
    try:
        max_days = int(max_days_raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("config.yaml 缺少有效的 crawl.incremental_max_days") from exc
    if max_days < 1:
        raise RuntimeError("crawl.incremental_max_days 必须大于 0")
    return CrawlConfig(
        input_csv=input_csv,
        mode=mode,
        articles_per_account=value,
        incremental_max_days=max_days,
    )


def discover_seed_urls(input_csv: Path) -> list[str]:
    urls: list[str] = []
    seen_keys: set[str] = set()
    with input_csv.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.reader(handle):
            for cell in row:
                for match in WECHAT_ARTICLE_URL_RE.finditer(unescape(str(cell))):
                    url = normalize_article_url(
                        match.group(0).rstrip(".,;:!?)]}，。；：！？）】》")
                    )
                    key = article_url_key(url)
                    if key and key not in seen_keys:
                        seen_keys.add(key)
                        urls.append(url)
    return urls


def merge_discovered_accounts(
    registry: list[RegisteredAccount], discovered: list[dict]
) -> tuple[list[RegisteredAccount], int]:
    by_mp_id = {account.mp_id: account for account in registry}
    next_number = max((account.number for account in registry), default=0) + 1
    added = 0
    for item in discovered:
        mp_id = str(item.get("fakeid") or "").strip()
        name = str(item.get("nickname") or mp_id).strip()
        sample_url = normalize_article_url(item.get("sample_url") or "")
        if not mp_id or not article_url_key(sample_url):
            continue
        account = by_mp_id.get(mp_id)
        if account is None:
            account = RegisteredAccount(next_number, mp_id, name, sample_url)
            registry.append(account)
            by_mp_id[mp_id] = account
            next_number += 1
            added += 1
        else:
            if name and account.name != name:
                account.name = name
            account.sample_url = sample_url
    registry.sort(key=lambda account: account.number)
    return registry, added


def refresh_registered_accounts(
    api: LocalAPI,
    registry: list[RegisteredAccount],
    resolved_by_url: dict[str, dict],
    logger: logging.Logger,
) -> tuple[int, int, int]:
    verified = 0
    renamed = 0
    warnings = 0
    for index, account in enumerate(registry, start=1):
        logger.info(
            "校验公众号身份 [%d/%d] %s", index, len(registry), account.name
        )
        key = article_url_key(account.sample_url)
        resolved = resolved_by_url.get(key)
        if resolved is None:
            try:
                resolved = retry(lambda: resolve_account(api, account.sample_url))
            except Exception as exc:
                if is_authentication_error(str(exc)):
                    raise AuthenticationRequiredError(str(exc)) from exc
                warnings += 1
                logger.warning(
                    "公众号身份校验失败，保留原信息 %s：%s", account.name, exc
                )
                continue
            resolved_by_url[key] = resolved

        resolved_mp_id = str(resolved.get("fakeid") or "").strip()
        resolved_name = str(resolved.get("nickname") or "").strip()
        if resolved_mp_id != account.mp_id:
            warnings += 1
            logger.error(
                "公众号 ID 校验不一致，保留原 ID：编号 %d，登记 %s，解析 %s",
                account.number,
                account.mp_id,
                resolved_mp_id or "空",
            )
            continue
        verified += 1
        if resolved_name and resolved_name != account.name:
            logger.info(
                "公众号名称已更新：%s -> %s", account.name, resolved_name
            )
            account.name = resolved_name
            renamed += 1
    return verified, renamed, warnings


class LocalAPI:
    def __init__(self):
        self.session = requests.Session()
        self.session.trust_env = False

    def request(self, method: str, path: str, **kwargs) -> dict:
        response = self.session.request(method, API_BASE + path, timeout=kwargs.pop("timeout", 90), **kwargs)
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError(f"wechat-mp-tools 返回非 JSON (HTTP {response.status_code})") from exc
        if response.status_code >= 400:
            raise RuntimeError(payload.get("error") or f"HTTP {response.status_code}")
        return payload

    def healthy(self) -> bool:
        try:
            self.request("GET", "/api/auth/status", timeout=2)
            return True
        except (requests.RequestException, RuntimeError):
            return False


def save_login_qr(url: str, qr_path: Path) -> Path:
    """Atomically save the short-lived login QR with owner-only permissions."""
    import qrcode

    qr_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = qr_path.parent / ".wechat-login-qr.tmp.png"
    image = qrcode.make(url)
    image.save(temporary_path)
    temporary_path.chmod(0o600)
    os.replace(temporary_path, qr_path)
    qr_path.chmod(0o600)
    return qr_path


def remove_login_qr(qr_path: Path) -> None:
    try:
        qr_path.unlink()
    except FileNotFoundError:
        pass


class ManagedService:
    def __init__(
        self,
        api: LocalAPI,
        logger: logging.Logger,
        tools_log_dir: Path,
        *,
        stop_existing_on_close: bool = False,
    ):
        self.api = api
        self.logger = logger
        self.tools_log_dir = tools_log_dir
        self.qr_path = tools_log_dir / "wechat-login-qr.png"
        self.service_log_path = tools_log_dir / "wechat-mp-tools.log"
        self.process: subprocess.Popen | None = None
        self.adopted_pid: int | None = None
        self.stop_existing_on_close = stop_existing_on_close
        self.log_handle = None

    def find_project_service_pid(self) -> int | None:
        expected_cwd = MP_PROJECT.resolve()
        for process_dir in Path("/proc").glob("[0-9]*"):
            try:
                arguments = process_dir.joinpath("cmdline").read_bytes().split(b"\0")
                arguments = [item.decode(errors="replace") for item in arguments if item]
                cwd = process_dir.joinpath("cwd").resolve()
            except (FileNotFoundError, PermissionError, OSError):
                continue
            if cwd != expected_cwd or not any(Path(item).name == "app.py" for item in arguments):
                continue
            if "--port" in arguments:
                index = arguments.index("--port")
                if index + 1 >= len(arguments) or arguments[index + 1] != "5200":
                    continue
            return int(process_dir.name)
        return None

    def start(self) -> None:
        if self.api.healthy():
            if self.stop_existing_on_close:
                self.adopted_pid = self.find_project_service_pid()
            self.logger.info(
                "使用已运行的 wechat-mp-tools 服务%s",
                "，no-agent 任务结束后关闭" if self.adopted_pid else "",
            )
            return
        self.tools_log_dir.mkdir(parents=True, exist_ok=True)
        self.log_handle = self.service_log_path.open("a", encoding="utf-8")
        self.process = subprocess.Popen(
            [str(MP_PYTHON), "app.py"],
            cwd=MP_PROJECT,
            env=service_environment(),
            stdout=self.log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(
                    f"wechat-mp-tools 启动失败，请查看 {self.service_log_path}"
                )
            if self.api.healthy():
                self.logger.info("wechat-mp-tools 已启动")
                return
            time.sleep(0.5)
        raise RuntimeError("wechat-mp-tools 启动超时")

    def ensure_login(self, *, force: bool = False, on_qr: Callable[[Path], None] | None = None) -> None:
        def notify_auth_status(status: str, detail: str = "") -> None:
            try:
                send_auth_status(status, detail)
            except Exception as exc:
                self.logger.warning("DingTalk 认证状态通知失败：%s", exc)

        def deliver_qr(path: Path) -> None:
            if on_qr is None:
                return
            try:
                on_qr(path)
            except Exception as exc:
                notify_auth_status("qr_error", str(exc))
                raise

        ensure_wechat_login(
            self.api,
            self.logger,
            self.qr_path,
            save_login_qr,
            remove_login_qr,
            LEGACY_WECHAT_AUTH_PATH,
            WECHAT_AUTH_PATH,
            AUTH_WAIT_SECONDS,
            force=force,
            on_qr=deliver_qr,
            on_login=lambda: notify_auth_status("success"),
            on_timeout=lambda: notify_auth_status("timeout"),
        )

    def close(self) -> None:
        remove_login_qr(self.qr_path)
        remove_legacy_auth_file(LEGACY_WECHAT_AUTH_PATH)
        if self.process is not None and self.process.poll() is None:
            self.logger.info("关闭本次启动的 wechat-mp-tools 服务")
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
                self.process.wait(timeout=10)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                if self.process.poll() is None:
                    os.killpg(self.process.pid, signal.SIGKILL)
                    self.process.wait(timeout=5)
        elif self.adopted_pid is not None:
            self.logger.info("关闭 no-agent 任务接管的 wechat-mp-tools 服务")
            try:
                process_group = os.getpgid(self.adopted_pid)
                if process_group == self.adopted_pid:
                    os.killpg(process_group, signal.SIGTERM)
                else:
                    os.kill(self.adopted_pid, signal.SIGTERM)
                for _ in range(100):
                    try:
                        os.kill(self.adopted_pid, 0)
                    except ProcessLookupError:
                        break
                    time.sleep(0.1)
                else:
                    os.kill(self.adopted_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if self.log_handle is not None:
            self.log_handle.close()


def resolve_account(api: LocalAPI, seed_url: str) -> dict:
    payload = api.request("POST", "/api/accounts/search", json={"keyword": seed_url})
    results = payload.get("results") or []
    if not results or not results[0].get("fakeid"):
        raise RuntimeError("未能从示例文章解析公众号")
    return results[0]


def fetch_history_page(api: LocalAPI, fakeid: str, begin: int) -> list[dict]:
    path = f"/api/articles/list/{quote(fakeid, safe='')}?begin={begin}&count={PAGE_SIZE}"
    payload = api.request("GET", path, timeout=120)
    articles = payload.get("articles") or []
    return sorted(
        articles,
        key=lambda item: timestamp_seconds(item.get("update_time") or item.get("publish_time")) or 0,
        reverse=True,
    )


def retry(operation, attempts: int = 3):
    error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except Exception as exc:
            error = exc
            if attempt < attempts:
                time.sleep(1.5 * attempt)
    assert error is not None
    raise error


def primary_download(url: str, title: str, work_parent: Path) -> tuple[dict, Path]:
    if str(MP_PROJECT) not in sys.path:
        sys.path.insert(0, str(MP_PROJECT))
    from backend import downloader

    original_get_settings = downloader.get_settings
    settings = dict(original_get_settings())
    settings["auto_save_images"] = True
    settings["auto_save_videos"] = False
    downloader.get_settings = lambda: settings
    try:
        result = downloader.download_single_article(url, work_parent, title)
    finally:
        downloader.get_settings = original_get_settings
    path_value = result.get("path")
    if path_value:
        return result, Path(path_value)
    candidates = sorted(path for path in work_parent.iterdir() if path.is_dir())
    if candidates:
        return result, candidates[0]
    article_dir = work_parent / safe_component(title, 60)
    article_dir.mkdir(parents=True, exist_ok=True)
    return result, article_dir


def run_fallback(
    url: str,
    title: str,
    article_dir: Path,
    logger: logging.Logger,
    tools_log_dir: Path,
) -> bool:
    env = os.environ.copy()
    env.update({"HEADLESS": "true", "BROWSER_TYPE": "chromium"})
    completed = subprocess.run(
        [str(RSS_PYTHON), str(RSS_WORKER), "--url", url, "--output", str(article_dir), "--title", title],
        cwd=RSS_PROJECT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=180,
    )
    if completed.stdout:
        fallback_log = tools_log_dir / "we-mp-rss.log"
        with fallback_log.open("a", encoding="utf-8") as handle:
            handle.write(
                f"\n[{datetime.now(LOCAL_TIMEZONE):%Y-%m-%d %H:%M:%S}] {title}\n"
            )
            handle.write(completed.stdout)
            if not completed.stdout.endswith("\n"):
                handle.write("\n")
    if completed.returncode != 0:
        tail = completed.stdout.strip().splitlines()[-1:] or ["未知错误"]
        logger.warning("we-mp-rss 回退失败：%s", tail[0])
        return False
    return True


def publication_timestamp(primary: dict, history: dict, article_dir: Path) -> int:
    values = [primary.get("publish_time")]
    for name in ("metadata.json", "data.json", "fallback_metadata.json"):
        values.append(read_json(article_dir / name).get("publish_time"))
    values.extend((history.get("update_time"), time.time()))
    for value in values:
        timestamp = timestamp_seconds(value)
        if timestamp is not None:
            return timestamp
    return int(time.time())


def final_destination(account_dir: Path, timestamp: int, title: str, url: str) -> Path:
    published = datetime.fromtimestamp(timestamp, LOCAL_TIMEZONE).strftime("%Y_%m_%d_%H_%M_%S")
    base = safe_component(f"{published}_{title}", 145)
    destination = account_dir / base
    if destination.exists():
        suffix = hashlib.sha256(url.encode("utf-8")).hexdigest()[:8]
        destination = account_dir / safe_component(f"{base}_{suffix}", 154)
    return destination


def existing_article_index(account_dir: Path) -> dict[str, ExistingArticle]:
    articles: dict[str, ExistingArticle] = {}
    if not account_dir.is_dir():
        return articles
    for article_dir in account_dir.iterdir():
        if not article_dir.is_dir():
            continue
        for metadata_name in ("data.json", "metadata.json", "fallback_metadata.json"):
            metadata = read_json(article_dir / metadata_name)
            url = str(metadata.get("url") or "")
            key = article_url_key(url)
            if key:
                articles[key] = ExistingArticle(
                    key=key,
                    title=unescape(str(metadata.get("title") or "")).strip(),
                    publish_time=timestamp_seconds(metadata.get("publish_time")) or 0,
                    path=article_dir,
                )
                break
    return articles


def existing_article_keys(account_dir: Path) -> set[str]:
    return set(existing_article_index(account_dir))


def article_publish_datetime(
    article: dict,
    existing_article: ExistingArticle | None,
    logger: logging.Logger | None = None,
) -> datetime | None:
    for value in (
        article.get("publish_time"),
        article.get("update_time"),
        existing_article.publish_time if existing_article is not None else None,
    ):
        published_at = publish_datetime(value)
        if published_at is not None:
            return published_at
    if logger is None:
        return None
    url = normalize_article_url(article.get("link") or article.get("url") or "")
    if not url:
        return None
    return publish_datetime(source_publication_timestamp(url, logger))


def fetch_nonempty_history_page(api: LocalAPI, fakeid: str, begin: int) -> list[dict]:
    articles = retry(lambda: fetch_history_page(api, fakeid, begin))
    if articles:
        return articles
    for retry_index in range(2):
        time.sleep(1.5 * (retry_index + 1))
        articles = retry(lambda: fetch_history_page(api, fakeid, begin), attempts=2)
        if articles:
            return articles
    return []


def collect_history_candidates(
    api: LocalAPI,
    fakeid: str,
    config: CrawlConfig,
    existing_keys: set[str],
    existing_articles: dict[str, ExistingArticle],
    logger: logging.Logger,
    now: datetime | None = None,
) -> list[tuple[str, str, dict]]:
    candidates: list[tuple[str, str, dict]] = []
    seen_keys: set[str] = set()
    begin = 0
    empty_pages = 0
    considered = 0
    incremental_cutoff = None
    if config.mode == "incremental":
        current_time = now or datetime.now(LOCAL_TIMEZONE)
        if current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=LOCAL_TIMEZONE)
        else:
            current_time = current_time.astimezone(LOCAL_TIMEZONE)
        incremental_cutoff = current_time - timedelta(days=config.incremental_max_days)

    while True:
        articles = fetch_nonempty_history_page(api, fakeid, begin)
        begin += PAGE_SIZE
        if not articles:
            break

        fresh_on_page = 0
        for article in articles:
            url = normalize_article_url(article.get("link") or "")
            key = article_url_key(url)
            if not key or key in seen_keys:
                continue
            seen_keys.add(key)
            fresh_on_page += 1

            if incremental_cutoff is not None:
                published_at = article_publish_datetime(
                    article,
                    existing_articles.get(key),
                    logger,
                )
                if published_at is None:
                    logger.info(
                        "文章时间无法确认，跳过本篇并继续回溯（最大回溯范围 %d 天）",
                        config.incremental_max_days,
                    )
                    continue
                if published_at < incremental_cutoff:
                    logger.info(
                        "文章发布时间 %s 早于回溯边界 %s（%d 天），停止继续回溯",
                        published_at.strftime("%Y-%m-%d %H:%M:%S %Z"),
                        incremental_cutoff.strftime("%Y-%m-%d %H:%M:%S %Z"),
                        config.incremental_max_days,
                    )
                    return candidates

            if config.mode == "incremental" and existing_keys:
                if key in existing_keys:
                    logger.info("已找到上次抓取断点，本次发现 %d 篇候选新文章", len(candidates))
                    return candidates
                candidates.append((url, key, article))
                continue

            considered += 1
            if key not in existing_keys:
                candidates.append((url, key, article))
            if considered >= config.articles_per_account:
                return candidates

        if fresh_on_page == 0:
            empty_pages += 1
            if empty_pages >= MAX_EMPTY_PAGES:
                break
        else:
            empty_pages = 0
        time.sleep(random.uniform(1.0, 2.0))

    if config.mode == "incremental" and not existing_keys:
        logger.info(
            "该公众号没有历史断点，增量模式仅检查最新 %d 篇",
            config.articles_per_account,
        )
    return candidates


def combine_article_candidates(
    explicit_urls: list[str],
    history_candidates: list[tuple[str, str, dict]],
    existing_keys: set[str],
) -> list[tuple[str, str, dict]]:
    candidates: list[tuple[str, str, dict]] = []
    seen_keys: set[str] = set()
    for explicit_url in explicit_urls:
        url = normalize_article_url(explicit_url)
        key = article_url_key(url)
        if not key or key in existing_keys or key in seen_keys:
            continue
        seen_keys.add(key)
        candidates.append(
            (url, key, {"link": url, "title": "输入文章", "update_time": 0})
        )
    for url, key, article in history_candidates:
        if key in existing_keys or key in seen_keys:
            continue
        seen_keys.add(key)
        candidates.append((url, key, article))
    return candidates


def crawl_account(
    api: LocalAPI,
    account: RegisteredAccount,
    work_root: Path,
    config: CrawlConfig,
    timing: AccountTiming,
    logger: logging.Logger,
    tools_log_dir: Path,
    explicit_urls: list[str],
) -> int:
    label = account.label
    account_dir = reconcile_account_directory(account)
    existing_articles = existing_article_index(account_dir)
    existing_keys = set(existing_articles)
    logger.info("公众号 %s 已有 %d 篇可按 URL 识别的文章", account.name, len(existing_keys))
    history_candidates = collect_history_candidates(
        api, account.mp_id, config, existing_keys, existing_articles, logger
    )
    candidates = combine_article_candidates(
        explicit_urls, history_candidates, existing_keys
    )
    explicit_keys = {
        key
        for url in explicit_urls
        if (key := article_url_key(normalize_article_url(url)))
    }
    explicit_pending = len(explicit_keys - existing_keys)
    if explicit_urls:
        logger.info(
            "公众号 %s 有 %d 篇登记/输入文章需要校验，其中 %d 篇尚未抓取",
            account.name,
            len(explicit_keys),
            explicit_pending,
        )
    if not candidates:
        logger.info("公众号 %s 没有需要抓取的新文章", account.name)
        return 0

    account_work = work_root / label
    account_work.mkdir(parents=True, exist_ok=True)
    candidates_dir = account_work / "candidates"
    candidates_dir.mkdir()
    successes = 0
    try:
        for url, url_key, history in candidates:
            title_hint = str(history.get("title") or "未命名").strip()
            candidate_key = hashlib.sha256(url_key.encode("utf-8")).hexdigest()[:16]
            candidate_parent = candidates_dir / candidate_key
            candidate_parent.mkdir(parents=True, exist_ok=True)
            primary: dict = {}
            article_dir = candidate_parent / safe_component(title_hint, 60)
            try:
                try:
                    primary, article_dir = primary_download(url, title_hint, candidate_parent)
                except Exception as exc:
                    primary = {"success": False, "error": f"首选下载器异常: {exc}"}
                    article_dir.mkdir(parents=True, exist_ok=True)
                actual_title = str(primary.get("title") or title_hint).strip()
                result = validate_article(article_dir, actual_title) if primary.get("success") else None
                if result is None or not result.success:
                    reason = result.reason if result else primary.get("error", "正文下载失败")
                    logger.info("%s：首选正文不完整（%s），尝试 we-mp-rss", title_hint, reason)
                    run_fallback(url, actual_title, article_dir, logger, tools_log_dir)
                    fallback_title = str(
                        read_json(article_dir / "fallback_metadata.json").get("title") or ""
                    ).strip()
                    if fallback_title:
                        actual_title = fallback_title
                    result = validate_article(article_dir, actual_title)
                if not result.success:
                    logger.warning("跳过无法完整抓取的文章：%s", title_hint)
                    shutil.rmtree(candidate_parent, ignore_errors=True)
                    time.sleep(random.uniform(1.0, 2.0))
                    continue

                timestamp = publication_timestamp(primary, history, article_dir)
                account_dir.mkdir(parents=True, exist_ok=True)
                destination = final_destination(account_dir, timestamp, actual_title, url)
                article_dir.rename(destination)
                shutil.rmtree(candidate_parent, ignore_errors=True)
                timing.add_article(actual_title, timestamp, directory_size(destination))
                existing_keys.add(url_key)
                successes += 1
                logger.info(
                    "[%s] 新增 %d/%d %s（文本 %d 字，图片 %d 张）",
                    account.name, successes, len(candidates), actual_title,
                    result.text_length, result.image_count,
                )
            except subprocess.TimeoutExpired:
                logger.warning("正文回退超时，跳过：%s", title_hint)
                shutil.rmtree(candidate_parent, ignore_errors=True)
            except Exception as exc:
                logger.exception("文章处理失败，跳过 %s：%s", title_hint, exc)
                shutil.rmtree(candidate_parent, ignore_errors=True)
            time.sleep(random.uniform(1.0, 2.5))
    finally:
        shutil.rmtree(account_work, ignore_errors=True)
    logger.info(
        "公众号 %s 完成：候选 %d 篇，本次成功新增 %d 篇",
        account.name, len(candidates), successes,
    )
    return successes


def execute(
    config: CrawlConfig,
    seed_urls: list[str],
    report: RunReport,
    logger: logging.Logger,
    tools_log_dir: Path,
    *,
    stop_existing_service: bool = False,
) -> Path:
    work_root = RESULTS_ROOT / ".working" / report.stamp
    RESULTS_ROOT.mkdir(parents=True, exist_ok=True)
    ARTICLES_ROOT.mkdir(parents=True, exist_ok=True)
    RECORD_ROOT.mkdir(parents=True, exist_ok=True)
    work_root.mkdir(parents=True, exist_ok=False)

    api = LocalAPI()
    service = ManagedService(
        api,
        logger,
        tools_log_dir,
        stop_existing_on_close=stop_existing_service,
    )
    try:
        service.start()
        service.ensure_login(
            on_qr=lambda path: send_login_qr(path, logger.info),
        )
        discovered_by_mp_id: dict[str, dict] = {}
        explicit_urls_by_mp_id: dict[str, list[str]] = {}
        resolved_by_url: dict[str, dict] = {}
        for index, seed_url in enumerate(seed_urls, start=1):
            logger.info("解析输入文章链接 [%d/%d]", index, len(seed_urls))
            try:
                resolved = retry(lambda: resolve_account(api, seed_url))
            except Exception as exc:
                logger.error("输入文章链接无法解析，跳过 %s：%s", seed_url, exc)
                continue
            mp_id = str(resolved.get("fakeid") or "").strip()
            if mp_id:
                resolved_by_url[article_url_key(seed_url)] = resolved
                discovered = dict(resolved)
                discovered["sample_url"] = seed_url
                discovered_by_mp_id[mp_id] = discovered
                explicit_urls_by_mp_id.setdefault(mp_id, []).append(seed_url)

        registry, added_accounts = merge_discovered_accounts(
            load_registry(), list(discovered_by_mp_id.values())
        )
        if not registry:
            raise RuntimeError("没有可处理的公众号：输入链接均未成功解析，注册表也为空")
        verified, renamed, validation_warnings = refresh_registered_accounts(
            api, registry, resolved_by_url, logger
        )
        save_registry(registry)
        update_global_summary(registry)
        logger.info(
            "公众号发现完成：输入解析出 %d 个，本次新增 %d 个，注册表共 %d 个；"
            "身份验证成功 %d 个、名称更新 %d 个、警告 %d 个；"
            "已同步 %s 和 results/summary.csv",
            len(discovered_by_mp_id),
            added_accounts,
            len(registry),
            verified,
            renamed,
            validation_warnings,
            ACCOUNT_SOURCES_PATH,
        )

        incomplete_accounts: list[str] = []
        for index, account in enumerate(registry, start=1):
            timing = report.start_account(account)
            logger.info("处理公众号 [%d/%d] %s", index, len(registry), account.name)
            try:
                explicit_urls = [account.sample_url]
                explicit_urls.extend(explicit_urls_by_mp_id.get(account.mp_id, []))
                crawl_account(
                    api,
                    account,
                    work_root,
                    config,
                    timing,
                    logger,
                    tools_log_dir,
                    explicit_urls,
                )
                timing.finish("成功")
            except Exception as exc:
                logger.error("公众号处理失败 %s：%s", account.name, exc)
                timing.finish("处理失败")
                incomplete_accounts.append(account.name)
        if incomplete_accounts:
            raise RuntimeError(
                f"{len(incomplete_accounts)} 个公众号处理失败，"
                f"详情见 {tools_log_dir / 'crawler.log'}"
            )
    finally:
        try:
            service.close()
        finally:
            shutil.rmtree(work_root, ignore_errors=True)
            working_parent = RESULTS_ROOT / ".working"
            try:
                working_parent.rmdir()
            except OSError:
                pass
    return RESULTS_ROOT


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="抓取登记表中的微信公众号最近文章")
    parser.add_argument("--check", action="store_true", help="只检查配置、输入与依赖，不联网抓取")
    parser.add_argument("--verbose", action="store_true", help="输出详细日志")
    parser.add_argument(
        "--notify",
        action="store_true",
        help="结束后将简短结果直接发送到配置的 DingTalk 群（供 no-agent 定时任务使用）",
    )
    parser.add_argument(
        "--scheduled",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    return parser.parse_args()


def emit_final_result(
    payload: dict[str, object], *, notify: bool, logger: logging.Logger
) -> None:
    if notify:
        try:
            send_crawl_status(payload)
            payload["notification"] = "sent"
        except Exception as exc:
            payload["notification"] = "failed"
            payload["notification_error"] = str(exc)[:500]
            logger.error("DingTalk 爬取结果通知失败：%s", exc)
    emit_json(payload)


def preflight(config: CrawlConfig) -> list[str]:
    if not config.input_csv.exists():
        raise RuntimeError(f"输入 CSV 不存在：{config.input_csv}")
    if not config.input_csv.is_file():
        raise RuntimeError(f"输入 CSV 不是普通文件：{config.input_csv}")
    if config.input_csv.suffix.lower() != ".csv":
        raise RuntimeError(f"输入文件必须是 CSV 格式：{config.input_csv}")
    missing = [path for path in (MP_PYTHON, RSS_PYTHON, RSS_WORKER) if not path.exists()]
    if missing:
        raise RuntimeError("缺少运行依赖: " + ", ".join(str(path) for path in missing))
    ensure_auth_layout(ROOT, MP_PROJECT, RSS_PROJECT)
    seed_urls = discover_seed_urls(config.input_csv)
    if not seed_urls and not load_registry():
        raise RuntimeError(
            "输入 CSV 中未识别到微信公众号文章链接，且现有公众号注册表为空"
        )
    return seed_urls


def main() -> None:
    ensure_runtime()
    args = parse_args()
    logger = setup_logging(args.verbose)
    report: RunReport | None = None
    final_status = "失败"
    final_error = ""
    output_emitted = False
    try:
        config = load_config()
        seed_urls = preflight(config)
        registered_count = len(load_registry())
        if args.check:
            if not seed_urls:
                logger.warning(
                    "警告：输入 CSV 中未识别到微信公众号文章链接；"
                    "本次不会新增公众号，仍可爬取已登记的 %d 个公众号",
                    registered_count,
                )
            if config.mode == "window":
                detail = f"窗口模式，检查每个公众号最新 {config.articles_per_account} 篇"
            else:
                detail = (
                    "增量模式，已有公众号抓取断点后的新文章，"
                    f"新公众号检查最新 {config.articles_per_account} 篇，"
                    f"最多回溯 {config.incremental_max_days} 天"
                )
            logger.info(
                "检查通过：输入文件发现 %d 个公众号文章链接；注册表已有 %d 个公众号；%s",
                len(seed_urls), registered_count, detail,
            )
            emit_final_result(
                {
                    "status": "ok",
                    "command": "check",
                    "input_csv": str(config.input_csv),
                    "seed_url_count": len(seed_urls),
                    "registered_account_count": registered_count,
                    "mode": config.mode,
                },
                notify=False,
                logger=logger,
            )
            output_emitted = True
            return
        candidate_report = RunReport(config=config)
        record_dir = RECORD_ROOT / candidate_report.stamp
        if record_dir.exists():
            raise RuntimeError(f"本次运行记录目录已存在，请稍后重试：{record_dir}")
        report = candidate_report
        tools_log_dir = record_dir / "tools-log"
        logger = setup_logging(args.verbose, tools_log_dir)
        if not seed_urls:
            logger.warning(
                "警告：输入 CSV 中未识别到微信公众号文章链接；"
                "本次不会新增公众号，将继续爬取已登记的 %d 个公众号",
                registered_count,
            )
        with single_instance():
            result_dir = execute(
                config,
                seed_urls,
                report,
                logger,
                tools_log_dir,
                stop_existing_service=args.notify or args.scheduled,
            )
        final_status = "成功"
        logger.info("爬取结束，结果目录：%s", result_dir)
    except KeyboardInterrupt:
        final_status = "已中断"
        final_error = "用户中止爬取"
        logger.error("用户中止爬取")
        raise SystemExit(130)
    except Exception as exc:
        final_status = "失败"
        final_error = str(exc)
        logger.error("爬取未完成：%s", exc)
        raise SystemExit(1)
    finally:
        if report is not None:
            report.finish(final_status)
            if args.verbose:
                print_run_report(report)
            saved_record_dir: Path | None = None
            summary_path: Path | None = None
            try:
                saved_record_dir = save_run_result_records(report)
                registry = load_registry()
                summary_path = update_global_summary(registry) if registry else RESULTS_ROOT / "summary.csv"
            except Exception as exc:
                logger.error("无法保存结果统计 CSV：%s", exc)
                if not final_error:
                    final_error = f"无法保存结果统计 CSV：{exc}"
            emit_final_result(
                {
                    "status": {"成功": "ok", "已中断": "interrupted"}.get(final_status, "failed"),
                    "command": "crawl",
                    "run_id": report.stamp,
                    "mode": report.config.mode,
                    "account_count": len(report.accounts),
                    "new_article_count": sum(item.new_articles for item in report.accounts),
                    "failed_account_count": sum(item.status != "成功" for item in report.accounts),
                    "duration_seconds": round(report.duration_seconds, 3),
                    "record_dir": str(saved_record_dir) if saved_record_dir else None,
                    "summary_file": str(summary_path) if summary_path else None,
                    "details_file": str((saved_record_dir / "account_summary.csv")) if saved_record_dir else None,
                    "error": final_error or None,
                },
                notify=args.notify,
                logger=logger,
            )
            output_emitted = True
        if not output_emitted:
            emit_final_result(
                {"status": "failed", "command": "crawl", "error": final_error or "unknown error"},
                notify=args.notify,
                logger=logger,
            )
