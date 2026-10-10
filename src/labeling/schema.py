from __future__ import annotations

import json
import re
import yaml
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DECISION_TREE_PATH = (
    REPO_ROOT / "skill" / "label-wechat-articles" / "references" / "decision-tree.md"
)
SCHEMA_VERSION = 2
SUMMARY_MAX_CHARS = 500
DECISIONS = ("KEEP", "DROP", "REVIEW")
APPLICATION_TYPES = ("科研项目申请", "科研指南申请", "都不是")
POSITIVE_APPLICATION_TYPES = ("科研项目申请", "科研指南申请")
DOMAINS = ("无人机", "具身智能", "大模型", "空天", "机器人", "机械臂")
EVIDENCE_TYPES = ("solicitation", "research_task", "domain", "negative", "missing_evidence")
REQUIRED_KEYS = {
    "schema_version",
    "tree_version",
    "profile_version",
    "geography",
    "decision",
    "decision_path",
    "reason_code",
    "reason",
    "evidence",
    "application_type",
    "domains",
}
EXPECTED_KEYS = REQUIRED_KEYS | {"summary", "deadline", "importance", "project_intake"}

TREE_VERSION_RE = re.compile(r"^Tree version: `([^`]+)`\s*$", re.MULTILINE)
NODE_RE = re.compile(r"^## \[([A-Z][A-Z0-9-]*)\]\s+.+$", re.MULTILINE)
DECISION_RE = re.compile(r"^- Decision: `(KEEP|DROP|REVIEW)`\s*$", re.MULTILINE)
PATH_STEP_RE = re.compile(r"^([A-Z][A-Z0-9-]*)(?::([A-Z][A-Z0-9-]*|PASS))?$")


class LabelSchemaError(ValueError):
    """Raised when the decision-tree contract itself cannot be loaded."""


def load_tree_spec(path: str | Path = DECISION_TREE_PATH) -> dict[str, Any]:
    tree_path = Path(path)
    try:
        text = tree_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise LabelSchemaError(f"cannot read decision tree {tree_path}: {exc}") from exc

    version_match = TREE_VERSION_RE.search(text)
    if not version_match:
        raise LabelSchemaError(f"Tree version is missing from {tree_path}")

    headings = list(NODE_RE.finditer(text))
    if not headings:
        raise LabelSchemaError(f"no decision-tree nodes found in {tree_path}")

    nodes: set[str] = set()
    terminals: dict[str, str] = {}
    for index, heading in enumerate(headings):
        code = heading.group(1)
        if code in nodes:
            raise LabelSchemaError(f"duplicate decision-tree node: {code}")
        nodes.add(code)
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        section = text[heading.end() : end]
        decisions = DECISION_RE.findall(section)
        if len(decisions) > 1:
            raise LabelSchemaError(f"node {code} has multiple terminal decisions")
        if decisions:
            terminals[code] = decisions[0]

    if not terminals:
        raise LabelSchemaError(f"no terminal nodes found in {tree_path}")
    return {
        "version": version_match.group(1),
        "nodes": frozenset(nodes),
        "terminals": terminals,
        "path": str(tree_path),
    }


def load_profile_version() -> str:
    path = DECISION_TREE_PATH.parent / "research-profile.yaml"
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    version = value.get("profile_version") if isinstance(value, dict) else None
    if not isinstance(version, str) or not version.strip():
        raise LabelSchemaError("research-profile.yaml must declare profile_version")
    return version


def current_contract() -> dict[str, Any]:
    return {"label_schema_version": SCHEMA_VERSION,
            "tree_version": load_tree_spec()["version"],
            "profile_version": load_profile_version()}


def _validate_path(value: Any, spec: dict[str, Any], reason_code: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(value, list) or not value:
        return ["decision_path must be a non-empty JSON array"]
    for index, step in enumerate(value):
        if not isinstance(step, str) or not step:
            errors.append(f"decision_path[{index}] must be a non-empty string")
            continue
        match = PATH_STEP_RE.fullmatch(step)
        if not match:
            errors.append(f"decision_path[{index}] has invalid syntax: {step}")
            continue
        node, outcome = match.groups()
        if node not in spec["nodes"]:
            errors.append(f"decision_path[{index}] references unknown node: {node}")
        if outcome and outcome != "PASS" and outcome not in spec["nodes"]:
            errors.append(f"decision_path[{index}] references unknown outcome: {outcome}")
    if isinstance(reason_code, str):
        last = value[-1] if value and isinstance(value[-1], str) else ""
        if last != reason_code and not last.endswith(f":{reason_code}"):
            errors.append("decision_path must end at reason_code")
    return errors


def validate_payload(
    payload: Any, *, require_summary: bool = False, require_deadline: bool = False,
    require_importance: bool = False,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["root value must be a JSON object"]

    actual_keys = set(payload)
    if not REQUIRED_KEYS.issubset(actual_keys) or not actual_keys.issubset(EXPECTED_KEYS):
        errors.append(
            "keys must contain the v2 fields and optional summary only: "
            + ", ".join(sorted(EXPECTED_KEYS))
        )
    if require_summary and "summary" not in payload:
        errors.append("summary is required for newly generated v2 labels")
    if require_deadline and "deadline" not in payload:
        errors.append("deadline is required for newly generated v2 labels")
    if require_importance and "importance" not in payload:
        errors.append("importance is required for newly generated v2 labels")

    spec = load_tree_spec()
    if payload.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}; legacy labels must be relabeled")
    if payload.get("tree_version") != spec["version"]:
        errors.append(f"tree_version must be {spec['version']}")

    if payload.get("profile_version") != load_profile_version():
        errors.append(f"profile_version must be {load_profile_version()}; relabel unverified labels")

    geography = payload.get("geography")
    if not isinstance(geography, dict) or set(geography) != {"status", "regions", "evidence"}:
        errors.append("geography must contain exactly status, regions, evidence")
    else:
        status, regions, evidence = geography.get("status"), geography.get("regions"), geography.get("evidence")
        if status not in {"eligible", "out_of_scope", "unclear"}:
            errors.append("geography.status is not allowed")
        allowed = {"national", "beijing", "zhejiang"}
        if not isinstance(regions, list) or not regions or any(not isinstance(v, str) or v not in allowed | {"other", "unknown"} for v in regions):
            errors.append("geography.regions must contain allowed region codes")
        elif status == "eligible" and (not allowed.intersection(regions) or "unknown" in regions):
            errors.append("eligible geography needs national, beijing or zhejiang evidence")
        if not isinstance(evidence, str) or (status != "unclear" and not evidence.strip()):
            errors.append("geography.evidence must quote source text for a confirmed scope")
        if payload.get("decision") == "KEEP" and status != "eligible":
            errors.append("KEEP requires eligible geography")
        if payload.get("reason_code") == "G1-D1" and status != "out_of_scope":
            errors.append("G1-D1 requires out_of_scope geography")
        if payload.get("reason_code") == "G1-R1" and status != "unclear":
            errors.append("G1-R1 requires unclear geography")

    decision = payload.get("decision")
    if decision not in DECISIONS:
        errors.append("decision is not an allowed value")

    reason_code = payload.get("reason_code")
    if not isinstance(reason_code, str) or reason_code not in spec["terminals"]:
        errors.append("reason_code is not a terminal node in decision-tree.md")
    elif decision in DECISIONS and spec["terminals"][reason_code] != decision:
        errors.append(
            f"reason_code {reason_code} belongs to {spec['terminals'][reason_code]}, not {decision}"
        )
    errors.extend(_validate_path(payload.get("decision_path"), spec, reason_code))

    reason = payload.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        errors.append("reason must be a non-empty article-specific explanation")

    summary = payload.get("summary")
    if "summary" in payload:
        if not isinstance(summary, str) or not summary.strip():
            errors.append("summary must be a non-empty factual article summary")
        elif len(summary.strip()) > SUMMARY_MAX_CHARS:
            errors.append(f"summary must be at most {SUMMARY_MAX_CHARS} characters")

    deadline = payload.get("deadline")
    if "deadline" in payload:
        if not isinstance(deadline, dict) or set(deadline) != {
            "status", "raw_text", "timestamp", "timezone"
        }:
            errors.append("deadline must contain exactly status, raw_text, timestamp, and timezone")
        else:
            status = deadline.get("status")
            raw_text = deadline.get("raw_text")
            timestamp = deadline.get("timestamp")
            if status not in {"confirmed", "ambiguous", "missing"}:
                errors.append("deadline.status is not allowed")
            if deadline.get("timezone") != "Asia/Shanghai":
                errors.append("deadline.timezone must be Asia/Shanghai")
            if not isinstance(raw_text, str):
                errors.append("deadline.raw_text must be a string")
            if status == "confirmed":
                if not isinstance(timestamp, int) or isinstance(timestamp, bool) or timestamp <= 0:
                    errors.append("confirmed deadline requires a positive Unix timestamp")
                if not isinstance(raw_text, str) or not raw_text.strip():
                    errors.append("confirmed deadline requires its verbatim source text")
            elif status == "ambiguous":
                if timestamp is not None:
                    errors.append("ambiguous deadline timestamp must be null")
                if not isinstance(raw_text, str) or not raw_text.strip():
                    errors.append("ambiguous deadline requires its verbatim source text")
            elif status == "missing" and timestamp is not None:
                errors.append("missing deadline timestamp must be null")

    evidence = payload.get("evidence")
    evidence_types: set[str] = set()
    if not isinstance(evidence, list) or not evidence:
        errors.append("evidence must be a non-empty JSON array")
    else:
        for index, item in enumerate(evidence):
            if not isinstance(item, dict) or set(item) != {"type", "text", "location"}:
                errors.append(f"evidence[{index}] must contain exactly type, text, and location")
                continue
            evidence_type = item.get("type")
            if evidence_type not in EVIDENCE_TYPES:
                errors.append(f"evidence[{index}].type is not allowed")
            else:
                evidence_types.add(evidence_type)
            for key in ("text", "location"):
                if not isinstance(item.get(key), str) or not item[key].strip():
                    errors.append(f"evidence[{index}].{key} must be a non-empty string")

    application_type = payload.get("application_type")
    if application_type not in APPLICATION_TYPES:
        errors.append("application_type is not an allowed value")

    domains = payload.get("domains")
    if not isinstance(domains, list):
        errors.append("domains must be a JSON array")
    else:
        unknown = [value for value in domains if value not in DOMAINS]
        if unknown:
            errors.append("domains contains unknown values: " + ", ".join(map(str, unknown)))
        if len(domains) != len(set(map(str, domains))):
            errors.append("domains contains duplicate values")
        canonical = [domain for domain in DOMAINS if domain in domains]
        if domains != canonical:
            errors.append("domains is not in canonical order")

    if decision == "KEEP":
        if "G1:PASS" not in (payload.get("decision_path") or []):
            errors.append("KEEP requires passing the G1 geography gate")
        if application_type not in POSITIVE_APPLICATION_TYPES:
            errors.append("KEEP requires a research project or guide application_type")
        if not isinstance(domains, list) or not domains:
            errors.append("KEEP requires at least one task-scope domain")
        if "solicitation" not in evidence_types or "domain" not in evidence_types:
            errors.append("KEEP evidence must include solicitation and domain entries")

    importance = payload.get("importance")
    if "importance" in payload:
        factor_keys = {
            "deadline_urgency", "project_significance", "amount_level",
            "amount_raw_text", "domain_fit",
        }
        if not isinstance(importance, dict) or set(importance) != {"level", "reason", "factors"}:
            errors.append("importance must contain exactly level, reason, and factors")
        else:
            level = importance.get("level")
            if level not in {"high", "medium", "low", "not_applicable"}:
                errors.append("importance.level is not allowed")
            if decision == "KEEP" and level not in {"high", "medium", "low"}:
                errors.append("KEEP requires a high, medium, or low importance level")
            if decision != "KEEP" and level != "not_applicable":
                errors.append("DROP and REVIEW require importance.level=not_applicable")
            if not isinstance(importance.get("reason"), str) or not importance["reason"].strip():
                errors.append("importance.reason must be a non-empty explanation")
            factors = importance.get("factors")
            if not isinstance(factors, dict) or set(factors) != factor_keys:
                errors.append("importance.factors has invalid keys")
            else:
                allowed = {
                    "deadline_urgency": {"high", "medium", "low", "expired", "unknown"},
                    "project_significance": {"high", "medium", "low", "unknown"},
                    "amount_level": {"high", "medium", "low", "unknown"},
                    "domain_fit": {"strong", "medium", "weak", "none"},
                }
                for key, values in allowed.items():
                    if factors.get(key) not in values:
                        errors.append(f"importance.factors.{key} is not allowed")
                if not isinstance(factors.get("amount_raw_text"), str):
                    errors.append("importance.factors.amount_raw_text must be a string")

    return errors


def read_label(path: Path) -> tuple[dict[str, Any] | None, list[str]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, [f"cannot read valid JSON: {exc}"]
    errors = validate_payload(payload)
    return (payload if not errors else None), errors
