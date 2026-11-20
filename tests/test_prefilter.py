"""Table-driven prefilter tests: every rule has at least one 'drops' and one 'keeps' case."""
import pytest

from radar.prefilter import Prefilter, visa_positive
from tests.conftest import make_job

K = None  # expected rule None == keep

CASES = [
    # --- titles ---
    ("Software Engineer", "Amsterdam", None, "", K),
    ("C++ Developer", "London", None, "", K),
    ("Software Engineering Intern", "London", None, "", "title_excluded"),
    ("Staff Software Engineer", "London", None, "", "title_excluded"),
    ("Member of Technical Staff", "London", None, "", K),
    ("Principal Engineer", "London", None, "", "title_excluded"),
    ("Engineering Manager", "London", None, "", "title_excluded"),
    ("Head of Infrastructure", "London", None, "", "title_excluded"),
    ("Quant Trader", "London", None, "", "title_excluded"),
    ("Trading Systems Engineer", "London", None, "", K),
    ("Quant Developer", "London", None, "", K),
    ("Graduate Software Engineer", "London", None, "", "title_excluded"),
    ("Graduate or Experienced Software Engineer", "London", None, "", K),
    ("HR Business Partner", "London", None, "", "title_excluded"),
    ("Sales Engineer", "London", None, "", "title_excluded"),
    ("Стажёр-разработчик", "Алматы", None, "", "title_excluded"),
    ("Ведущий разработчик C++", "Алматы", None, "", K),
    ("Инженер-программист", "Удалённо", None, "", K),
    ("Product Designer", "London", None, "", "title_not_eng"),
    ("Data Scientist", "London", None, "", "title_not_eng"),
    ("Low Latency C++ Developer", "Dubai", None, "", K),
    # --- always-drop text ---
    ("Software Engineer", "London", None, "Candidates must already have the right to work in the UK.", "text_excluded"),
    ("Software Engineer", "Remote", None, "Active security clearance required.", "text_excluded"),
    ("Software Engineer", "Remote", None, "Due to export rules we can only hire U.S. Persons.", "text_excluded"),
    ("Разработчик C++", "Удалённо", None, "Только для налоговых резидентов РФ.", "text_excluded"),
    # --- no sponsorship: drops onsite, keeps remote-eligible ---
    ("Software Engineer", "Lisbon, Portugal", "HYBRID", "No visa sponsorship.", "no_sponsorship"),
    ("Software Engineer", "Anywhere in the World", "Remote", "We are unable to sponsor visas.", K),
    ("Software Engineer", "Tokyo, Japan", "no visa support, US/Canada remote available", "", "no_sponsorship"),
    ("Software Engineer", None, "Remote (US only)", "We do not sponsor visas.", "no_sponsorship"),
    # --- location ---
    ("Software Engineer", "Moscow", None, "", "location_ru_by"),
    ("Software Engineer", "Москва", "гибрид", "", "location_ru_by"),
    ("Software Engineer", "Minsk, Belarus", None, "", "location_ru_by"),
    ("Software Engineer", "Moscow; Almaty", None, "", K),
    ("Software Engineer", "Chicago, United States", None, "", "location_na_au"),
    ("Software Engineer", "Austin, TX", None, "", "location_na_au"),
    ("Software Engineer", "Sydney, Australia", None, "", "location_na_au"),
    ("Software Engineer", "Toronto", None, "", "location_na_au"),
    ("Software Engineer", "New York", None, "We offer visa sponsorship for this role.", K),
    ("Software Engineer", "New York", None, "We do not offer visa sponsorship.", "no_sponsorship"),
    ("Software Engineer", "Chicago; London", None, "", K),
    ("Software Engineer", None, "REMOTE (US)", "", "location_na_au"),
    ("Software Engineer", "Mumbai, India", None, "", "location_other"),
    ("Software Engineer", "Hong Kong", None, "", "location_other"),
    ("Software Engineer", None, "Remote; countries: Bolivia, Brazil, Chile", "", "location_other"),
    ("Software Engineer", "Singapore", None, "", K),
    ("Software Engineer", "Limassol, Cyprus", None, "", K),
    ("Software Engineer", "Zug, Switzerland", None, "", K),
    ("Software Engineer", "Tbilisi, Georgia", None, "", K),
    ("Software Engineer", "Belgrade", None, "", K),
    ("Software Engineer", None, "Remote (EMEA)", "", K),
    ("Software Engineer", None, "Remote", "", K),
    ("Software Engineer", None, None, "", K),
    ("Software Engineer", "Yerevan", None, "", K),  # unknown region -> labeler decides
]


@pytest.mark.parametrize("title,loc,remote,desc,expected", CASES)
def test_rules(settings, title, loc, remote, desc, expected):
    pf = Prefilter(settings)
    job = make_job(title=title, location_text=loc, remote_text=remote, description_text=desc or None)
    d = pf.check(job)
    assert (d.rule if not d.keep else None) == expected, f"{title!r} @ {loc!r}/{remote!r}: {d}"


def test_counts_per_rule(settings):
    pf = Prefilter(settings)
    for t in ("Intern", "Manager", "Designer", "Software Engineer"):
        pf.check(make_job(title=t))
    assert pf.counts == {"title_excluded": 2, "title_not_eng": 1}


@pytest.mark.parametrize(
    "visa,expected",
    [
        ("We offer visa sponsorship.", True),
        ("Relocation package provided.", True),
        ("No visa sponsorship.", False),
        ("We are not able to sponsor visas.", False),
        ("Tokyo (no visa support)", False),
        (None, False),
    ],
)
def test_visa_positive(visa, expected):
    assert visa_positive(visa) is expected


def test_keyword_lists_come_from_settings(settings):
    import copy

    s = copy.deepcopy(settings)
    s["prefilter"]["title_drop"].append("blockchain")
    pf = Prefilter(s)
    assert not pf.check(make_job(title="Blockchain Engineer")).keep
