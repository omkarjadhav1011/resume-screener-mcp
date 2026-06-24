"""Robustness tests for the extractor.

Run with:  uv run python -m pytest -q
These regenerate the sample resumes if needed, then prove each failure mode
produces a structured record (never an exception).
"""

import os

import pytest

from resume_screener.extractor import (
    extract_resume,
    find_resume_files,
    normalize_text,
)

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLES = os.path.join(HERE, "sample_resumes")


@pytest.fixture(scope="session", autouse=True)
def ensure_samples():
    if not os.path.isdir(SAMPLES) or not os.listdir(SAMPLES):
        import generate_samples

        generate_samples.main()


def _path(name: str) -> str:
    return os.path.join(SAMPLES, name)


def test_good_pdf_ok():
    rec = extract_resume(_path("alice_backend.pdf"))
    assert rec["ok"] is True
    assert rec["error"] is None
    assert rec["char_count"] > 100
    assert "Python" in rec["text"]


def test_docx_table_captured():
    rec = extract_resume(_path("priya_backend.docx"))
    assert rec["ok"] is True
    # Skills live in a table; if tables were skipped these would be missing.
    assert "Terraform" in rec["text"]
    assert "PostgreSQL" in rec["text"]


def test_scanned_pdf_reported_not_dropped():
    from resume_screener.ocr import ocr_available

    rec = extract_resume(_path("scanned_resume.pdf"))
    if ocr_available():
        # OCR may rescue it (extracted_via=ocr); if not, it's reported, never dropped.
        assert rec["ok"] or "no extractable text" in rec["error"].lower()
    else:
        assert rec["ok"] is False
        assert "scanned" in rec["error"].lower() or "no extractable text" in rec["error"].lower()


def test_legacy_doc_reported():
    rec = extract_resume(_path("old_resume.doc"))
    assert rec["ok"] is False
    assert "legacy .doc" in rec["error"].lower()


def test_nonexistent_path_no_exception():
    rec = extract_resume(_path("does_not_exist.pdf"))
    assert rec["ok"] is False
    assert rec["error"]  # some reason, but no raise


def test_normalize_collapses_whitespace():
    out = normalize_text("Hello   \t world\n\n\n\nfoo\x00bar")
    assert "  " not in out
    assert "\x00" not in out
    assert "Hello world" in out


def test_find_resume_files_lists_and_separates_doc():
    resumes, legacy, error = find_resume_files(SAMPLES)
    assert error is None
    assert any(p.endswith(".pdf") for p in resumes)
    assert any(p.endswith(".docx") for p in resumes)
    assert any(p.endswith(".doc") for p in legacy)


def test_find_resume_files_bad_folder():
    resumes, legacy, error = find_resume_files(_path("nope_not_here"))
    assert error is not None
    assert resumes == [] and legacy == []
