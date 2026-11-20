"""Git-friendly state under the deployment's data/ dir (JSON / JSONL, no binary DB).

data/seen/<YYYY-MM>.jsonl      {"id","d"} for every fetched id (kept or dropped)
data/inbox/pending.jsonl       capped view the labeler reads
data/inbox/overflow.jsonl      prefiltered but over the cap (still pending)
data/inbox/manifest.json
data/labels/<YYYY-MM-DD>.jsonl written by the labeler; ids found here leave pending
data/closed.jsonl              {"id","closed_at"} for complete boards
data/boards.json               last full id set per complete board (for closures)
data/health.json               per-source status history (for alerts / issue)
data/cursors.json              per-source cursors (e.g. last Telegram post id)
data/usage/<YYYY-MM>.json      Actions minutes accounting
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Iterable


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: Iterable[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n" for r in rows)
    path.write_text(text, encoding="utf-8", newline="\n")


def append_jsonl(path: Path, rows: Iterable[dict]) -> None:
    rows = list(rows)
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")


def read_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def _months_back(today: date, n: int) -> list[str]:
    y, m = today.year, today.month
    out = []
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return out


class DataStore:
    def __init__(self, root: Path):
        self.root = Path(root)

    # paths
    @property
    def pending_path(self) -> Path:
        return self.root / "inbox" / "pending.jsonl"

    @property
    def overflow_path(self) -> Path:
        return self.root / "inbox" / "overflow.jsonl"

    @property
    def manifest_path(self) -> Path:
        return self.root / "inbox" / "manifest.json"

    # seen
    def load_seen(self, today: date, months: int) -> set[str]:
        ids: set[str] = set()
        for ym in _months_back(today, months):
            ids.update(r["id"] for r in read_jsonl(self.root / "seen" / f"{ym}.jsonl"))
        return ids

    def append_seen(self, ids: Iterable[str], today: date) -> None:
        d = today.isoformat()
        append_jsonl(self.root / "seen" / f"{today:%Y-%m}.jsonl", ({"id": i, "d": d} for i in sorted(ids)))

    # pending
    def load_pending(self) -> list[dict]:
        return read_jsonl(self.pending_path) + read_jsonl(self.overflow_path)

    def save_pending(self, inbox: list[dict], overflow: list[dict]) -> None:
        write_jsonl(self.pending_path, inbox)
        write_jsonl(self.overflow_path, overflow)

    # labels
    def labeled_ids(self) -> set[str]:
        ids: set[str] = set()
        d = self.root / "labels"
        if d.exists():
            for p in sorted(d.glob("*.jsonl")):
                ids.update(str(r["id"]) for r in read_jsonl(p) if "id" in r)
        return ids

    # misc json
    def load(self, name: str, default):
        return read_json(self.root / name, default)

    def save(self, name: str, obj) -> None:
        write_json(self.root / name, obj)

    def append_closed(self, ids: Iterable[str], when: datetime) -> None:
        ts = when.replace(microsecond=0).isoformat()
        append_jsonl(self.root / "closed.jsonl", ({"id": i, "closed_at": ts} for i in sorted(ids)))
