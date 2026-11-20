import httpx
import pytest

from radar.http import Blocked, FetchError, is_challenge_page
from tests.conftest import Router, make_http

URL = "https://api.example.com/jobs"


def test_user_agent_sent_and_json():
    r = Router()
    r.add(URL, json_body={"ok": 1})
    http = make_http(r)
    assert http.get_json(URL) == {"ok": 1}
    assert all("job-radar-test" in c.headers["user-agent"] for c in r.calls)


def test_robots_disallow_blocks_and_is_cached():
    r = Router()
    r.add("https://api.example.com/robots.txt", content=b"User-agent: *\nDisallow: /jobs\n")
    r.add(URL, json_body={})
    http = make_http(r)
    for _ in range(2):
        with pytest.raises(Blocked, match="robots"):
            http.get(URL)
    assert [c.url.path for c in r.calls] == ["/robots.txt"]  # never hit the disallowed path


def test_robots_query_param_disallow():
    r = Router()
    r.add("https://api.example.com/robots.txt", content=b"User-agent: *\nDisallow: /jobs?page=\n")
    r.add(URL, json_body={})
    http = make_http(r)
    http.get(URL, params={"q": "c++"})
    with pytest.raises(Blocked):
        http.get(URL, params={"page": "2"})


def test_robots_404_allows_all():
    r = Router()
    r.add(URL, json_body={"a": 1})
    assert make_http(r).get_json(URL) == {"a": 1}


def test_robots_unreachable_blocks():
    r = Router()
    r.add("https://api.example.com/robots.txt", status=503)
    with pytest.raises(Blocked, match="unreachable"):
        make_http(r, retries=0).get(URL)


@pytest.mark.parametrize("status", [401, 403, 429])
def test_refusals_are_blocked(status):
    r = Router()
    r.add(URL, status=status)
    with pytest.raises(Blocked, match=str(status)):
        make_http(r).get(URL)


def test_404_is_error_not_blocked():
    r = Router()
    with pytest.raises(FetchError, match="404"):
        make_http(r).get(URL)


def test_retries_5xx_then_succeeds_with_backoff():
    r = Router()
    n = {"c": 0}

    def flaky(req):
        n["c"] += 1
        return httpx.Response(502) if n["c"] < 3 else httpx.Response(200, json={"ok": True})

    r.add_fn(URL, flaky)
    sleeps = []
    http = make_http(r, sleep=sleeps.append, retries=2, backoff_s=2.0)
    assert http.get_json(URL) == {"ok": True}
    assert [s for s in sleeps if s >= 2] == [2.0, 4.0]


def test_retries_exhausted_raises_fetch_error():
    r = Router()
    r.add(URL, status=500)
    with pytest.raises(FetchError, match="500"):
        make_http(r, retries=2).get(URL)
    assert sum(1 for c in r.calls if c.url.path == "/jobs") == 3


def test_transport_error_retried_then_fetch_error():
    def boom(req):
        raise httpx.ConnectTimeout("t")

    http = make_http(Router(), respect_robots=False)
    http._client = httpx.Client(transport=httpx.MockTransport(boom))
    with pytest.raises(FetchError, match="ConnectTimeout"):
        http.get(URL)


def test_throttle_one_request_per_second_per_host():
    r = Router()
    r.add(URL, json_body={})
    r.add("https://other.example.com/x", json_body={})
    t = {"now": 100.0}
    sleeps = []

    def sleep(s):
        sleeps.append(round(s, 3))
        t["now"] += s

    http = make_http(r, sleep=sleep, clock=lambda: t["now"], respect_robots=False)
    http.get(URL)
    http.get(URL)  # same host, no time passed -> waits 1s
    http.get("https://other.example.com/x")  # other host -> no wait
    assert sleeps == [1.0]
    assert http.requests["api.example.com"] == 2


def test_challenge_page_detection():
    assert is_challenge_page("<html><title>Just a moment...</title></html>")
    assert is_challenge_page("<title>Attention Required! | Cloudflare</title>")
    normal_with_recaptcha = "<title>Senior Engineer - Acme</title><form><div class='g-recaptcha'></div></form>"
    assert not is_challenge_page(normal_with_recaptcha)


def test_challenge_page_raises_blocked():
    r = Router()
    r.add("https://site.example.com/jobs", content=b"<title>Just a moment...</title>", headers={"content-type": "text/html"})
    with pytest.raises(Blocked, match="challenge"):
        make_http(r).get("https://site.example.com/jobs")
