"""Tests for structured field extraction (Phase U1). Pure, deterministic."""

from resume_screener.fields import (
    estimate_years_experience,
    extract_email,
    extract_links,
    extract_name,
    extract_phone,
    extract_skills,
    parse_fields,
)


# --- Email ------------------------------------------------------------------

def test_email_with_plus_tag():
    assert extract_email("reach me at john.doe+jobs@example.com today") == (
        "john.doe+jobs@example.com"
    )


def test_email_none_when_absent():
    assert extract_email("no address here") is None


# --- Phone ------------------------------------------------------------------

def test_phone_us_formatted():
    assert extract_phone("Call +1 (234) 567-8900 anytime") == "+12345678900"


def test_phone_dotted():
    assert extract_phone("234.567.8900") == "2345678900"


def test_phone_rejects_short_number():
    # A year or zip code must not be read as a phone number.
    assert extract_phone("graduated 2019, zip 90210") is None


# --- Links ------------------------------------------------------------------

def test_links_linkedin_github_portfolio():
    text = (
        "Portfolio https://jane.dev | https://www.linkedin.com/in/jane-doe | "
        "github.com/janedoe"
    )
    links = extract_links(text)
    assert "linkedin.com/in/jane-doe" in links["linkedin"]
    assert "github.com/janedoe" in links["github"]
    assert links["portfolio"] == "https://jane.dev"


def test_links_absent():
    links = extract_links("plain text resume")
    assert links == {"linkedin": None, "github": None, "portfolio": None}


# --- Name (heuristic) -------------------------------------------------------

def test_name_from_first_line():
    text = "Jane Doe\nSenior Backend Engineer\njane@example.com"
    assert extract_name(text) == "Jane Doe"


def test_name_skips_header_word():
    text = "CURRICULUM VITAE\nMaria Garcia Lopez\nsoftware engineer"
    assert extract_name(text) == "Maria Garcia Lopez"


def test_name_none_when_first_line_is_email():
    text = "jane@example.com\n+1 234 567 8900\nPython developer"
    assert extract_name(text) is None


# --- Years of experience (heuristic) ----------------------------------------

def test_years_explicit():
    assert estimate_years_experience("7 years of backend experience") == 7.0


def test_years_explicit_with_plus():
    assert estimate_years_experience("5+ years building APIs") == 5.0


def test_years_prefers_largest_explicit():
    assert estimate_years_experience("3 years at A, then 8 yrs at B") == 8.0


def test_years_inferred_from_date_range():
    # No explicit "N years" — fall back to span of the years present.
    assert estimate_years_experience("Engineer 2018 - 2023, Intern 2017") == 6.0


def test_years_none_when_no_signal():
    assert estimate_years_experience("backend engineer, loves Python") is None


# --- Skills -----------------------------------------------------------------

VOCAB = ["Python", "Go", "Rust", "C++", ".NET", "Kubernetes"]


def test_skills_word_boundary_hit_and_miss():
    found = extract_skills("Python and Go developer", VOCAB)
    assert found == ["Python", "Go"]


def test_skills_go_does_not_match_substring():
    # 'Go' must not match inside 'good' or 'Google'.
    assert "Go" not in extract_skills("Good engineer at Google", VOCAB)


def test_skills_symbol_skills_substring():
    found = extract_skills("Built services in C++ and .NET", VOCAB)
    assert "C++" in found and ".NET" in found


def test_skills_empty_vocab():
    assert extract_skills("Python Rust", []) == []


# --- parse_fields integration ----------------------------------------------

def test_parse_fields_full():
    text = (
        "Jane Doe\n"
        "Senior Backend Engineer\n"
        "jane.doe@example.com | +1 (234) 567-8900\n"
        "linkedin.com/in/jane-doe\n"
        "8 years of experience in Python, AWS, Kubernetes."
    )
    fields = parse_fields(text, skill_vocab=["Python", "AWS", "Kubernetes", "Go"])
    assert fields["name"] == "Jane Doe"
    assert fields["email"] == "jane.doe@example.com"
    assert fields["phone"] == "+12345678900"
    assert "linkedin.com/in/jane-doe" in fields["links"]["linkedin"]
    assert fields["years_experience"] == 8.0
    assert fields["skills_found"] == ["Python", "AWS", "Kubernetes"]


def test_parse_fields_empty_text_no_raise():
    fields = parse_fields("")
    assert fields["name"] is None
    assert fields["email"] is None
    assert fields["skills_found"] == []
