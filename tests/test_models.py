import json

import pytest

from radar.models import Job
from tests.conftest import NOW_ISO, make_job


def test_build_ids_and_fields():
    j = make_job(source_job_id=42, description_html="<p>We sponsor visas.</p><p>Salary €100k-€120k.</p>")
    assert j.id == "greenhouse:42"
    assert j.visa_text == "We sponsor visas."
    assert j.salary_text == "€100k-€120k"
    assert j.first_seen == NOW_ISO
    assert j.fingerprint == "acme|software engineer|amsterdam"


def test_id_falls_back_to_url_hash():
    j = make_job(source_job_id=None, url="https://example.com/x?utm_source=a")
    assert j.id.startswith("greenhouse:") and len(j.id.split(":")[1]) == 16
    j2 = make_job(source_job_id=None, url="https://example.com/x")
    assert j.id == j2.id  # tracking params do not change identity


def test_unknowns_stay_null():
    j = make_job()
    assert j.salary_text is None and j.visa_text is None and j.excerpt is None and j.posted_at is None


def test_description_never_serialized():
    j = make_job(description_text="secret long description")
    d = j.inbox_dict()
    assert "description" not in d
    assert "secret long description" not in json.dumps(d)


def test_http_url_upgraded_and_non_http_rejected():
    assert make_job(url="http://example.com/a").url == "https://example.com/a"
    with pytest.raises(Exception):
        Job(id="x", source="s", company="c", title="t", url="ftp://x", first_seen=NOW_ISO, fingerprint="f")


def test_remote_text_clipped_to_120():
    j = make_job(remote_text="Remote " * 50)
    assert len(j.remote_text) <= 120


def test_salary_from_source_field_wins():
    j = make_job(salary_text="$100k", description_text="Salary €1k-€2k")
    assert j.salary_text == "$100k"
