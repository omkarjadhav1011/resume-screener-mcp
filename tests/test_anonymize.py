"""Tests for anonymization (Phase U3) — unit + server-level."""

import os

import pytest

from resume_screener.anonymize import anonymize_text
from resume_screener.fields import parse_fields
from resume_screener.server import (
    compare_candidates,
    screen_resumes,
    screen_resumes_bulk,
)

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLES = os.path.join(HERE, "sample_resumes")
JD = "Senior Backend Engineer: Python, FastAPI, AWS, microservices, PostgreSQL."


@pytest.fixture(scope="session", autouse=True)
def ensure_samples():
    if not os.path.isdir(SAMPLES) or not os.listdir(SAMPLES):
        import generate_samples

        generate_samples.main()


# --- Unit: anonymize_text ---------------------------------------------------

SAMPLE_TEXT = (
    "Jane Doe\n"
    "Senior Engineer\n"
    "jane.doe@example.com | +1 (234) 567-8900\n"
    "linkedin.com/in/jane-doe\n"
    "B.S. Stanford University, 2015.\n"
    "Python and AWS expert. Jane led the platform team."
)


def test_anonymize_removes_direct_identifiers():
    fields = parse_fields(SAMPLE_TEXT)
    red, counts = anonymize_text(SAMPLE_TEXT, fields)
    # Name (full + token) gone
    assert "Jane" not in red and "Doe" not in red
    # Email gone
    assert "jane.doe@example.com" not in red and "@example.com" not in red
    # Phone gone
    assert "567-8900" not in red
    # Link gone
    assert "linkedin.com/in/jane-doe" not in red
    # School gone
    assert "Stanford" not in red
    # Placeholders present
    for tag in ("[CANDIDATE]", "[EMAIL]", "[PHONE]", "[LINK]", "[SCHOOL]"):
        assert tag in red
    # counts tally what happened
    assert counts["email"] == 1 and counts["phone"] == 1
    assert counts["name"] >= 1 and counts["link"] >= 1 and counts["school"] >= 1


def test_anonymize_preserves_skill_signal():
    # The whole point: identity goes, MERIT stays.
    fields = parse_fields(SAMPLE_TEXT)
    red, _ = anonymize_text(SAMPLE_TEXT, fields)
    assert "Python" in red and "AWS" in red


def test_anonymize_empty_text_safe():
    red, counts = anonymize_text("", {})
    assert red == ""
    assert counts == {"name": 0, "email": 0, "phone": 0, "link": 0, "school": 0}


# --- Server: opt-in behavior ------------------------------------------------

def test_screen_anonymized_packet():
    out = screen_resumes(SAMPLES, JD, top_k=5, anonymize=True)
    assert out["ok"] and out["anonymized"] is True
    assert out["fairness_note"]
    # opaque ids everywhere, real names nowhere
    assert all(c["filename"].startswith("candidate_") for c in out["candidates"])
    assert all("@example.com" not in c["text"] for c in out["candidates"])
    # id_map round-trips opaque id -> real filename
    assert all(c["filename"] in out["id_map"] for c in out["candidates"])
    real = set(out["id_map"].values())
    assert "alice_backend.pdf" in real


def test_bulk_anonymized_keeps_prefilter_score():
    out = screen_resumes_bulk(SAMPLES, JD, top_k=5, shortlist_size=5, anonymize=True)
    assert out["anonymized"] is True
    assert all(c["filename"].startswith("candidate_") for c in out["candidates"])
    assert all("prefilter_score" in c for c in out["candidates"])


def test_compare_anonymized():
    out = compare_candidates(
        SAMPLES, ["alice_backend.pdf", "henry_principal.pdf"], JD, anonymize=True
    )
    assert out["anonymized"] is True
    assert len(out["id_map"]) == 2
    assert all(c["filename"].startswith("candidate_") for c in out["candidates"])


# --- Server: NO regression when anonymize is off (the default) --------------

def test_plain_mode_unchanged_and_no_anon_keys():
    default = screen_resumes(SAMPLES, JD, top_k=5)
    explicit_off = screen_resumes(SAMPLES, JD, top_k=5, anonymize=False)
    assert default == explicit_off
    # No anonymization metadata leaks into a plain packet
    for key in ("anonymized", "id_map", "fairness_note"):
        assert key not in default
    # Real filenames preserved
    assert any(c["filename"] == "alice_backend.pdf" for c in default["candidates"])
    assert all("filename" in c and "text" in c for c in default["candidates"])
