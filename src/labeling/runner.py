from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .model import LabelModel
from .prompt import build_article_prompt, build_system_prompt, read_rules
from .schema import read_label, validate_payload


REPO_ROOT = Path(__file__).resolve().parents[2]
ARTICLES_ROOT = REPO_ROOT / "results" / "articles"
RECORD_ROOT = REPO_ROOT / "results" / "record"
SELECTOR_PATH = REPO_ROOT / "skill" / "article-label-export" / "scripts" / "select_articles.py"
ARTICLE_MARKERS = ("content.txt", "metadata.json", "data.json")
LOGGER = logging.getLogger("wechat-labeling")
METADATA_KEYS = (
    "title",
    "url",
    "publish_time",
    "account_name",
    "author",
    "digest",
    "description",
)


class LabelingError(ValueError):
    pass


@dataclass(frozen=True)
class ArticleInput:
    article_dir: Path
    metadata: dict[str, Any]
    content: str

    @property
    def evidence_text(self) -> str:
        return json.dumps(self.metadata, ensure_ascii=False) + "\n" + self.content


def discover_article_dirs(root: Path = ARTICLES_ROOT) -> list[Path]:
    if not root.is_dir():
        return []
    result: list[Path] = []
    for account_dir in root.iterdir():
        if not account_dir.is_dir():
            continue
        for article_dir in account_dir.iterdir():
            if not article_dir.is_dir():
                continue
            if any((article_dir / name).is_file() for name in ARTICLE_MARKERS):
                result.append(article_dir)
    return sorted(result, key=lambda path: (path.stat().st_mtime_ns, str(path)), reverse=True)


def resolve_article_dir(raw: str, root: Path = ARTICLES_ROOT) -> Path:
    path = Path(raw).resolve()
    try:
        relative = path.relative_to(root.resolve())
    except ValueError as exc:
        raise LabelingError(f"article directory is outside {root.resolve()}: {path}") from exc
    if len(relative.parts) != 2 or not path.is_dir():
        raise LabelingError(f"not a two-level article directory: {path}")
    return path


def resolve_run_dir(raw: str, root: Path = RECORD_ROOT) -> Path:
    path = Path(raw).resolve()
    try:
        relative = path.relative_to(root.resolve())
    except ValueError as exc:
        raise LabelingError(f"run directory is outside {root.resolve()}: {path}") from exc
    if len(relative.parts) != 1 or not path.is_dir():
        raise LabelingError(f"not a run directory: {path}")
    if not (path / "article_details.csv").is_file():
        raise LabelingError(f"article_details.csv is missing: {path}")
    return path


def _parse_json_object(output: str) -> dict[str, Any]:
    """Recover the last JSON object from selector stdout, including mixed log output."""
    decoder = json.JSONDecoder()
    candidates: list[tuple[int, dict[str, Any]]] = []
    for index, char in enumerate(output):
        if char != "{":
            continue
        try:
            candidate, length = decoder.raw_decode(output[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict):
            candidates.append((length, candidate))
    if not candidates:
        raise LabelingError("article selector returned no JSON object")
    # The top-level selector payload encloses per-article objects, so it is the
    # largest complete JSON object even when log lines surround stdout.
    return max(candidates, key=lambda item: item[0])[1]


def discover_run_article_dirs(
    run_dir: Path,
    *,
    record_root: Path = RECORD_ROOT,
    articles_root: Path = ARTICLES_ROOT,
    selector_path: Path = SELECTOR_PATH,
) -> list[Path]:
    """Resolve exactly the article directories used by the Python selector for one run."""
    resolved_run = resolve_run_dir(str(run_dir), record_root)
    try:
        completed = subprocess.run(
            [
                sys.executable,
                str(selector_path),
                "candidates",
                "--run-dir",
                str(resolved_run),
            ],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise LabelingError(f"cannot execute article selector: {exc}") from exc
    if completed.returncode:
        detail = (completed.stderr or completed.stdout).strip()
        raise LabelingError(
            f"article selector failed for {resolved_run} (exit {completed.returncode}): {detail}"
        )
    payload = _parse_json_object(completed.stdout)
    articles = payload.get("articles")
    if not isinstance(articles, list):
        raise LabelingError("article selector JSON has no articles array")
    result: list[Path] = []
    seen: set[Path] = set()
    for index, item in enumerate(articles):
        if not isinstance(item, dict) or not isinstance(item.get("article_dir"), str):
            raise LabelingError(f"article selector returned invalid articles[{index}]")
        article_dir = resolve_article_dir(item["article_dir"], articles_root)
        if article_dir not in seen:
            seen.add(article_dir)
            result.append(article_dir)
    return result


def _read_json_object(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LabelingError(f"cannot read metadata JSON: {path}: {exc}") from exc
    return value if isinstance(value, dict) else {}


def read_article(article_dir: Path) -> ArticleInput:
    content_path = article_dir / "content.txt"
    try:
        content = content_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise LabelingError(f"cannot read article content: {content_path}: {exc}") from exc
    metadata: dict[str, Any] = {}
    for name in ("metadata.json", "data.json", "fallback_metadata.json"):
        source = _read_json_object(article_dir / name)
        for key in METADATA_KEYS:
            value = source.get(key)
            if value and (key not in metadata or not metadata[key]):
                metadata[key] = value
    return ArticleInput(article_dir=article_dir, metadata=metadata, content=content)


def _normalize_evidence(value: str) -> str:
    return re.sub(r"\s+", "", value)


def validate_model_label(payload: dict[str, Any], article: ArticleInput) -> list[str]:
    errors = validate_payload(payload)
    source = _normalize_evidence(article.evidence_text)
    for index, evidence in enumerate(payload.get("evidence", [])):
        if not isinstance(evidence, dict) or evidence.get("type") == "missing_evidence":
            continue
        text = evidence.get("text")
        if isinstance(text, str) and _normalize_evidence(text) not in source:
            errors.append(f"evidence[{index}].text is not present in the article")
    return errors


def write_label_atomic(article_dir: Path, payload: dict[str, Any]) -> Path:
    label_path = article_dir / "label.json"
    temporary_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=article_dir,
            prefix=".label.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_name = handle.name
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_name, 0o644)
        os.replace(temporary_name, label_path)
    finally:
        if temporary_name:
            Path(temporary_name).unlink(missing_ok=True)
    return label_path


async def label_one(
    article_dir: Path,
    model: LabelModel,
    system_prompt: str,
    max_retries: int,
) -> dict[str, Any]:
    try:
        article = read_article(article_dir)
    except LabelingError as exc:
        return {"article_dir": str(article_dir), "status": "failed", "error": str(exc)}

    article_prompt = build_article_prompt(article.article_dir, article.metadata, article.content)
    feedback = ""
    last_error = ""
    for attempt in range(max_retries + 1):
        try:
            payload = await model.label(system_prompt, article_prompt, feedback)
            errors = validate_model_label(payload, article)
            if not errors:
                path = write_label_atomic(article.article_dir, payload)
                return {
                    "article_dir": str(article.article_dir),
                    "status": "labeled",
                    "decision": payload["decision"],
                    "reason_code": payload["reason_code"],
                    "label_path": str(path),
                    "attempts": attempt + 1,
                }
            last_error = "; ".join(errors)
            feedback = last_error
        except Exception as exc:  # SDK exposes several transient exception subclasses.
            last_error = f"{type(exc).__name__}: {exc}"
            feedback = ""
        if attempt < max_retries:
            await asyncio.sleep(min(2**attempt, 8))
    return {
        "article_dir": str(article.article_dir),
        "status": "failed",
        "error": last_error,
        "attempts": max_retries + 1,
    }


async def run_labeling(
    article_dirs: list[Path],
    model: LabelModel,
    concurrency: int,
    max_retries: int,
    replace: bool = False,
) -> dict[str, Any]:
    decision_tree, research_profile = read_rules()
    system_prompt = build_system_prompt(decision_tree, research_profile)
    skipped: list[dict[str, str]] = []
    candidates: list[Path] = []
    for article_dir in article_dirs:
        label_path = article_dir / "label.json"
        if label_path.is_file() and not replace:
            label, errors = read_label(label_path)
            if label is not None and not errors:
                skipped.append({"article_dir": str(article_dir), "status": "skipped_valid"})
                continue
        candidates.append(article_dir)

    semaphore = asyncio.Semaphore(concurrency)

    async def bounded(article_dir: Path) -> dict[str, Any]:
        async with semaphore:
            return await label_one(article_dir, model, system_prompt, max_retries)

    tasks = [asyncio.create_task(bounded(path)) for path in candidates]
    results: list[dict[str, Any]] = []
    for completed, task in enumerate(asyncio.as_completed(tasks), start=1):
        item = await task
        results.append(item)
        LOGGER.info(
            "打标进度 [%d/%d] status=%s decision=%s article=%s",
            completed,
            len(tasks),
            item["status"],
            item.get("decision", ""),
            item["article_dir"],
        )
    results.extend(skipped)
    return {
        "candidates": len(candidates),
        "labeled": sum(item["status"] == "labeled" for item in results),
        "failed": sum(item["status"] == "failed" for item in results),
        "skipped_valid": sum(item["status"] == "skipped_valid" for item in results),
        "results": results,
    }
