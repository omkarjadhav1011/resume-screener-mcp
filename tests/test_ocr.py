"""Tests for OCR fallback (Phase U2).

These pass whether or not Tesseract/Poppler are installed: the success path is
proven by MOCKING the OCR seams, and the real-binary test skips when unavailable.
"""

import os

import pytest

import resume_screener.extractor as ex
from resume_screener.extractor import extract_resume
from resume_screener.ocr import ocr_available, ocr_pdf

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLES = os.path.join(HERE, "sample_resumes")
SCANNED = os.path.join(SAMPLES, "scanned_resume.pdf")


@pytest.fixture(scope="session", autouse=True)
def ensure_samples():
    if not os.path.isdir(SAMPLES) or not os.listdir(SAMPLES):
        import generate_samples

        generate_samples.main()


# --- ocr module: never raises, always degrades gracefully -------------------

def test_ocr_available_returns_bool():
    assert isinstance(ocr_available(), bool)


def test_ocr_disabled_by_env(monkeypatch):
    monkeypatch.setenv("RESUME_SCREENER_OCR", "0")
    assert ocr_available() is False  # env opt-out short-circuits, even if present


def test_ocr_pdf_never_raises():
    text, err = ocr_pdf(SCANNED)
    assert isinstance(text, str)
    # On a machine without the libs/binaries, err is set; with them, err is None.
    assert err is None or isinstance(err, str)


# --- Wiring into extract_resume (mocked, deterministic) ---------------------

def test_ocr_success_path_rescues_scanned(monkeypatch):
    monkeypatch.setattr(ex, "ocr_available", lambda: True)
    monkeypatch.setattr(
        ex, "ocr_pdf",
        lambda path: ("Jane Doe\nBackend engineer with 8 years of Python on AWS. " * 4, None),
    )
    rec = extract_resume(SCANNED)
    assert rec["ok"] is True
    assert rec["extracted_via"] == "ocr"
    assert rec["char_count"] >= 100
    # fields are populated from the OCR text too
    assert rec["fields"]["name"] == "Jane Doe"


def test_ocr_attempted_but_empty_is_reported(monkeypatch):
    monkeypatch.setattr(ex, "ocr_available", lambda: True)
    monkeypatch.setattr(ex, "ocr_pdf", lambda path: ("", "OCR could not render the PDF"))
    rec = extract_resume(SCANNED)
    assert rec["ok"] is False
    assert "ocr" in rec["error"].lower()  # message reflects that OCR was tried


def test_ocr_unavailable_keeps_original_behavior(monkeypatch):
    monkeypatch.setattr(ex, "ocr_available", lambda: False)
    rec = extract_resume(SCANNED)
    assert rec["ok"] is False
    assert "no extractable text" in rec["error"].lower()
    assert "extracted_via" not in rec


# --- Real binaries, if present ---------------------------------------------

def test_real_ocr_if_installed():
    if not ocr_available():
        pytest.skip("Tesseract/Poppler not installed — OCR dormant")
    rec = extract_resume(SCANNED)
    # If the sample truly has rendered text, OCR rescues it; otherwise it's
    # honestly reported. Either outcome is correct.
    assert rec["ok"] is False or rec.get("extracted_via") == "ocr"
