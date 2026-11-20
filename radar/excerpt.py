"""Build a <=700-char excerpt that maximizes signal per labeler token.

Sentence priority: visa/relocation -> remote/location eligibility -> experience/level
-> C++/latency/stack -> salary. Benefits and EEO boilerplate are dropped.
"""
from __future__ import annotations

import re

from .text import VISA_RE, clip, split_sentences

PRIORITY: list[tuple[str, re.Pattern[str]]] = [
    ("visa", VISA_RE),
    (
        "remote",
        re.compile(
            r"remote|anywhere|worldwide|time ?zone|\bUTC\b|\bCET\b|based in|located in|"
            r"office in|hybrid|on-?site|in-office|eligib|удал[её]н|из любой|офис|гибрид",
            re.I,
        ),
    ),
    (
        "level",
        re.compile(
            r"\d\+?\s*(?:-\s*\d+\s*)?years?|years of experience|\bsenior\b|\bjunior\b|\bmid\b|"
            r"опыт|лет\b|года\b",
            re.I,
        ),
    ),
    (
        "stack",
        re.compile(
            r"c\+\+|\bcpp\b|latency|performance|linux|kernel|distributed|trading|"
            r"\bgo(?:lang)?\b|\brust\b|\bpython\b|\bjava\b|\bsql\b|multithread|concurren|"
            r"high[- ]load|highload|search|database",
            re.I,
        ),
    ),
    (
        "salary",
        re.compile(r"salary|compensation|\bpay\b|[$€£₸]|\bEUR\b|\bUSD\b|\bGBP\b|зарплат|оклад|вилк", re.I),
    ),
]

BOILERPLATE = re.compile(
    r"equal opportunity|\beeo\b|affirmative action|gender identity|sexual orientation|"
    r"protected veteran|national origin|disabilit|reasonable accommodation|"
    r"we offer|benefits|perks|health insurance|dental|gym|pension|401\(k\)|paid time off|"
    r"\bPTO\b|free (?:lunch|snacks|meals)|wellness|parental leave|privacy (?:notice|policy)|"
    r"recruitment agenc|ДМС|печеньк",
    re.I,
)


def build_excerpt(text: str | None, limit: int = 700) -> str | None:
    if not text:
        return None
    sentences = [s for s in split_sentences(text) if not BOILERPLATE.search(s)]
    chosen: list[str] = []
    used = 0
    seen: set[str] = set()
    for _, pattern in PRIORITY:
        for s in sentences:
            if s in seen or not pattern.search(s):
                continue
            s_clip = clip(s, 300) or ""
            cost = len(s_clip) + (1 if chosen else 0)
            if used + cost > limit:
                continue
            chosen.append(s_clip)
            seen.add(s)
            used += cost
    if not chosen:
        return None
    return " ".join(chosen)
