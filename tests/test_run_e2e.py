"""End-to-end pipeline on recorded fixtures (no network). Covers the Phase-0/3 contracts:
inbox + manifest produced, second run idempotent, labels remove ids from pending,
closures recorded, failures isolated and alerted, budget guard."""
import json
from datetime import datetime, timezone

import httpx
import yaml

from radar.run import main as run_main
from radar.run import run
from radar.state import read_jsonl
from tests.conftest import default_router, fixture_json, make_http

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)


class FakeAlerter:
    def __init__(self):
        self.sent = []
        self.last_error = None

    def send(self, text):
        self.sent.append(text)
        return True


def go(cfg, data, router=None, now=NOW, alerter=None):
    return run(cfg, data, http=make_http(router or default_router()), now=now, alerter=alerter or FakeAlerter(), log=lambda *_: None)


def write_cfg(tmp_path, settings=None, companies=None):
    cfg = tmp_path / "config"
    cfg.mkdir(exist_ok=True)
    if settings:
        (cfg / "settings.yaml").write_text(yaml.safe_dump(settings), encoding="utf-8")
    if companies is not None:
        (cfg / "companies.yaml").write_text(yaml.safe_dump({"companies": companies}), encoding="utf-8")
    return cfg


def test_first_run_produces_inbox_and_manifest(tmp_path):
    cfg, data = write_cfg(tmp_path), tmp_path / "data"
    rep = go(cfg, data)
    m = json.loads((data / "inbox" / "manifest.json").read_text(encoding="utf-8"))
    assert set(m["sources"]) == {"greenhouse", "hn", "wwr"}
    assert all(s["status"] == "ok" for s in m["sources"].values())
    for key in ("generated_at", "pending_count", "overflow", "month_minutes_used", "dropped_by_rule"):
        assert key in m
    pending = read_jsonl(data / "inbox" / "pending.jsonl")
    assert len(pending) == m["pending_count"] == rep.pending_count > 0
    assert all("description" not in p for p in pending)
    assert all(list(p)[-1] == "text" for p in pending)  # full text is the last key
    gh = [p for p in pending if p["source"] == "greenhouse"]
    assert gh and all(p["text"] and len(p["text"]) > len(p["excerpt"] or "") for p in gh)
    assert m["inbox_tokens_est"] <= 90000
    assert all(p["url"].startswith("https://") for p in pending)
    assert m["this_run_minutes"] >= 1 and (data / "usage" / "2026-11.json").exists()
    assert (data / "inbox" / "dropped_last.jsonl").exists()
    # every fetched id is now seen, kept or not
    seen = read_jsonl(data / "seen" / "2026-11.jsonl")
    assert len(seen) == m["new_total"]


def test_second_run_is_idempotent(tmp_path):
    cfg, data = write_cfg(tmp_path), tmp_path / "data"
    first = go(cfg, data)
    second = go(cfg, data)
    assert second.new_total == 0
    assert second.pending_count == first.pending_count


def test_labels_remove_ids_from_pending(tmp_path):
    cfg, data = write_cfg(tmp_path), tmp_path / "data"
    go(cfg, data)
    pending = read_jsonl(data / "inbox" / "pending.jsonl")
    labeled = [pending[0]["id"], pending[1]["id"]]
    (data / "labels").mkdir()
    (data / "labels" / "2026-11-20.jsonl").write_text(
        "".join(json.dumps({"id": i, "score": 50, "action": "maybe"}) + "\n" for i in labeled), encoding="utf-8"
    )
    rep = go(cfg, data)
    after = {p["id"] for p in read_jsonl(data / "inbox" / "pending.jsonl")}
    assert not (after & set(labeled))
    assert rep.manifest["removed_labeled"] == 2


def test_closures_for_complete_boards(tmp_path):
    cfg = write_cfg(tmp_path, companies=[{"name": "IMC", "ats": "greenhouse", "token": "imc", "complete": True}])
    data = tmp_path / "data"
    go(cfg, data)
    payload = fixture_json("greenhouse_imc.json")
    gone = payload["jobs"].pop(0)
    r = default_router()
    r.add("https://boards-api.greenhouse.io/v1/boards/imc/jobs", json_body=payload)
    rep = go(cfg, data, router=r)
    closed = read_jsonl(data / "closed.jsonl")
    assert [c["id"] for c in closed] == [f"greenhouse:{gone['id']}"]
    assert rep.manifest["removed_closed"] == 1
    assert f"greenhouse:{gone['id']}" not in {p["id"] for p in read_jsonl(data / "inbox" / "pending.jsonl")}


def test_empty_board_does_not_close_everything(tmp_path):
    cfg = write_cfg(tmp_path, companies=[{"name": "IMC", "ats": "greenhouse", "token": "imc", "complete": True}])
    data = tmp_path / "data"
    go(cfg, data)
    r = default_router()
    r.add("https://boards-api.greenhouse.io/v1/boards/imc/jobs", json_body={"jobs": []})
    go(cfg, data, router=r)
    assert not (data / "closed.jsonl").exists()


def test_failing_source_is_isolated_and_alerted(tmp_path):
    cfg, data = write_cfg(tmp_path), tmp_path / "data"
    go(cfg, data)
    r = default_router()
    r.add("https://hn.algolia.com/api/v1/search_by_date", status=403)
    alerter = FakeAlerter()
    rep = go(cfg, data, router=r, alerter=alerter)
    src = rep.manifest["sources"]
    assert src["hn"]["status"] == "blocked" and "403" in src["hn"]["detail"]
    assert src["greenhouse"]["status"] == "ok" and src["wwr"]["status"] == "ok"
    assert len(alerter.sent) == 1 and "hn: broke (blocked)" in alerter.sent[0]


def test_parser_crash_becomes_error_not_crash(tmp_path):
    cfg, data = write_cfg(tmp_path), tmp_path / "data"
    r = default_router()
    r.add("https://boards-api.greenhouse.io/v1/boards/flowtraders/jobs", json_body={"jobs": [{"no_id": True}]})
    rep = go(cfg, data, router=r)
    gh = rep.manifest["sources"]["greenhouse"]
    assert gh["status"] == "partial" and "KeyError" in gh["detail"]


def test_persistent_failures_listed_after_three_runs(tmp_path):
    cfg, data = write_cfg(tmp_path), tmp_path / "data"
    r = default_router()
    r.add("https://weworkremotely.com/categories/remote-back-end-programming-jobs.rss", status=429)
    for _ in range(3):
        rep = go(cfg, data, router=r)
    assert rep.manifest["persistent_failures"] == ["wwr"]


def test_budget_soft_limit_runs_tier1_only(tmp_path):
    cfg, data = write_cfg(tmp_path), tmp_path / "data"
    (cfg / "sources.yaml").write_text(yaml.safe_dump({"sources": {"wwr": {"tier": 2}}}), encoding="utf-8")
    (data / "usage").mkdir(parents=True)
    (data / "usage" / "2026-11.json").write_text(json.dumps({"runs": [], "total_minutes": 1500}), encoding="utf-8")
    rep = go(cfg, data)
    assert rep.mode == "tier1"
    assert rep.manifest["sources"]["wwr"]["status"] == "skipped"
    assert rep.manifest["sources"]["greenhouse"]["status"] == "ok"


def test_budget_hard_limit_exits_without_fetching(tmp_path):
    cfg, data = write_cfg(tmp_path), tmp_path / "data"
    (data / "usage").mkdir(parents=True)
    (data / "usage" / "2026-11.json").write_text(json.dumps({"runs": [], "total_minutes": 1800}), encoding="utf-8")
    r = default_router()
    rep = go(cfg, data, router=r)
    assert rep.mode == "stop" and r.calls == []
    m = json.loads((data / "inbox" / "manifest.json").read_text(encoding="utf-8"))
    assert "notice" in m and all(s["status"] == "skipped" for s in m["sources"].values())


def test_cli_dry_run_leaves_data_untouched(tmp_path, monkeypatch):
    """--dry-run works on a temp copy; the real data dir must not change."""
    cfg, data = write_cfg(tmp_path), tmp_path / "data"
    go(cfg, data)
    before = {p: p.read_bytes() for p in data.rglob("*") if p.is_file()}

    router = default_router()
    real_client = httpx.Client

    def patched_client(*a, **kw):
        kw["transport"] = httpx.MockTransport(router)
        return real_client(*a, **kw)

    monkeypatch.setattr(httpx, "Client", patched_client)
    monkeypatch.setattr("radar.http.time.sleep", lambda s: None)
    out = tmp_path / "out"
    assert run_main(["--config", str(cfg), "--data", str(data), "--dry-run", "--out", str(out)]) == 0
    after = {p: p.read_bytes() for p in data.rglob("*") if p.is_file()}
    assert before == after
    assert (out / "manifest.json").exists() and (out / "pending.jsonl").exists()


def test_cli_writes_commit_message(tmp_path, monkeypatch):
    cfg, data = write_cfg(tmp_path), tmp_path / "data"
    router = default_router()
    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda *a, **kw: real_client(*a, **{**kw, "transport": httpx.MockTransport(router)}))
    monkeypatch.setattr("radar.http.time.sleep", lambda s: None)
    msg_file = tmp_path / "msg.txt"
    run_main(["--config", str(cfg), "--data", str(data), "--commit-msg-file", str(msg_file)])
    msg = msg_file.read_text(encoding="utf-8")
    assert msg.startswith("radar: ") and msg.endswith("[skip ci]") and " new, " in msg


def test_text_chars_zero_disables_full_text(tmp_path):
    cfg = write_cfg(tmp_path, settings={"inbox": {"text_chars": 0}})
    data = tmp_path / "data"
    go(cfg, data)
    assert all("text" not in p for p in read_jsonl(data / "inbox" / "pending.jsonl"))


def test_summary_sent_every_run_by_default(tmp_path):
    cfg, data = write_cfg(tmp_path), tmp_path / "data"
    a = FakeAlerter()
    go(cfg, data, alerter=a)
    go(cfg, data, alerter=a)
    assert len(a.sent) == 2
    assert a.sent[0].startswith("📡 job-radar: ") and "top new:" in a.sent[0]
    assert "📡 job-radar: 0 new, 0 kept" in a.sent[1] and "top new:" not in a.sent[1]
    assert "⏱ " in a.sent[0] and "month " in a.sent[0]


def test_summary_on_new_skips_quiet_runs(tmp_path):
    cfg = write_cfg(tmp_path, settings={"alerts": {"summary": "on_new"}})
    data = tmp_path / "data"
    a = FakeAlerter()
    go(cfg, data, alerter=a)
    go(cfg, data, alerter=a)
    assert len(a.sent) == 1


def test_summary_off_still_sends_health_events(tmp_path):
    cfg = write_cfg(tmp_path, settings={"alerts": {"summary": "off"}})
    data = tmp_path / "data"
    a = FakeAlerter()
    go(cfg, data, alerter=a)
    r = default_router()
    r.add("https://hn.algolia.com/api/v1/search_by_date", status=403)
    go(cfg, data, router=r, alerter=a)
    assert len(a.sent) == 1 and "hn: broke" in a.sent[0]


def test_budget_hard_limit_notifies(tmp_path):
    cfg, data = write_cfg(tmp_path), tmp_path / "data"
    (data / "usage").mkdir(parents=True)
    (data / "usage" / "2026-11.json").write_text(json.dumps({"runs": [], "total_minutes": 1800}), encoding="utf-8")
    a = FakeAlerter()
    go(cfg, data, alerter=a)
    assert len(a.sent) == 1 and "budget hard limit" in a.sent[0]
