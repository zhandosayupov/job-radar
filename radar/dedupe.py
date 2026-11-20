"""Cross-source dedupe by fingerprint (company|title|city). ATS records beat aggregators."""
from __future__ import annotations

from typing import Callable

KIND_RANK = {"ats": 0, "aggregator": 1, "community": 2}


def _usable(fp: str) -> bool:
    company, title, _ = (fp.split("|") + ["", "", ""])[:3]
    return bool(company and title)


def merge(
    pending: list[dict],
    new_items: list[dict],
    rank_of: Callable[[str], int],
) -> tuple[list[dict], int]:
    """Merge new items into pending. Returns (merged, duplicates_dropped).

    On a fingerprint clash the better-ranked source (lower rank) wins; ties keep the
    existing item so ids already in the inbox stay stable.
    """
    merged = list(pending)
    index: dict[str, int] = {}
    for i, item in enumerate(merged):
        fp = item.get("fingerprint", "")
        if _usable(fp):
            index.setdefault(fp, i)
    dupes = 0
    for item in new_items:
        fp = item.get("fingerprint", "")
        if not _usable(fp) or fp not in index:
            if _usable(fp):
                index[fp] = len(merged)
            merged.append(item)
            continue
        dupes += 1
        i = index[fp]
        if rank_of(item["source"]) < rank_of(merged[i]["source"]):
            merged[i] = item
    return merged, dupes
