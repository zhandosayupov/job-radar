"""Inbox shaping: order newest first, cap by item count and estimated tokens."""
from __future__ import annotations

import json
from datetime import datetime, timedelta


def est_tokens(item: dict) -> int:
    return len(json.dumps(item, ensure_ascii=False, separators=(",", ":"))) // 4 + 1


def sort_key(item: dict) -> str:
    return item.get("posted_at") or item.get("first_seen") or ""


def expire(items: list[dict], now: datetime, max_age_days: int) -> tuple[list[dict], int]:
    cutoff = (now - timedelta(days=max_age_days)).isoformat()
    kept = [i for i in items if (i.get("first_seen") or "") >= cutoff]
    return kept, len(items) - len(kept)


def split_inbox(items: list[dict], max_items: int, max_tokens: int) -> tuple[list[dict], list[dict], int]:
    """Returns (inbox, overflow, inbox_tokens)."""
    ordered = sorted(items, key=sort_key, reverse=True)
    inbox: list[dict] = []
    tokens = 0
    cut = len(ordered)
    for i, item in enumerate(ordered):
        t = est_tokens(item)
        if len(inbox) >= max_items or tokens + t > max_tokens:
            cut = i
            break
        inbox.append(item)
        tokens += t
    return inbox, ordered[cut:], tokens
