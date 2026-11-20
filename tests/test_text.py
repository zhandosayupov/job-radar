import pytest

from radar import text as T
from radar.excerpt import build_excerpt


def test_html_to_text_blocks_and_entities():
    assert T.html_to_text("<p>Hello <b>world</b></p><ul><li>One.</li><li>Two</li></ul>") == "Hello world\nOne.\nTwo"


def test_html_to_text_double_escaped_greenhouse():
    assert T.html_to_text("&lt;p&gt;A &amp;amp; B&lt;/p&gt;&lt;p&gt;C&lt;/p&gt;") == "A & B\nC"


def test_html_to_text_drops_scripts_and_handles_empty():
    assert T.html_to_text("<p>x</p><script>alert(1)</script>") == "x"
    assert T.html_to_text(None) == ""
    assert T.html_to_text("plain &amp; simple") == "plain & simple"


@pytest.mark.parametrize(
    "url,expected",
    [
        ("http://Example.com/jobs/1/?utm_source=x&gh_src=abc#apply", "https://example.com/jobs/1"),
        ("https://jobs.lever.co/acme/123?lever-source=LinkedIn&team=core", "https://jobs.lever.co/acme/123?team=core"),
        ("https://x.io/a?gclid=1&fbclid=2&id=7", "https://x.io/a?id=7"),
        ("https://x.io", "https://x.io/"),
    ],
)
def test_canonical_url(url, expected):
    assert T.canonical_url(url) == expected


def test_visa_text_only_relevant_sentences():
    t = "We build trading systems. We offer visa sponsorship and relocation support. Lunch is free."
    assert T.extract_visa_text(t) == "We offer visa sponsorship and relocation support."
    assert T.extract_visa_text("Nothing here.") is None
    assert "релокац" in T.extract_visa_text("Помогаем с релокацией в Белград.")


def test_visa_text_is_clipped():
    t = "Visa sponsorship available. " * 20
    assert len(T.extract_visa_text(t)) <= 200


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Pay: $160K–$220K + equity", "$160K–$220K"),
        ("Salary €25k–€35k plus meals", "€25k–€35k"),
        ("Base 90,000 - 120,000 EUR per year", "90,000 - 120,000 EUR"),
        ("Зарплата от 300 000 руб", "от 300 000 руб"),
        ("We raised $17.5M from top VCs", None),
        ("Visa + €10k Signing Bonus", None),
        ("$5k relocation budget and $150k-$180k base", "$150k-$180k"),
        ("Costs $5 a month", None),
        ("No numbers here", None),
    ],
)
def test_salary_text(text, expected):
    assert T.extract_salary_text(text) == expected


def test_detect_lang():
    assert T.detect_lang("Senior C++ Developer") == "en"
    assert T.detect_lang("Ведущий разработчик C++") == "ru"
    assert T.detect_lang("") == "en"


def test_fingerprint_normalizes_suffixes_case_and_city():
    a = T.fingerprint("IMC Trading B.V.", "C++ Software Engineer", "Amsterdam, Netherlands")
    b = T.fingerprint("imc trading", "C++  software engineer", "Amsterdam")
    assert a == b
    assert T.fingerprint("Acme", "Dev (Remote)", None) == "acme|dev|"


def test_split_sentences_handles_bullets_and_newlines():
    s = T.split_sentences("First one. Second one!\n• Third bullet\n- Fourth")
    assert s == ["First one.", "Second one!", "Third bullet", "Fourth"]


# --- excerpt ----------------------------------------------------------------------


def test_excerpt_priority_and_boilerplate():
    text = (
        "We are an equal opportunity employer. "
        "You will write low latency C++ code. "
        "We need 3+ years of experience. "
        "This role is remote within EMEA. "
        "We sponsor visas for this role. "
        "Our benefits include a gym. "
        "Salary range €90k-€120k."
    )
    e = build_excerpt(text)
    assert e.startswith("We sponsor visas for this role. This role is remote within EMEA.")
    assert "equal opportunity" not in e and "gym" not in e
    order = [e.index(x) for x in ("sponsor", "remote", "3+ years", "C++", "Salary")]
    assert order == sorted(order)


def test_excerpt_limit_and_empty():
    long = " ".join(f"Sentence {i} about C++ performance work." for i in range(100))
    assert len(build_excerpt(long)) <= 700
    assert build_excerpt(None) is None
    assert build_excerpt("Nothing relevant at all here") is None


# --- full text --------------------------------------------------------------------

from radar.excerpt import full_text  # noqa: E402


def test_full_text_keeps_content_drops_legal_boilerplate():
    t = (
        "About the role\nYou will build low-latency C++ systems.\n"
        "We offer a relocation package and visa sponsorship.\n"
        "We are an equal opportunity employer. Apply by Friday.\n"
        "Read our privacy notice before applying."
    )
    out = full_text(t)
    assert "low-latency C++" in out and "relocation package" in out  # benefits kept
    assert "equal opportunity" not in out and "privacy notice" not in out
    assert "Apply by Friday." in out  # rest of a mixed line survives
    assert out.count("\n") == 3


def test_full_text_cap_and_empty():
    assert len(full_text("word " * 5000, 8000)) <= 8000
    assert full_text(None) is None and full_text("") is None
