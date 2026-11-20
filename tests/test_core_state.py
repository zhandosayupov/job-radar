"""dedupe, inbox shaping, state files, budget accounting."""
from datetime import date, datetime, timedelta, timezone

import pytest

from radar import budget
from radar.dedupe import KIND_RANK, merge
from radar.inbox import est_tokens, expire, split_inbox
from radar.state import DataStore, read_jsonl
from tests.conftest import make_job

RANK = {"greenhouse": KIND_RANK["ats"], "wwr": KIND_RANK["aggregator"], "hn": KIND_RANK["community"]}


def item(source, sid, company="Acme", title="Software Engineer", loc="London", posted=None):
    return make_job(source=source, source_job_id=sid, company=company, title=title, location_text=loc,
                    posted_at=posted).inbox_dict()


# --- dedupe ---------------------------------------------------------------------------


def test_ats_replaces_aggregator_duplicate():
    merged, dupes = merge([item("wwr", "a")], [item("greenhouse", "1")], RANK.get)
    assert dupes == 1 and [m["id"] for m in merged] == ["greenhouse:1"]


def test_aggregator_does_not_replace_ats():
    merged, dupes = merge([item("greenhouse", "1")], [item("hn", "9")], RANK.get)
    assert dupes == 1 and [m["id"] for m in merged] == ["greenhouse:1"]


def test_duplicates_within_one_batch():
    merged, dupes = merge([], [item("hn", "1"), item("greenhouse", "2"), item("wwr", "3")], RANK.get)
    assert dupes == 2 and [m["id"] for m in merged] == ["greenhouse:2"]


def test_different_city_is_not_duplicate():
    merged, dupes = merge([item("greenhouse", "1", loc="London")], [item("greenhouse", "2", loc="Amsterdam")], RANK.get)
    assert dupes == 0 and len(merged) == 2


def test_empty_company_never_deduped():
    a = item("hn", "1", company="")
    b = item("hn", "2", company="")
    merged, dupes = merge([a], [b], RANK.get)
    assert dupes == 0 and len(merged) == 2


# --- inbox ------------------------------------------------------------------------------


def test_split_inbox_newest_first_and_item_cap():
    items = [item("greenhouse", str(i), title=f"Engineer {i}", posted=f"2026-11-{i + 1:02d}T00:00:00+00:00") for i in range(10)]
    inbox, overflow, tokens = split_inbox(items, max_items=3, max_tokens=10**6)
    assert [i["id"] for i in inbox] == ["greenhouse:9", "greenhouse:8", "greenhouse:7"]
    assert len(overflow) == 7 and tokens == sum(est_tokens(i) for i in inbox)


def test_split_inbox_token_cap():
    items = [item("greenhouse", str(i), title=f"Engineer {i}") for i in range(10)]
    per = est_tokens(items[0])
    inbox, overflow, _ = split_inbox(items, max_items=100, max_tokens=per * 4 + 1)
    assert len(inbox) == 4 and len(overflow) == 6


def test_expire_old_pending():
    now = datetime(2026, 11, 20, tzinfo=timezone.utc)
    fresh = item("greenhouse", "1")
    old = dict(item("greenhouse", "2"), first_seen=(now - timedelta(days=60)).isoformat())
    kept, n = expire([fresh, old], now, 45)
    assert n == 1 and kept == [fresh]


# --- state ------------------------------------------------------------------------------


def test_seen_spans_months(tmp_path):
    s = DataStore(tmp_path)
    s.append_seen(["a", "b"], date(2026, 10, 31))
    s.append_seen(["c"], date(2026, 11, 1))
    s.append_seen(["old"], date(2026, 1, 1))
    assert s.load_seen(date(2026, 11, 20), months=2) == {"a", "b", "c"}


def test_pending_roundtrip_unicode_and_lf(tmp_path):
    s = DataStore(tmp_path)
    a = item("hn", "1", title="Разработчик C++")
    s.save_pending([a], [item("hn", "2")])
    assert [p["id"] for p in s.load_pending()] == ["hn:1", "hn:2"]
    raw = s.pending_path.read_bytes()
    assert b"\r\n" not in raw and "Разработчик".encode() in raw


def test_labeled_ids_from_all_label_files(tmp_path):
    s = DataStore(tmp_path)
    (tmp_path / "labels").mkdir()
    (tmp_path / "labels" / "2026-11-19.jsonl").write_text('{"id":"a","score":1}\n', encoding="utf-8")
    (tmp_path / "labels" / "2026-11-20.jsonl").write_text('{"id":"b"}\n\n{"noid":1}\n', encoding="utf-8")
    assert s.labeled_ids() == {"a", "b"}


def test_closed_append(tmp_path):
    s = DataStore(tmp_path)
    s.append_closed({"x", "y"}, datetime(2026, 11, 20, 1, 2, 3, 999, tzinfo=timezone.utc))
    rows = read_jsonl(tmp_path / "closed.jsonl")
    assert rows == [{"id": "x", "closed_at": "2026-11-20T01:02:03+00:00"}, {"id": "y", "closed_at": "2026-11-20T01:02:03+00:00"}]


# --- budget -----------------------------------------------------------------------------


@pytest.mark.parametrize("total,mode", [(0, "full"), (1400, "full"), (1401, "tier1"), (1700, "tier1"), (1701, "stop")])
def test_decide_mode(total, mode):
    assert budget.decide_mode(total, {"month_soft_limit": 1400, "month_hard_limit": 1700}) == mode


def test_record_run_rounds_up_and_sums(tmp_path):
    s = DataStore(tmp_path)
    t0 = datetime(2026, 11, 20, 0, 17, tzinfo=timezone.utc)
    budget.record_run(s, t0, t0 + timedelta(seconds=61), "full")
    u = budget.record_run(s, t0, t0 + timedelta(seconds=5), "full")
    assert [r["minutes_rounded_up"] for r in u["runs"]] == [2, 1]
    assert u["total_minutes"] == 3
    assert (tmp_path / "usage" / "2026-11.json").exists()


def test_job_started_from_env(monkeypatch):
    default = datetime(2026, 11, 20, tzinfo=timezone.utc)
    monkeypatch.setenv("RADAR_JOB_STARTED", "1763600000")
    assert budget.job_started(default) == datetime.fromtimestamp(1763600000, tz=timezone.utc)
    monkeypatch.setenv("RADAR_JOB_STARTED", "garbage")
    assert budget.job_started(default) == default
    monkeypatch.delenv("RADAR_JOB_STARTED")
    assert budget.job_started(default) == default
