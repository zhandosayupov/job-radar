"""Source health history and change detection.

Events (one per source per run at most):
  broke      was ok/partial last time, now blocked/error
  degraded   was ok, now partial
  empty      was ok with items, now ok but fetched 0 (silent breakage: layout/API change)
  recovered  was broke/empty, now ok with items
Runs where a source is `skipped` (budget) are ignored for comparison.
"""
from __future__ import annotations

from dataclasses import dataclass

HISTORY_LEN = 12
BAD = ("blocked", "error")


@dataclass
class Event:
    source: str
    kind: str
    status: str
    detail: str = ""


def _last_real(hist: list[dict]) -> dict | None:
    for h in reversed(hist):
        if h["status"] != "skipped":
            return h
    return None


def _is_broken(h: dict) -> bool:
    return h["status"] in BAD or (h["status"] == "ok" and h.get("fetched", 0) == 0 and h.get("was_empty_alert"))


def update(health: dict, results: dict[str, dict], now_iso: str) -> list[Event]:
    """Mutates `health` ({source: {"history": [...]}}) and returns change events.

    results: {source: {"status", "fetched", "detail"}}
    """
    events: list[Event] = []
    for name, r in results.items():
        entry = health.setdefault(name, {"history": []})
        hist = entry["history"]
        prev = _last_real(hist)
        cur = {"t": now_iso, "status": r["status"], "fetched": int(r.get("fetched", 0))}
        detail = r.get("detail") or ""
        if r["status"] != "skipped" and prev is not None:
            p, c = prev["status"], cur["status"]
            if c in BAD and p not in BAD:
                events.append(Event(name, "broke", c, detail))
            elif c == "partial" and p == "ok":
                events.append(Event(name, "degraded", c, detail))
            elif c == "ok" and cur["fetched"] == 0 and p == "ok" and prev.get("fetched", 0) > 0:
                cur["was_empty_alert"] = True
                events.append(Event(name, "empty", c, f"fetched 0 (was {prev['fetched']})"))
            elif c == "ok" and cur["fetched"] == 0 and prev.get("was_empty_alert"):
                cur["was_empty_alert"] = True  # still empty: keep state, no repeat alert
            elif c == "ok" and cur["fetched"] > 0 and _is_broken(prev):
                events.append(Event(name, "recovered", c, f"fetched {cur['fetched']}"))
        hist.append(cur)
        del hist[:-HISTORY_LEN]
    return events


def consecutive_bad(health: dict, name: str) -> int:
    n = 0
    for h in reversed(health.get(name, {}).get("history", [])):
        if h["status"] == "skipped":
            continue
        if h["status"] in BAD:
            n += 1
        else:
            break
    return n
