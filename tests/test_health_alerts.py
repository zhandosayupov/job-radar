import httpx
import pytest

from radar import health
from radar.alerts import Alerter, format_events, main as alerts_main

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
    assert b'"chat_id":"42"' in seen[0].content.replace(b" ", b"")


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
