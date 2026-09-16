"""Time-sensitive eligibility, evaluated at selection/send time, not model time."""
import time
from typing import Any


def deadline_expired(label: dict[str, Any], *, now: float | None = None) -> bool:
    deadline = label.get("deadline") or {}
    timestamp = deadline.get("timestamp")
    return (
        deadline.get("status") == "confirmed"
        and isinstance(timestamp, (int, float))
        and not isinstance(timestamp, bool)
        and timestamp > 0
        and timestamp <= (time.time() if now is None else now)
    )


def geography_eligible(label: dict[str, Any]) -> bool:
    """Unclassified legacy records cannot enter the regional opportunity feed."""
    geography = label.get("geography") or {}
    if not isinstance(geography, dict):
        return False
    regions = geography.get("regions")
    return (geography.get("status") == "eligible"
            and isinstance(regions, list)
            and any(region in {"national", "beijing", "zhejiang"} for region in regions)
            and "unknown" not in regions
            and isinstance(geography.get("evidence"), str)
            and bool(geography["evidence"].strip()))
