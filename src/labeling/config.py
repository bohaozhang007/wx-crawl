from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config.yaml"


class LabelingConfigError(ValueError):
    pass


@dataclass(frozen=True)
class LabelingConfig:
    api_key: str
    api_key_source: str
    provider: str
    model: str
    base_url: str
    api_style: str
    source: str
    concurrency: int
    timeout_seconds: float
    max_retries: int


def _positive_int(value: Any, name: str, *, allow_zero: bool = False) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise LabelingConfigError(f"labeling.{name} must be an integer") from exc
    minimum = 0 if allow_zero else 1
    if parsed < minimum:
        raise LabelingConfigError(f"labeling.{name} must be at least {minimum}")
    return parsed


def _positive_float(value: Any, name: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise LabelingConfigError(f"labeling.{name} must be a number") from exc
    if parsed <= 0:
        raise LabelingConfigError(f"labeling.{name} must be greater than 0")
    return parsed


def _read_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return values
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.removeprefix("export ").strip()
        if key:
            values[key] = value.strip().strip('"').strip("'")
    return values


def _read_hermes_model(hermes_home: Path) -> dict[str, str]:
    try:
        payload = yaml.safe_load((hermes_home / "config.yaml").read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return {}
    model = payload.get("model")
    if not isinstance(model, dict):
        return {}
    return {
        "provider": str(model.get("provider") or "").strip(),
        "model": str(model.get("default") or "").strip(),
        "base_url": str(model.get("base_url") or "").strip(),
    }


def _infer_provider(base_url: str) -> str:
    hostname = (urlparse(base_url).hostname or "").lower()
    if hostname.endswith("openai.com"):
        return "openai"
    if hostname.endswith("deepseek.com"):
        return "deepseek"
    return "openai-compatible"


def _provider_key_name(provider: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", provider).strip("_").upper()
    return f"{normalized}_API_KEY" if normalized else "OPENAI_API_KEY"


def _api_style(provider: str, base_url: str) -> str:
    hostname = (urlparse(base_url).hostname or "").lower()
    if provider.lower() == "openai" and hostname.endswith("openai.com"):
        return "responses_parse"
    return "chat_completions_json"


def load_labeling_config(
    path: Path = DEFAULT_CONFIG_PATH,
    *,
    hermes_home: Path | None = None,
) -> LabelingConfig:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise LabelingConfigError(f"cannot read config: {path}: {exc}") from exc
    labeling = payload.get("labeling")
    if not isinstance(labeling, dict):
        raise LabelingConfigError("config.yaml must contain a labeling mapping")

    resolved_hermes_home = hermes_home or Path(
        os.getenv("HERMES_HOME") or (Path.home() / ".hermes")
    )
    hermes = _read_hermes_model(resolved_hermes_home)
    file_env = _read_dotenv(resolved_hermes_home / ".env")
    runtime_env = {**file_env, **os.environ}

    explicit_provider = str(
        runtime_env.get("LABEL_PROVIDER") or labeling.get("provider") or ""
    ).strip()
    explicit_model = str(runtime_env.get("LABEL_MODEL") or labeling.get("model") or "").strip()
    explicit_base_url = str(
        runtime_env.get("LABEL_BASE_URL") or labeling.get("base_url") or ""
    ).strip()

    model = explicit_model or hermes.get("model", "")
    if not model:
        raise LabelingConfigError(
            "Hermes has no default model; set labeling.model or export LABEL_MODEL"
        )
    base_url = explicit_base_url or hermes.get("base_url", "") or "https://api.openai.com/v1"
    provider = (
        explicit_provider
        or (_infer_provider(explicit_base_url) if explicit_base_url else "")
        or hermes.get("provider", "")
        or _infer_provider(base_url)
    )

    provider_key_name = _provider_key_name(provider)
    key_candidates = (
        ("LABEL_API_KEY", runtime_env.get("LABEL_API_KEY")),
        (provider_key_name, runtime_env.get(provider_key_name)),
        ("OPENAI_API_KEY", runtime_env.get("OPENAI_API_KEY")),
    )
    api_key_source = ""
    api_key = ""
    for key_name, value in key_candidates:
        candidate = str(value or "").strip()
        if candidate:
            api_key_source = key_name
            api_key = candidate
            break
    if not api_key:
        raise LabelingConfigError(
            f"no API key for provider {provider}; set {provider_key_name} in the Hermes environment "
            "or export LABEL_API_KEY"
        )

    concurrency = _positive_int(
        os.getenv("LABEL_CONCURRENCY") or labeling.get("concurrency", 2), "concurrency"
    )
    timeout_seconds = _positive_float(
        os.getenv("LABEL_TIMEOUT_SECONDS") or labeling.get("timeout_seconds", 180),
        "timeout_seconds",
    )
    max_retries = _positive_int(
        os.getenv("LABEL_MAX_RETRIES") or labeling.get("max_retries", 2),
        "max_retries",
        allow_zero=True,
    )
    return LabelingConfig(
        api_key=api_key,
        api_key_source=api_key_source,
        provider=provider,
        model=model,
        base_url=base_url,
        api_style=_api_style(provider, base_url),
        source="label_override" if any((explicit_provider, explicit_model, explicit_base_url)) else "hermes",
        concurrency=concurrency,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
    )
