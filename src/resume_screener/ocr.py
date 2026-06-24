"""OCR fallback for scanned/image PDFs (Phase U2).

Scanned resumes have no extractable text — base extraction reports them as
unreadable. This module RESCUES them with optical character recognition, as a
best-effort fallback only when the tooling is actually available.

Design rules:
  - OCR is OPTIONAL and DORMANT by default. The server must run perfectly with no
    OCR libraries and no Tesseract/Poppler binaries installed — it just won't
    rescue scanned PDFs (the original "reported as unreadable" behavior).
  - Everything here NEVER raises. Missing libraries or binaries become a False
    from ocr_available() or a structured ("", error) from ocr_pdf().
  - Enabling OCR requires BOTH the Python extra (`pip install
    'resume-screener-mcp[ocr]'` → pytesseract, pdf2image) AND the system binaries
    Tesseract (the OCR engine) and Poppler (PDF→image rendering).
  - Opt out at runtime with RESUME_SCREENER_OCR=0 even when everything is present.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger("resume-screener")

# Cache the (expensive) availability probe; None = not yet checked.
_OCR_AVAILABLE: bool | None = None


def _ocr_disabled_by_env() -> bool:
    return os.environ.get("RESUME_SCREENER_OCR", "").strip().lower() in {
        "0", "false", "off", "no",
    }


def ocr_available() -> bool:
    """True only if OCR is enabled AND pytesseract+pdf2image import AND the
    Tesseract binary is callable. Cached after the first real probe. Never raises.

    The env opt-out is checked first (and uncached) so RESUME_SCREENER_OCR=0
    disables OCR immediately without affecting the cached binary probe."""
    if _ocr_disabled_by_env():
        return False

    global _OCR_AVAILABLE
    if _OCR_AVAILABLE is not None:
        return _OCR_AVAILABLE

    available = False
    try:
        import pytesseract  # noqa: F401
        import pdf2image  # noqa: F401

        # Importing the wrappers isn't enough — confirm the engine binary runs.
        pytesseract.get_tesseract_version()
        available = True
    except Exception as exc:  # ImportError, TesseractNotFoundError, etc.
        log.info("OCR unavailable: %s", type(exc).__name__)
        available = False

    _OCR_AVAILABLE = available
    return available


def ocr_pdf(path: str) -> tuple[str, str | None]:
    """Render a PDF's pages to images and OCR them. Returns (text, error).

    Never raises: a missing library, a missing Poppler binary, or a per-page
    failure becomes a structured ("", reason)."""
    try:
        import pytesseract
        from pdf2image import convert_from_path
    except Exception as exc:
        return "", f"OCR libraries unavailable ({type(exc).__name__})"

    try:
        images = convert_from_path(path)
    except Exception as exc:
        # Almost always Poppler missing or an unreadable/corrupt PDF.
        return "", f"OCR could not render the PDF — is Poppler installed? ({type(exc).__name__})"

    parts: list[str] = []
    for image in images:
        try:
            parts.append(pytesseract.image_to_string(image) or "")
        except Exception:
            # One bad page shouldn't sink the whole document.
            continue
    return "\n".join(parts), None
