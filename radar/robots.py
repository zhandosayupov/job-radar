"""robots.txt per RFC 9309 (the stdlib `urllib.robotparser` ignores `*` and `$`, so it
would e.g. allow hh.kz search pages that `Disallow: *?*` forbids).

- Groups: consecutive `User-agent` lines share the rules that follow. All groups naming
  our product token (case-insensitive) are merged; otherwise the `*` groups are used.
- Rules match against path + query from the start; `*` = any sequence, a trailing `$`
  anchors the end.
- Most specific (longest) matching rule wins; on a tie Allow wins.
- `/robots.txt` itself is always allowed. Empty `Disallow:` allows everything.
- `Crawl-delay` (non-standard, widely used) is exposed so the client can slow down.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import quote, unquote, urlsplit

MAX_BYTES = 500 * 1024  # RFC 9309: parse at least the first 500 KiB


@dataclass
class Rule:
    allow: bool
    pattern: str
    regex: re.Pattern[str]


@dataclass
class Group:
    agents: list[str] = field(default_factory=list)
    rules: list[Rule] = field(default_factory=list)
    crawl_delay: float | None = None


def _normalize(s: str) -> str:
    """Canonical percent-encoding so '/a%2Fb' and '/a/b' style differences compare consistently."""
    return quote(unquote(s), safe="/?=&*$:@!,;+%-._~")


def _compile(pattern: str) -> re.Pattern[str]:
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    rx = "".join(".*" if ch == "*" else re.escape(ch) for ch in _normalize(body))
    return re.compile(rx + ("$" if anchored else ""), re.S)


def parse(text: str) -> list[Group]:
    groups: list[Group] = []
    current: Group | None = None
    last_was_agent = False
    for raw in text[:MAX_BYTES].splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, value = (x.strip() for x in line.split(":", 1))
        key = key.lower()
        if key == "user-agent":
            if current is None or not last_was_agent:
                current = Group()
                groups.append(current)
            current.agents.append(value.lower())
            last_was_agent = True
            continue
        last_was_agent = False if key in ("allow", "disallow", "crawl-delay") else last_was_agent
        if current is None:
            continue
        if key in ("allow", "disallow"):
            if not value:
                continue  # empty Disallow = no restriction
            current.rules.append(Rule(key == "allow", value, _compile(value)))
        elif key == "crawl-delay":
            try:
                current.crawl_delay = float(value)
            except ValueError:
                pass
    return groups


class Robots:
    def __init__(self, text: str, token: str):
        self.token = token.lower()
        groups = parse(text)
        mine = [g for g in groups if self.token in g.agents]
        chosen = mine or [g for g in groups if "*" in g.agents]
        self.rules: list[Rule] = [r for g in chosen for r in g.rules]
        delays = [g.crawl_delay for g in chosen if g.crawl_delay is not None]
        self.crawl_delay: float | None = max(delays) if delays else None

    def can_fetch(self, url: str) -> bool:
        parts = urlsplit(url)
        target = parts.path or "/"
        if target == "/robots.txt":
            return True
        if parts.query:
            target += "?" + parts.query
        target = _normalize(target)
        best: Rule | None = None
        for r in self.rules:
            if not r.regex.match(target):
                continue
            if best is None or len(r.pattern) > len(best.pattern) or (
                len(r.pattern) == len(best.pattern) and r.allow and not best.allow
            ):
                best = r
        return best is None or best.allow
