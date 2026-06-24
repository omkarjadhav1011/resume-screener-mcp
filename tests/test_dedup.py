"""Tests for duplicate detection (Phase U6) — unit + server."""

import os
import shutil

import pytest

from resume_screener.dedup import find_duplicates
from resume_screener.server import find_duplicate_candidates, list_resumes

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLES = os.path.join(HERE, "sample_resumes")


@pytest.fixture(scope="session", autouse=True)
def ensure_samples():
    if not os.path.isdir(SAMPLES) or not os.listdir(SAMPLES):
        import generate_samples

        generate_samples.main()


def _rec(filename, text, email=None):
    return {"filename": filename, "text": text, "fields": {"email": email}}


# --- Unit: find_duplicates --------------------------------------------------

def test_identical_text_grouped():
    body = "Backend engineer with 8 years of Python and AWS experience. " * 8
    groups = find_duplicates([_rec("a.pdf", body), _rec("b.pdf", body)])
    assert len(groups) == 1
    assert groups[0]["members"] == ["a.pdf", "b.pdf"]
    assert "identical" in groups[0]["reason"]


def test_same_email_different_text_grouped():
    groups = find_duplicates([
        _rec("a.pdf", "Alice the backend engineer " * 8, "alice@x.com"),
        _rec("b.pdf", "Completely unrelated content about gardening " * 8, "alice@x.com"),
    ])
    assert len(groups) == 1
    assert "same email" in groups[0]["reason"]


def test_near_identical_text_grouped():
    base = "Backend engineer with 8 years of Python and AWS building microservices. " * 8
    near = base.replace("8 years", "9 years")
    groups = find_duplicates([_rec("a.pdf", base), _rec("b.pdf", near)])
    assert len(groups) == 1
    assert "near-identical" in groups[0]["reason"]


def test_distinct_records_not_grouped():
    groups = find_duplicates([
        _rec("a.pdf", "Backend engineer Python AWS Kubernetes microservices " * 8),
        _rec("b.pdf", "Registered nurse ICU triage patient care Epic EHR " * 8),
    ])
    assert groups == []


def test_single_record_no_groups():
    assert find_duplicates([_rec("a.pdf", "anything")]) == []


# --- Server -----------------------------------------------------------------

def test_list_resumes_distinct_samples_no_dupes():
    out = list_resumes(SAMPLES)
    # The 9 sample resumes are all distinct people.
    assert out["duplicate_groups"] == []


def test_find_duplicate_candidates_on_copies(tmp_path):
    d = tmp_path / "dupes"
    d.mkdir()
    # Same resume under two names -> duplicate; a third distinct resume -> not.
    shutil.copy(os.path.join(SAMPLES, "alice_backend.pdf"), d / "alice_v1.pdf")
    shutil.copy(os.path.join(SAMPLES, "alice_backend.pdf"), d / "alice_v2.pdf")
    shutil.copy(os.path.join(SAMPLES, "henry_principal.pdf"), d / "henry.pdf")
    out = find_duplicate_candidates(str(d))
    assert out["ok"] and out["group_count"] == 1
    members = set(out["duplicate_groups"][0]["members"])
    assert members == {"alice_v1.pdf", "alice_v2.pdf"}


def test_find_duplicate_candidates_bad_folder():
    out = find_duplicate_candidates(os.path.join(SAMPLES, "nope"))
    assert out["ok"] is False
