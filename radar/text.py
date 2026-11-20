"""Text helpers: HTML stripping, sentence splitting, URL canonicalization, evidence extraction.

Everything here is deterministic and offline so it can be unit-tested exhaustively.
"""
from __future__ import annotations

import hashlib
import html
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from selectolax.lexbor import LexborHTMLParser as HTMLParser

# --- HTML -> text -----------------------------------------------------------------

_BLOCK_TAGS = ("p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section")


def html_to_text(raw: str | None) -> str:
    """Strip HTML to plain text, keeping block boundaries as newlines.

    Handles double-escaped HTML (Greenhouse returns `&lt;p&gt;...`).
    """
    if not raw:
        return ""
    s = raw
    if "&lt;" in s and "<" not in s:
        s = html.unescape(s)
    if "<" not in s:
        return _squash(html.unescape(s))
    tree = HTMLParser(s)
    for tag in ("script", "style", "noscript"):
        for node in tree.css(tag):
            node.decompose()
    for tag in _BLOCK_TAGS:
        for node in tree.css(tag):
            node.insert_before("\n")
            node.insert_after("\n")
    body = tree.body or tree.root
    text = body.text(separator="", deep=True) if body else ""
    return _squash(html.unescape(text))


def _squash(s: str) -> str:
    s = s.replace("\xa0", " ").replace("\r", "")
    s = re.sub(r"[ \t\f\v]+", " ", s)
    s = re.sub(r" *\n *", "\n", s)
    s = re.sub(r"\n{2,}", "\n", s)
    return s.strip()


# --- Sentences ----------------------------------------------------------------------

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-ZА-ЯЁ0-9•\-–(])|\n+")


def split_sentences(text: str) -> list[str]:
    parts = (p.strip(" •-–*\t") for p in _SENT_SPLIT.split(text or ""))
    return [p for p in parts if len(p) >= 3]


def clip(s: str | None, limit: int) -> str | None:
    if s is None:
        return None
    s = s.strip()
    if len(s) <= limit:
        return s
    cut = s[: limit - 1].rstrip()
    return cut + "…"


# --- Evidence -------------------------------------------------------------------

VISA_RE = re.compile(
    r"visa|relocat|sponsor|work permit|blue card|skilled worker|right to work|"
    r"виз[аыу]|релокац|переезд|разрешени[ея] на работу",
    re.I,
)


def extract_visa_text(text: str, limit: int = 200) -> str | None:
    hits = [s for s in split_sentences(text) if VISA_RE.search(s)]
    if not hits:
        return None
    return clip(" ".join(hits), limit)


_CUR = r"(?:[$€£₸]|USD|EUR|GBP|CHF|AED|SGD|KZT|руб\.?|₽)"
_NUM = r"\d[\d,. ]*\d(?:\s?[kKкК])?|\d(?:\s?[kKкК])?"
SALARY_RE = re.compile(
    rf"(?:{_CUR}\s?(?:{_NUM})(?:\s?(?:-|–|to|до)\s?{_CUR}?\s?(?:{_NUM}))?(?:\s?{_CUR})?)"
    rf"|(?:(?:{_NUM})\s?(?:-|–|to)\s?(?:{_NUM})\s?{_CUR})"
    rf"|(?:(?:от|до)\s(?:{_NUM})\s?{_CUR})",
    re.I,
)


_FUNDING_AFTER = re.compile(r"\s?(?:m|mm|b|bn|mn|million|billion|млн|млрд)\b", re.I)


# "€10k signing bonus", "$5k relocation budget" are not salaries.
_NOT_SALARY_AFTER = re.compile(
    r"\s*(?:\+\s*)?(?:signing|sign-on|bonus|relocation|stipend|allowance|budget|grant|learning|equipment)", re.I
)


def extract_salary_text(text: str, limit: int = 120) -> str | None:
    """Verbatim salary fragment if the text states one with a currency; else None."""
    text = text or ""
    for m in SALARY_RE.finditer(text):
        frag = m.group(0).strip()
        digits = re.sub(r"\D", "", frag)
        if len(digits) < 2:  # avoid "$5" noise
            continue
        after = text[m.end() : m.end() + 20]
        if _FUNDING_AFTER.match(after) or _NOT_SALARY_AFTER.match(after):  # "$17.5M raised", "€10k bonus"
            continue
        return clip(frag, limit)
    return None


# --- Language -----------------------------------------------------------------------


def detect_lang(text: str) -> str:
    letters = re.findall(r"[A-Za-zА-Яа-яЁё]", text or "")
    if not letters:
        return "en"
    cyr = sum(1 for c in letters if "А" <= c <= "я" or c in "Ёё")
    if cyr / len(letters) > 0.3:
        return "ru"
    return "en"


# --- URLs -----------------------------------------------------------------------------

_TRACKING_PREFIXES = ("utm_", "mc_", "_hs")
_TRACKING_KEYS = {
    "gh_src", "gh_jid", "lever-source", "lever-origin", "ref", "referrer", "source",
    "src", "fbclid", "gclid", "yclid", "msclkid", "trk", "trackingid", "ashby_jid",
}


def canonical_url(url: str) -> str:
    """https, lowercase host, no fragment, no tracking params, no trailing slash."""
    parts = urlsplit(url.strip())
    scheme = "https"
    host = parts.netloc.lower()
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=False)
        if k.lower() not in _TRACKING_KEYS and not k.lower().startswith(_TRACKING_PREFIXES)
    ]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((scheme, host, path, urlencode(query), ""))


def sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


# --- Fingerprint ----------------------------------------------------------------------

_COMPANY_SUFFIX = re.compile(
    r"\b(inc|llc|ltd|limited|gmbh|ag|bv|b\.v|plc|sa|sas|srl|llp|lp|co|corp|corporation|group|holdings?)\b\.?",
    re.I,
)


def _norm(s: str) -> str:
    s = s.lower().replace("ё", "е")
    s = re.sub(r"[^\w]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def fingerprint(company: str, title: str, location_text: str | None) -> str:
    comp = _norm(_COMPANY_SUFFIX.sub(" ", company or ""))
    ttl = _norm(re.sub(r"\(.*?\)", " ", title or ""))
    city = ""
    if location_text:
        first = re.split(r"[,;/|•(]| - ", location_text)[0]
        city = _norm(first)
    return f"{comp}|{ttl}|{city}"
