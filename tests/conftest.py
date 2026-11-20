from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

import httpx
import pytest

from radar.config import load_config
from radar.http import Http
from radar.models import Job

FIX = Path(__file__).parent / "fixtures"
NOW_ISO = "2026-11-20T12:00:00+00:00"


def fixture_bytes(name: str) -> bytes:
    return (FIX / name).read_bytes()


def fixture_json(name: str):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def settings() -> dict:
    return load_config(None)["settings"]


def make_job(**kw) -> Job:
    base = dict(
        source="greenhouse",
        source_job_id=kw.pop("source_job_id", "1"),
        company="Acme",
        title="Software Engineer",
        url="https://example.com/jobs/1",
        location_text="Amsterdam",
        first_seen=NOW_ISO,
    )
    base.update(kw)
    return Job.build(**base)


class Router:
    """httpx.MockTransport handler: map (host, path) -> response factory. Records calls."""

    def __init__(self):
        self.routes: dict[tuple[str, str], Callable[[httpx.Request], httpx.Response]] = {}
        self.calls: list[httpx.Request] = []

    def add(self, url: str, *, json_body=None, content: bytes | None = None, status: int = 200,
            headers: dict | None = None) -> None:
        u = httpx.URL(url)

        def handler(req: httpx.Request) -> httpx.Response:
            if json_body is not None:
                return httpx.Response(status, json=json_body, headers=headers)
            return httpx.Response(status, content=content or b"", headers=headers)

        self.routes[(u.host, u.path)] = handler

    def add_fn(self, url: str, fn: Callable[[httpx.Request], httpx.Response]) -> None:
        u = httpx.URL(url)
        self.routes[(u.host, u.path)] = fn

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(req)
        fn = self.routes.get((req.url.host, req.url.path))
        if fn is None:
            return httpx.Response(404, content=b"not found")
        return fn(req)


def make_http(router: Router, **kw) -> Http:
    kw.setdefault("sleep", lambda s: None)
    return Http("job-radar-test/0 (+https://example.invalid)", transport=httpx.MockTransport(router), **kw)


def default_router() -> Router:
    """All Phase-0 sources served from recorded fixtures."""
    r = Router()
    for token in ("imc", "flowtraders", "optiverprivate"):
        r.add(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs", json_body=fixture_json(f"greenhouse_{token}.json"))
    r.add("https://hn.algolia.com/api/v1/search_by_date", json_body=fixture_json("hn_stories.json"))

    cpp = fixture_json("hn_comments_cpp.json")
    visa = fixture_json("hn_comments_visa.json")

    def comments(req: httpx.Request) -> httpx.Response:
        q = req.url.params.get("query", "")
        body = cpp if q == "C++" else visa if q == "visa" else {"hits": []}
        return httpx.Response(200, json=body)

    r.add_fn("https://hn.algolia.com/api/v1/search", comments)
    r.add(
        "https://weworkremotely.com/categories/remote-back-end-programming-jobs.rss",
        content=fixture_bytes("wwr_backend.rss"),
        headers={"content-type": "application/rss+xml"},
    )
    return r
