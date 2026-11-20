"""Telegram Bot API alerts to the owner (a bot you created, not the userbot).

Secrets come from env: RADAR_TG_BOT_TOKEN, RADAR_TG_CHAT_ID. Without them alerts are a
no-op. The token is part of the request URL, so errors are reported without the URL and
any accidental echo of the token is masked.

CLI (used by the workflow's failure step):
    python -m radar.alerts --text "radar run failed: <url>"
"""
from __future__ import annotations

import argparse
import os
import sys

import httpx

from .health import Event

ICONS = {"broke": "🔴", "degraded": "🟠", "empty": "🟡", "recovered": "🟢"}


class Alerter:
    def __init__(
        self,
        token: str | None = None,
        chat_id: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ):
        self.token = token if token is not None else os.environ.get("RADAR_TG_BOT_TOKEN", "")
        self.chat_id = chat_id if chat_id is not None else os.environ.get("RADAR_TG_CHAT_ID", "")
        self.transport = transport
        self.last_error: str | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def _mask(self, s: str) -> str:
        return s.replace(self.token, "***") if self.token else s

    def send(self, text: str) -> bool:
        if not self.enabled:
            return False
        try:
            with httpx.Client(timeout=15, transport=self.transport) as c:
                r = c.post(
                    f"https://api.telegram.org/bot{self.token}/sendMessage",
                    json={"chat_id": self.chat_id, "text": text[:4000], "disable_web_page_preview": True},
                )
            if r.status_code != 200:
                self.last_error = f"telegram HTTP {r.status_code}"
                return False
            return True
        except Exception as e:  # never let alerting break a run; never echo the URL
            self.last_error = self._mask(f"telegram {type(e).__name__}")
            return False


def format_events(events: list[Event], run_url: str | None = None) -> str | None:
    if not events:
        return None
    lines = ["job-radar: source health changed"]
    for e in events:
        icon = ICONS.get(e.kind, "•")
        d = f" — {e.detail[:200]}" if e.detail else ""
        lines.append(f"{icon} {e.source}: {e.kind} ({e.status}){d}")
    if run_url:
        lines.append(run_url)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", required=True)
    ap.add_argument("--strict", action="store_true", help="exit 1 if not configured or not delivered")
    args = ap.parse_args(argv)
    a = Alerter()
    if not a.enabled:
        print("alerts: not configured, skipped")
        return 1 if args.strict else 0
    ok = a.send(args.text)
    print("alerts: sent" if ok else f"alerts: failed ({a.last_error})")
    return 0 if ok or not args.strict else 1


if __name__ == "__main__":
    sys.exit(main())
