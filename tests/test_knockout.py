"""Tests for deterministic knockout filters (Phase U4) — unit + server."""

import os

import pytest

from resume_screener.fields import parse_fields
from resume_screener.knockout import apply_knockouts
from resume_screener.server import screen_resumes, screen_resumes_bulk

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLES = os.path.join(HERE, "sample_resumes")
JD = "Senior Backend Engineer: Python, FastAPI, AWS, microservices, PostgreSQL."


@pytest.fixture(scope="session", autouse=True)
def ensure_samples():
    if not os.path.isdir(SAMPLES) or not os.listdir(SAMPLES):
        import generate_samples

        generate_samples.main()


def _rec(text: str) -> dict:
    return {"filename": "x.pdf", "text": text, "fields": parse_fields(text)}


# --- Unit: apply_knockouts --------------------------------------------------

def test_min_years_fails_below_bar():
    r = _rec("Backend engineer with 3 years of Python experience.")
    ok, reasons, warns = apply_knockouts(r, {"min_years_experience": 5})
    assert ok is False
    assert any("years" in x for x in reasons)


def test_min_years_passes_at_or_above_bar():
    r = _rec("Backend engineer, 8 years of Python and AWS.")
    ok, reasons, _ = apply_knockouts(r, {"min_years_experience": 5})
    assert ok is True and reasons == []


def test_min_years_indeterminate_passes_with_warning():
    # No parseable years -> NOT dropped (never silently lose a resume), warned.
    r = _rec("Backend engineer who loves Python.")
    ok, reasons, warns = apply_knockouts(r, {"min_years_experience": 5})
    assert ok is True and reasons == []
    assert warns and "determine" in warns[0].lower()


def test_required_skills_all_must_appear():
    r = _rec("10 years. Python and AWS expert.")
    ok, reasons, _ = apply_knockouts(r, {"required_skills": ["Python", "Kubernetes"]})
    assert ok is False and "Kubernetes" in reasons[0]
    ok2, _, _ = apply_knockouts(r, {"required_skills": ["Python", "AWS"]})
    assert ok2 is True


def test_any_of_skills():
    r = _rec("React frontend developer, 6 years.")
    ok, reasons, _ = apply_knockouts(r, {"any_of_skills": ["Python", "Go", "Rust"]})
    assert ok is False and "none of" in reasons[0].lower()
    ok2, _, _ = apply_knockouts(
        _rec("Go and Rust systems dev, 6 years."),
        {"any_of_skills": ["Python", "Go"]},
    )
    assert ok2 is True


def test_no_rules_passes():
    ok, reasons, warns = apply_knockouts(_rec("anything at all"), {})
    assert ok is True and reasons == [] and warns == []


def test_multiple_rules_accumulate_reasons():
    r = _rec("Junior dev, 1 year, HTML and CSS.")
    ok, reasons, _ = apply_knockouts(
        r, {"min_years_experience": 5, "required_skills": ["Python"]}
    )
    assert ok is False and len(reasons) == 2


# --- Server integration -----------------------------------------------------

def test_screen_knockouts_partition():
    out = screen_resumes(SAMPLES, JD, top_k=5, knockouts={"min_years_experience": 6})
    assert out["ok"] and "knocked_out" in out
    ko_names = {k["filename"] for k in out["knocked_out"]}
    # frank_junior has ~1 year -> knocked out
    assert "frank_junior.pdf" in ko_names
    cand_names = {c["filename"] for c in out["candidates"]}
    assert cand_names.isdisjoint(ko_names)  # knocked-out never in the packet
    # each knocked-out entry carries a reason
    assert all(k["reasons"] for k in out["knocked_out"])


def test_bulk_knockouts_required_skill():
    out = screen_resumes_bulk(
        SAMPLES, JD, shortlist_size=9, knockouts={"required_skills": ["Python"]}
    )
    ko_names = {k["filename"] for k in out["knocked_out"]}
    # the nurse resume has no Python -> knocked out
    assert "grace_nurse.pdf" in ko_names
    # every surviving candidate is absent from the knocked-out set
    cand_names = {c["filename"] for c in out["candidates"]}
    assert cand_names.isdisjoint(ko_names)


def test_all_knocked_out_is_graceful():
    out = screen_resumes(SAMPLES, JD, knockouts={"min_years_experience": 99})
    assert out["ok"] and out["candidate_count"] == 0
    assert out["candidates"] == []
    assert len(out["knocked_out"]) >= 8  # everyone with a parseable year


def test_knockouts_deterministic():
    a = screen_resumes(SAMPLES, JD, knockouts={"min_years_experience": 6})
    b = screen_resumes(SAMPLES, JD, knockouts={"min_years_experience": 6})
    assert a == b


def test_no_knockouts_no_keys():
    out = screen_resumes(SAMPLES, JD, top_k=5)
    assert "knocked_out" not in out and "knockout_warnings" not in out
