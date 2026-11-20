"""Re-record test fixtures from live endpoints (polite client, robots respected).

    python tools/record_fixtures.py            # all
    python tools/record_fixtures.py greenhouse # one source

Fixtures are trimmed (few items, short descriptions, no author names, no job-seeker posts)
so the public repo stays small and holds only employer-published text.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from radar.http import Http
from radar.sources.hn import COMMENTS, STORIES, find_story, is_seeker_post
from radar.text import html_to_text

FIX = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
UA = "job-radar/0.1 (+https://github.com/zhandosayupov/job-radar)"
GH_BOARDS = {"imc": 8, "flowtraders": 6, "optiverprivate": 5}


def dump(name: str, obj) -> None:
    p = FIX / name
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {p.relative_to(FIX.parent.parent)} ({p.stat().st_size // 1024} KB)")


def greenhouse(http: Http) -> None:
    for token, n in GH_BOARDS.items():
        data = http.get_json(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs", params={"content": "true"})
        jobs = data["jobs"]
        # Keep a mix: some engineering in Europe, some elsewhere, some non-engineering.
        jobs.sort(key=lambda j: (("engineer" not in j["title"].lower() and "developer" not in j["title"].lower()), j["id"]))
        keep = jobs[: n - 2] + jobs[-2:]
        for j in keep:
            j["content"] = (j.get("content") or "")[:6000]
            j.pop("metadata", None)
        dump(f"greenhouse_{token}.json", {"jobs": keep, "meta": {"total": len(keep)}})


def hn(http: Http) -> None:
    stories = http.get_json(STORIES, params={"tags": "story,author_whoishiring", "hitsPerPage": 5})
    for h in stories["hits"]:
        h.pop("_highlightResult", None)
        h.pop("author", None)
    dump("hn_stories.json", stories)
    story = find_story(stories, max_age_days=400)
    sid = int(story["objectID"])
    for q, name in (("C++", "cpp"), ("visa", "visa")):
        data = http.get_json(COMMENTS, params={"tags": f"comment,story_{sid}", "query": q, "hitsPerPage": 50})
        hits = []
        for h in data["hits"]:
            if is_seeker_post(html_to_text(h.get("comment_text"))):
                continue  # never store individuals' posts
            for k in ("_highlightResult", "author", "children"):
                h.pop(k, None)
            hits.append(h)
        dump(f"hn_comments_{name}.json", {"hits": hits[:15], "nbHits": len(hits[:15])})


def wwr(http: Http) -> None:
    resp = http.get("https://weworkremotely.com/categories/remote-back-end-programming-jobs.rss")
    (FIX / "wwr_backend.rss").write_bytes(resp.content)
    print(f"wrote tests/fixtures/wwr_backend.rss ({len(resp.content) // 1024} KB)")


def main() -> int:
    FIX.mkdir(parents=True, exist_ok=True)
    which = set(sys.argv[1:]) or {"greenhouse", "hn", "wwr"}
    http = Http(UA)
    try:
        for name, fn in (("greenhouse", greenhouse), ("hn", hn), ("wwr", wwr)):
            if name in which:
                fn(http)
    finally:
        http.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
