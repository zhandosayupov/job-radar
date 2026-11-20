import httpx
import pytest

from radar import health
from radar.alerts import Alerter, format_events, format_run_message, should_send
from radar.alerts import main as alerts_main

TOKEN = "123456789:AAAbbbCCCdddEEEfffGGGhhhIIIjjjKKKlll"


def step(h, status, fetched=5, src="s"):
    return health.update(h, {src: {"status": status, "fetched": fetched}}, "t")


def kinds(events):
    return [e.kind for e in events]


def test_first_run_no_events():
    assert step({}, "error") == []


def test_broke_then_recovered():
    h = {}
    step(h, "ok")
    assert kinds(step(h, "blocked")) == ["broke"]
    assert kinds(step(h, "blocked")) == []  # no repeat while still broken
    assert kinds(step(h, "ok")) == ["recovered"]


def test_degraded():
    h = {}
    step(h, "ok")
    assert kinds(step(h, "partial")) == ["degraded"]


def test_silent_empty_then_recovered():
    h = {}
    step(h, "ok", 10)
    assert kinds(step(h, "ok", 0)) == ["empty"]
    assert kinds(step(h, "ok", 0)) == []  # still empty: no repeat
    assert kinds(step(h, "ok", 3)) == ["recovered"]


def test_skipped_runs_are_ignored():
    h = {}
    step(h, "ok")
    assert step(h, "skipped", 0) == []
    assert kinds(step(h, "error")) == ["broke"]


def test_consecutive_bad_and_history_cap():
    h = {}
    for _ in range(20):
        step(h, "error")
    assert health.consecutive_bad(h, "s") == health.HISTORY_LEN
    step(h, "ok")
    assert health.consecutive_bad(h, "s") == 0


# --- alerts ---------------------------------------------------------------------------


def test_alerter_disabled_without_secrets(monkeypatch):
    monkeypatch.delenv("RADAR_TG_BOT_TOKEN", raising=False)
    monkeypatch.delenv("RADAR_TG_CHAT_ID", raising=False)
    a = Alerter()
    assert not a.enabled and a.send("x") is False


def test_alerter_sends_payload():
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(200, json={"ok": True})

    a = Alerter(TOKEN, "42", transport=httpx.MockTransport(handler))
    assert a.send("hello") is True
    assert seen[0].url.path == f"/bot{TOKEN}/sendMessage"
    body = seen[0].content.replace(b" ", b"")
    assert b'"chat_id":"42"' in body and b'"disable_notification":false' in body
    assert a.send("quiet", silent=True) is True
    assert b'"disable_notification":true' in seen[1].content.replace(b" ", b"")


@pytest.mark.parametrize("mode", ["raise", "500"])
def test_alerter_errors_never_leak_token(mode):
    def handler(req):
        if mode == "raise":
            raise httpx.ConnectError(f"cannot connect to {req.url}")
        return httpx.Response(500)

    a = Alerter(TOKEN, "42", transport=httpx.MockTransport(handler))
    assert a.send("x") is False
    assert a.last_error and TOKEN not in a.last_error


def test_format_events():
    ev = [health.Event("hn", "broke", "blocked", "HTTP 403 on hn.algolia.com"), health.Event("wwr", "recovered", "ok")]
    text = format_events(ev, "https://github.com/x/y/actions/runs/1")
    assert "🔴 hn: broke (blocked) — HTTP 403" in text and "🟢 wwr: recovered" in text and text.endswith("/runs/1")
    assert format_events([]) is None


MANIFEST = {
    "mode": "full",
    "new_total": 155,
    "kept_total": 45,
    "pending_count": 45,
    "overflow": 3,
    "removed_labeled": 2,
    "dropped_by_rule": {"title_excluded": 40, "location_na_au": 37},
    "sources": {
        "greenhouse": {"status": "ok", "fetched": 84, "new": 84, "kept": 16},
        "hn": {"status": "blocked", "fetched": 0, "new": 0, "kept": 0, "detail": "story: HTTP 403 on hn.algolia.com"},
    },
    "this_run_minutes": 1,
    "month_minutes_used": 7,
}


def test_run_message_contents():
    top = [{"company": "IMC", "title": "Performance Engineer", "location_text": "Amsterdam, Netherlands"}]
    ev = [health.Event("hn", "broke", "blocked", "HTTP 403")]
    text = format_run_message(MANIFEST, ev, top, "https://github.com/x/y/actions/runs/1", 2000)
    assert text.startswith("⚠️ source health changed:\n🔴 hn: broke (blocked) — HTTP 403")
    assert "📡 job-radar: 155 new, 45 kept → inbox 45 (+3 overflow)" in text
    assert "✅ greenhouse: 84 fetched, 84 new, 16 kept" in text
    assert "🔴 hn: 0 fetched, 0 new, 0 kept — story: HTTP 403" in text
    assert "dropped: title 40, US/CA/AU 37" in text
    assert "left pending: labeled 2" in text
    assert "• IMC — Performance Engineer (Amsterdam, Netherlands)" in text
    assert "⏱ 1 min · month 7/2000" in text and text.endswith("/runs/1")


@pytest.mark.parametrize(
    "mode,kept,events,expected",
    [
        ("always", 0, False, True),
        ("on_new", 0, False, False),
        ("on_new", 3, False, True),
        ("off", 3, False, False),
        ("off", 0, True, True),  # health changes are always sent
    ],
)
def test_should_send(mode, kept, events, expected):
    ev = [health.Event("s", "broke", "error")] if events else []
    assert should_send(mode, {"kept_total": kept}, ev) is expected


def test_cli_without_secrets_is_noop(monkeypatch, capsys):
    monkeypatch.delenv("RADAR_TG_BOT_TOKEN", raising=False)
    assert alerts_main(["--text", "x"]) == 0
    assert "not configured" in capsys.readouterr().out
    assert alerts_main(["--text", "x", "--strict"]) == 1


def test_cli_strict_fails_on_delivery_error(monkeypatch, capsys):
    monkeypatch.setenv("RADAR_TG_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("RADAR_TG_CHAT_ID", "42")
    monkeypatch.setattr(Alerter, "send", lambda self, text: False)
    assert alerts_main(["--text", "x"]) == 0
    assert alerts_main(["--text", "x", "--strict"]) == 1
    assert TOKEN not in capsys.readouterr().out
