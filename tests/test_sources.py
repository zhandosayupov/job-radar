"""Contract tests: parsers against recorded live responses (tests/fixtures).

If a source changes its format, re-record with `python tools/record_fixtures.py <source>`
and these tests show exactly what broke.
"""
from datetime import datetime, timezone

from radar.sources.base import RunContext, iso_utc
from radar.sources.greenhouse import GreenhouseSource, parse_board
from radar.sources.hn import HNSource, find_story, is_seeker_post, parse_comment
from radar.sources.wwr import WWRSource, parse_feed
from tests.conftest import NOW_ISO, Router, default_router, fixture_bytes, fixture_json, make_http


def ctx(companies=None):
    return RunContext(now_iso=NOW_ISO, settings={}, companies=companies or [])


# --- greenhouse -------------------------------------------------------------------------


def test_greenhouse_department_filter_and_fields():
    payload = fixture_json("greenhouse_imc.json")
    jobs = parse_board(payload, {"token": "imc", "name": "IMC", "departments": [4023813101]}, NOW_ISO)
    assert len(jobs) == 4
    assert {j.title for j in jobs} >= {"Performance Engineer", "Quant Developer DeFi - Digital Assets"}
    j = next(j for j in jobs if j.title == "Performance Engineer")
    assert j.id.startswith("greenhouse:") and j.company == "IMC"
    assert j.url.startswith("https://") and j.location_text == "Amsterdam, Netherlands"
    assert j.posted_at and j.posted_at.endswith("+00:00") and j.posted_approx is False
    assert j.excerpt is None or len(j.excerpt) <= 700
    assert j.description and "<" not in j.description[:200]


def test_greenhouse_without_filter_takes_all():
    payload = fixture_json("greenhouse_flowtraders.json")
    assert len(parse_board(payload, {"token": "flowtraders"}, NOW_ISO)) == len(payload["jobs"])


def test_greenhouse_falls_back_to_updated_at_as_approx():
    payload = {"jobs": [{"id": 1, "title": "SWE", "absolute_url": "https://x.io/1", "updated_at": "2026-11-01T10:00:00-04:00"}]}
    j = parse_board(payload, {"token": "x"}, NOW_ISO)[0]
    assert j.posted_at == "2026-11-01T14:00:00+00:00" and j.posted_approx is True


def test_greenhouse_fetch_marks_partial_and_complete_sets():
    r = default_router()
    r.add("https://boards-api.greenhouse.io/v1/boards/broken/jobs", status=500)
    comps = [
        {"token": "imc", "ats": "greenhouse", "departments": [4023813101], "complete": True},
        {"token": "flowtraders", "ats": "greenhouse"},
        {"token": "broken", "ats": "greenhouse"},
        {"token": "off", "ats": "greenhouse", "enabled": False},
        {"token": "lev", "ats": "lever"},
    ]
    out = GreenhouseSource().fetch(make_http(r, retries=0), ctx(comps))
    assert out.status == "partial" and out.units_ok == 2
    assert list(out.complete_sets) == ["greenhouse:imc"] and len(out.complete_sets["greenhouse:imc"]) == 4
    assert "broken" in out.errors[0]
    assert not any("off" in str(c.url) or "lev" in str(c.url) for c in r.calls)


def test_greenhouse_all_blocked():
    r = Router()
    r.add("https://boards-api.greenhouse.io/v1/boards/imc/jobs", status=403)
    out = GreenhouseSource().fetch(make_http(r), ctx([{"token": "imc", "ats": "greenhouse"}]))
    assert out.status == "blocked"


# --- hn ---------------------------------------------------------------------------------


def test_hn_find_story_picks_hiring_thread_not_wants_to_be_hired():
    s = find_story(fixture_json("hn_stories.json"), max_age_days=10_000)
    assert s["title"].startswith("Ask HN: Who is hiring?")


def test_hn_find_story_ignores_stale_threads():
    old = {"hits": [{"title": "Ask HN: Who is hiring? (January 2020)", "created_at": "2020-01-01T00:00:00Z", "objectID": "1"}]}
    assert find_story(old) is None


def test_hn_parse_employer_header():
    hits = {h["objectID"]: h for h in fixture_json("hn_comments_visa.json")["hits"]}
    j = parse_comment(hits["50518905"], NOW_ISO)
    assert (j.company, j.title, j.location_text, j.remote_text) == ("outGoing", "Software Engineer", "Lisbon, Portugal", "HYBRID")
    assert j.salary_text.startswith("€25k")
    assert j.url == "https://news.ycombinator.com/item?id=50518905"
    assert j.posted_at.endswith("+00:00")


def test_hn_visa_part_not_taken_as_salary():
    hits = {h["objectID"]: h for h in fixture_json("hn_comments_visa.json")["hits"]}
    j = parse_comment(hits["50348803"], NOW_ISO)  # "... | Visa + €10k Signing Bonus"
    assert j.company == "Odoo" and j.salary_text is None and j.visa_text


def test_hn_salary_from_body_when_header_has_none():
    hits = {h["objectID"]: h for h in fixture_json("hn_comments_cpp.json")["hits"]}
    j = parse_comment(hits["50340438"], NOW_ISO)
    assert j.title == "C++ Systems Engineer" and j.salary_text == "$250–325K"


def test_hn_header_without_pipes_is_skipped():
    hits = {h["objectID"]: h for h in fixture_json("hn_comments_cpp.json")["hits"]}
    assert parse_comment(hits["50341745"], NOW_ISO) is None


def test_hn_seeker_posts_are_skipped():
    seeker = (
        "Location: Somewhere\nRemote: Yes\nWilling to relocate: Yes\nTechnologies: C++, Go\n"
        "Résumé: https://example.invalid/cv\nEmail: someone@example.invalid"
    )
    assert is_seeker_post(seeker)
    assert parse_comment({"objectID": "1", "comment_text": seeker.replace("\n", "<p>")}, NOW_ISO) is None
    assert is_seeker_post("Jane Doe | Backend Engineer | Remote\nI'm a backend engineer with 5 years in Go.")
    assert not is_seeker_post("Acme | Backend Engineer | Remote\nWe're hiring! I'm a CTO and our team is small.")


def test_hn_fetch_keeps_top_level_and_dedupes_across_queries():
    out = HNSource({"queries": ["C++", "visa"]}).fetch(make_http(default_router()), ctx())
    assert out.status == "ok"
    ids = [j.id for j in out.jobs]
    assert len(ids) == len(set(ids))
    assert "hn:50342412" not in ids  # reply, not a top-level post
    assert ids.count("hn:50383984") == 1  # appears in both query results
    assert any("skipped" in n for n in out.notes)


def test_hn_no_thread_is_ok_with_note():
    r = Router()
    r.add("https://hn.algolia.com/api/v1/search_by_date", json_body={"hits": []})
    out = HNSource().fetch(make_http(r), ctx())
    assert out.status == "ok" and out.jobs == [] and out.notes


# --- wwr --------------------------------------------------------------------------------


def test_wwr_parse_feed():
    jobs = {j.id: j for j in parse_feed(fixture_bytes("wwr_backend.rss"), NOW_ISO)}
    d = jobs["wwr:dropbox-backend-product-software-engineer-1"]
    assert (d.company, d.title) == ("Dropbox", "Backend Product Software Engineer")
    assert d.remote_text == "Remote: Anywhere in the World"
    assert d.posted_at == "2026-10-31T07:31:12+00:00"
    v = jobs["wwr:vertican-senior-applied-ai-engineer-document-intelligence"]
    assert v.remote_text.startswith("Remote; countries: Bolivia") and "🇧" not in v.location_text
    assert "Anywhere" not in v.remote_text


def test_wwr_fetch():
    out = WWRSource().fetch(make_http(default_router()), ctx())
    assert out.status == "ok" and len(out.jobs) == 7


# --- dates ------------------------------------------------------------------------------


def test_iso_utc_variants():
    assert iso_utc("2026-11-15T22:27:27-05:00") == "2026-11-16T03:27:27+00:00"
    assert iso_utc("2026-11-02T16:01:37Z") == "2026-11-02T16:01:37+00:00"
    assert iso_utc("Thu, 19 Nov 2026 06:01:30 +0000") == "2026-11-19T06:01:30+00:00"
    assert iso_utc(1763600000) == datetime.fromtimestamp(1763600000, tz=timezone.utc).isoformat()
    assert iso_utc("not a date") is None and iso_utc(None) is None
