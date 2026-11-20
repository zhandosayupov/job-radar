"""RFC 9309 robots.txt matching, including the wildcard rules the stdlib parser ignores."""
import urllib.robotparser

import pytest

from radar.robots import Robots, parse
from tests.conftest import Router, make_http

# Excerpt of hh.kz/robots.txt as served in Nov 2026 (structure kept, lists shortened).
HH = """
User-agent: Yandex
User-agent: Googlebot
Allow: /vacancy/*
Disallow: *?*

User-agent: GPTBot
User-agent: ClaudeBot
User-agent: Anthropic-ai
Disallow: /vacancy/*
Disallow: /resume$

User-agent: *
Allow: *?u*
Allow: *?currencyCode*
Allow: *?vacancyId*
Disallow: /auth/*
Disallow: /rss/*
Disallow: *?*
Disallow: /resume$
Disallow: /resume?*

Host: https://hh.kz
Sitemap: https://hh.kz/sitemap/main.xml
"""
SEARCH = "https://almaty.hh.kz/search/vacancy?ored_clusters=true&search_period=1&text=C%2B%2B&search_field=name"


def test_hh_search_with_query_is_disallowed():
    assert Robots(HH, "job-radar").can_fetch(SEARCH) is False


def test_stdlib_parser_gets_this_wrong():
    """Why radar.robots exists: urllib.robotparser ignores `*` and would allow the search."""
    rp = urllib.robotparser.RobotFileParser()
    rp.parse(HH.splitlines())
    assert rp.can_fetch("job-radar", SEARCH) is True


@pytest.mark.parametrize(
    "url,allowed",
    [
        ("https://hh.kz/search/vacancy?text=go", False),     # Disallow: *?*
        ("https://hh.kz/search/vacancy", True),               # no query, no rule
        ("https://hh.kz/vacancy/123", True),                  # * group has no /vacancy rule
        ("https://hh.kz/vacancy/123?vacancyId=1", True),      # Allow *?vacancyId* is longer than *?*
        ("https://hh.kz/resume", False),                      # $ anchor
        ("https://hh.kz/resume/abc", True),                   # ... only exact /resume
        ("https://hh.kz/resume?x=1", False),
        ("https://hh.kz/rss/vacancies", False),
        ("https://hh.kz/robots.txt", True),                   # always allowed
    ],
)
def test_hh_star_group(url, allowed):
    assert Robots(HH, "job-radar").can_fetch(url) is allowed


def test_specific_group_beats_star_and_lists_share_rules():
    r = Robots(HH, "ClaudeBot")
    assert r.can_fetch("https://hh.kz/vacancy/123") is False
    assert r.can_fetch("https://hh.kz/search/vacancy?text=x") is True  # its group has no *?* rule
    assert Robots(HH, "anthropic-ai").can_fetch("https://hh.kz/vacancy/1") is False  # case-insensitive


def test_wwr_wildcard_rule():
    wwr = "User-agent: *\nAllow: /\nDisallow: /admin/\nDisallow: /*edit?token=/\n"
    r = Robots(wwr, "job-radar")
    assert r.can_fetch("https://weworkremotely.com/categories/remote-back-end-programming-jobs.rss")
    assert not r.can_fetch("https://weworkremotely.com/jobs/1/edit?token=/abc")
    assert not r.can_fetch("https://weworkremotely.com/admin/x")


def test_longest_match_and_tie_goes_to_allow():
    r = Robots("User-agent: *\nDisallow: /\nAllow: /jobs\nDisallow: /jobs/private\n", "x")
    assert r.can_fetch("https://a.io/jobs/1")
    assert not r.can_fetch("https://a.io/jobs/private/1")
    assert not r.can_fetch("https://a.io/other")
    tie = Robots("User-agent: *\nDisallow: /page\nAllow: /page\n", "x")
    assert tie.can_fetch("https://a.io/page")


def test_empty_disallow_comments_and_no_matching_group():
    assert Robots("User-agent: *\nDisallow:\n", "x").can_fetch("https://a.io/anything")
    assert Robots("# hi\nUser-agent: * # all\nDisallow: /p # private\n", "x").can_fetch("https://a.io/p") is False
    assert Robots("User-agent: other\nDisallow: /\n", "x").can_fetch("https://a.io/") is True


def test_groups_for_same_agent_are_merged():
    txt = "User-agent: x\nDisallow: /a\n\nUser-agent: y\nDisallow: /\n\nUser-agent: x\nDisallow: /b\n"
    r = Robots(txt, "x")
    assert not r.can_fetch("https://a.io/a") and not r.can_fetch("https://a.io/b") and r.can_fetch("https://a.io/c")


def test_percent_encoding_is_normalized():
    r = Robots("User-agent: *\nDisallow: /search/C++\n", "x")
    assert not r.can_fetch("https://a.io/search/C%2B%2B")


def test_crawl_delay_parsed_and_grouping():
    groups = parse("User-agent: a\nUser-agent: b\nCrawl-delay: 5\nDisallow: /x\n")
    assert len(groups) == 1 and groups[0].agents == ["a", "b"] and groups[0].crawl_delay == 5.0
    assert Robots("User-agent: *\nCrawl-delay: 3\n", "x").crawl_delay == 3.0


def test_http_client_honors_crawl_delay():
    r = Router()
    r.add("https://slow.example.com/robots.txt", content=b"User-agent: *\nCrawl-delay: 4\n")
    r.add("https://slow.example.com/a", json_body={})
    t = {"now": 0.0}
    sleeps = []

    def sleep(s):
        sleeps.append(round(s, 2))
        t["now"] += s

    http = make_http(r, sleep=sleep, clock=lambda: t["now"])
    http.get("https://slow.example.com/a")
    http.get("https://slow.example.com/a")
    assert sleeps.count(4.0) == 2  # robots->first page and first->second page both wait 4s


def test_http_client_blocks_hh_search():
    r = Router()
    r.add("https://almaty.hh.kz/robots.txt", content=HH.encode())
    r.add("https://almaty.hh.kz/search/vacancy", content=b"<html></html>")
    from radar.http import Blocked

    with pytest.raises(Blocked, match="robots"):
        make_http(r).get("https://almaty.hh.kz/search/vacancy", params={"text": "C++"})
    assert [c.url.path for c in r.calls] == ["/robots.txt"]
