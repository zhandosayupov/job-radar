"""We Work Remotely category RSS feeds.

`<region>` is the advertised scope ("Anywhere in the World"); `<country>` is the list of
countries the employer accepts. When a country list exists it is the real scope, so the
region label is dropped (it otherwise makes LATAM-only jobs look worldwide).
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

import feedparser

from ..http import Http
from ..models import Job
from .base import FetchOutcome, RunContext, Source, iso_utc

DEFAULT_FEEDS = ["https://weworkremotely.com/categories/remote-back-end-programming-jobs.rss"]
_FLAGS = re.compile("[\U0001F1E6-\U0001F1FF]")


def _clean(s: str | None) -> str:
    return re.sub(r"\s+", " ", _FLAGS.sub("", s or "")).strip(" ,")


def parse_feed(content: bytes, now_iso: str) -> list[Job]:
    feed = feedparser.parse(content)
    jobs: list[Job] = []
    for e in feed.entries:
        raw_title = e.get("title", "")
        company, _, title = raw_title.partition(": ")
        if not title:
            company, title = "", raw_title
        region = _clean(e.get("region"))
        countries = _clean(e.get("country"))
        # An explicit country list overrides the generic region label ("Anywhere in the
        # World" is often shown next to a LATAM-only or US-only list).
        remote = f"Remote; countries: {countries}" if countries else (f"Remote: {region}" if region else "Remote")
        link = e.get("link") or e.get("id")
        slug = urlsplit(link).path.rstrip("/").rsplit("/", 1)[-1]
        jobs.append(
            Job.build(
                source="wwr",
                source_job_id=slug,
                company=company,
                title=title,
                url=link,
                location_text=countries or region or None,
                remote_text=remote,
                posted_at=iso_utc(e.get("published")),
                description_html=e.get("summary"),
                first_seen=now_iso,
            )
        )
    return jobs


class WWRSource(Source):
    name = "wwr"
    kind = "aggregator"

    def fetch(self, http: Http, ctx: RunContext) -> FetchOutcome:
        out = FetchOutcome()
        for url in self.cfg.get("feeds") or DEFAULT_FEEDS:

            def one(url=url) -> None:
                resp = http.get(url)
                out.jobs.extend(parse_feed(resp.content, ctx.now_iso))

            out.run_unit(url.rsplit("/", 1)[-1], one)
        return out
