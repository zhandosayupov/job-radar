"""GitHub Actions minutes self-accounting (private repos share 2000 min/month on Free).

Each run appends {started, finished, minutes} to data/usage/<YYYY-MM>.json, where minutes
is the job's wall time rounded up (GitHub bills each job rounded up to a whole minute).
The job start comes from RADAR_JOB_STARTED (set by the workflow's first step) so checkout
and pip install are counted too.
"""
from __future__ import annotations

import math
import os
from datetime import datetime, timezone

from .sources.base import iso_utc
from .state import DataStore


def month_key(now: datetime) -> str:
    return f"usage/{now:%Y-%m}.json"


def load_usage(store: DataStore, now: datetime) -> dict:
    return store.load(month_key(now), {"runs": [], "total_minutes": 0})


def decide_mode(total_minutes: int, budget: dict) -> str:
    """full | tier1 | stop"""
    if total_minutes > int(budget.get("month_hard_limit", 1700)):
        return "stop"
    if total_minutes > int(budget.get("month_soft_limit", 1400)):
        return "tier1"
    return "full"


def job_started(default: datetime) -> datetime:
    raw = os.environ.get("RADAR_JOB_STARTED")
    if raw:
        iso = iso_utc(int(raw) if raw.isdigit() else raw)
        if iso:
            return datetime.fromisoformat(iso)
    return default


def record_run(store: DataStore, started: datetime, finished: datetime, mode: str) -> dict:
    usage = load_usage(store, started)
    minutes = max(1, math.ceil((finished - started).total_seconds() / 60))
    usage["runs"].append(
        {
            "started": started.astimezone(timezone.utc).replace(microsecond=0).isoformat(),
            "finished": finished.astimezone(timezone.utc).replace(microsecond=0).isoformat(),
            "minutes_rounded_up": minutes,
            "mode": mode,
        }
    )
    usage["total_minutes"] = sum(r["minutes_rounded_up"] for r in usage["runs"])
    store.save(month_key(started), usage)
    return usage
