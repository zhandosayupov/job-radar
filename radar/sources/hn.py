"""Hacker News "Ask HN: Who is hiring?" via the Algolia API. Employer posts only."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from ..http import Http
from ..models import Job
from ..text import extract_salary_text, html_to_text
from .base import FetchOutcome, RunContext, Source, iso_utc

STORIES = "https://hn.algolia.com/api/v1/search_by_date"
COMMENTS = "https://hn.algolia.com/api/v1/search"
DEFAULT_QUERIES = ["C++", "visa", "relocation", "remote"]

ENG_RE = re.compile(
    r"engineer|developer|programmer|\bswe\b|\bsre\b|devops|architect|\bmts\b|technical staff|"
    r"backend|back-end|full[- ]?stack|infrastructure|platform|c\+\+",
    re.I,
)
REMOTE_RE = re.compile(r"remote|onsite|on-site|hybrid|in[- ]office|in person", re.I)
JOBTYPE_RE = re.compile(r"^(full[- ]?time|part[- ]?time|contract(or)?|intern(ship)?|ft|pt|permanent|freelance)\b", re.I)
URL_RE = re.compile(r"^(https?://|www\.)|^\S+\.(com|io|ai|dev|co|org|net|app|tech)(/\S*)?$", re.I)
SEEKER_FIELD_RE = re.compile(r"^(location|remote|willing to relocate|technologies|r[ée]sum[ée]|cv|email)\s*:", re.I | re.M)
SEEKER_SELF_RE = re.compile(r"\bI(?:'m| am) an? (?:\w+ ){0,3}(?:engineer|developer|programmer)", re.I)
HIRING_RE = re.compile(r"\bwe(?:'re| are) hiring|\bjoin (?:us|our)|\bour team\b|\bwe(?:'re| are) looking", re.I)


def is_seeker_post(text: str) -> bool:
    if len(SEEKER_FIELD_RE.findall(text[:600])) >= 2:
        return True
    head = text[:400]
    return bool(SEEKER_SELF_RE.search(head)) and not HIRING_RE.search(text)


def parse_comment(hit: dict, now_iso: str) -> Job | None:
    text = html_to_text(hit.get("comment_text"))
    if not text or is_seeker_post(text):
        return None
    lines = text.split("\n")
    header = lines[0]
    if "|" not in header:
        return None
    parts = [p.strip() for p in header.split("|") if p.strip()]
    if len(parts) < 2:
        return None
    company = parts[0]
    title = None
    remote, salary, location = [], None, []
    for p in parts[1:]:
        if URL_RE.search(p) or JOBTYPE_RE.search(p):
            continue
        if title is None and ENG_RE.search(p) and not REMOTE_RE.fullmatch(p):
            title = p
            continue
        if REMOTE_RE.search(p):
            remote.append(p)
            continue
        if re.search(r"visa|sponsor", p, re.I):
            continue  # visa evidence is extracted from the full text
        if salary is None and extract_salary_text(p):
            salary = p
            continue
        location.append(p)
    if title is None:
        for ln in lines[1:]:
            if len(ln) <= 100 and ENG_RE.search(ln):
                title = ln.strip(" -•*:")
                break
    if not title:
        return None
    return Job.build(
        source="hn",
        source_job_id=hit["objectID"],
        company=company,
        title=title[:150],
        url=f"https://news.ycombinator.com/item?id={hit['objectID']}",
        location_text=" | ".join(location) or None,
        remote_text=" | ".join(remote) or None,
        posted_at=iso_utc(hit.get("created_at")),
        salary_text=salary,
        description_text=text,
        first_seen=now_iso,
    )


def find_story(payload: dict, max_age_days: int = 40) -> dict | None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    for h in payload.get("hits", []):
        if not str(h.get("title", "")).startswith("Ask HN: Who is hiring?"):
            continue
        created = iso_utc(h.get("created_at"))
        if created and datetime.fromisoformat(created) >= cutoff:
            return h
    return None


class HNSource(Source):
    name = "hn"
    kind = "community"

    def fetch(self, http: Http, ctx: RunContext) -> FetchOutcome:
        out = FetchOutcome()
        queries = self.cfg.get("queries") or DEFAULT_QUERIES
        story: dict | None = None

        def get_story() -> None:
            nonlocal story
            payload = http.get_json(STORIES, params={"tags": "story,author_whoishiring", "hitsPerPage": 5})
            story = find_story(payload)

        out.run_unit("story", get_story)
        if story is None:
            if not out.errors and not out.blocked:
                out.notes.append("no current 'Who is hiring' thread")
            return out
        sid = int(story["objectID"])
        seen: set[str] = set()
        skipped = 0
        for q in queries:

            def one(q=q) -> None:
                nonlocal skipped
                payload = http.get_json(
                    COMMENTS, params={"tags": f"comment,story_{sid}", "query": q, "hitsPerPage": 50}
                )
                for hit in payload.get("hits", []):
                    oid = str(hit.get("objectID"))
                    if oid in seen or int(hit.get("parent_id") or 0) != sid:
                        continue
                    seen.add(oid)
                    job = parse_comment(hit, ctx.now_iso)
                    if job is None:
                        skipped += 1
                    else:
                        out.jobs.append(job)

            out.run_unit(f"q={q}", one)
        if skipped:
            out.notes.append(f"{skipped} non-employer/unparsable posts skipped")
        return out
