"""Deterministic structured-field extraction from already-cleaned resume text.

This is the FOUNDATION for the v2 upgrade (see UPGRADE_PLAN.md, Phase U1):
  - anonymization (U3) needs the name/email/phone to redact them,
  - knockout filters (U4) need years-of-experience and skills,
  - export (U5) and email (U9) need the contact address,
  - dedup (U6) keys on email/name.

Design rules (carried over from the base build):
  - Pure functions, NO MCP code, NO network, NO LLM. Just regex/heuristics.
  - NEVER raise. Every failed sub-parse yields None / [] — never an exception.
  - These operate on `record["text"]` (already NFKC-normalized, whitespace
    collapsed by extractor.normalize_text), so PDF/DOCX differences are gone.
  - Heuristic, not guaranteed. `extract_name`/`estimate_years_experience` are
    best-effort. For anonymization, over-redaction is safer than a leak, so the
    name heuristic deliberately errs toward catching a candidate line.
"""

from __future__ import annotations

import re

# --- Email ------------------------------------------------------------------

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def extract_email(text: str) -> str | None:
    """Return the first email address found, or None."""
    try:
        m = _EMAIL_RE.search(text or "")
        return m.group(0) if m else None
    except Exception:
        return None


# --- Phone ------------------------------------------------------------------

# A phone-like run: optional +, then digits/spaces/()/.- , length-bounded so we
# don't grab arbitrary number strings. We validate the DIGIT count afterwards.
_PHONE_CANDIDATE_RE = re.compile(r"\+?\d[\d\s().\-]{7,}\d")


def extract_phone(text: str) -> str | None:
    """Return the first plausible phone number, lightly normalized to '+digits'.

    Accepts US/international formats ("+1 (234) 567-8900", "234.567.8900",
    "+91 98765 43210"). Requires 10-15 digits so years/dates/zip codes don't
    masquerade as phone numbers. Returns None if nothing qualifies."""
    try:
        for m in _PHONE_CANDIDATE_RE.finditer(text or ""):
            raw = m.group(0)
            has_plus = raw.lstrip().startswith("+")
            digits = re.sub(r"\D", "", raw)
            if 10 <= len(digits) <= 15:
                return ("+" + digits) if has_plus else digits
        return None
    except Exception:
        return None


# --- Links ------------------------------------------------------------------

_LINKEDIN_RE = re.compile(r"(?:https?://)?(?:www\.)?linkedin\.com/[^\s)]+", re.I)
_GITHUB_RE = re.compile(r"(?:https?://)?(?:www\.)?github\.com/[^\s)]+", re.I)
_URL_RE = re.compile(r"https?://[^\s)]+", re.I)


def extract_links(text: str) -> dict:
    """Return {"linkedin", "github", "portfolio"} URLs (None where absent).

    'portfolio' is the first http(s) URL that isn't LinkedIn or GitHub."""
    out: dict[str, str | None] = {"linkedin": None, "github": None, "portfolio": None}
    try:
        t = text or ""
        li = _LINKEDIN_RE.search(t)
        if li:
            out["linkedin"] = li.group(0).rstrip(".,;")
        gh = _GITHUB_RE.search(t)
        if gh:
            out["github"] = gh.group(0).rstrip(".,;")
        for m in _URL_RE.finditer(t):
            url = m.group(0)
            low = url.lower()
            if "linkedin.com" in low or "github.com" in low:
                continue
            out["portfolio"] = url.rstrip(".,;")
            break
        return out
    except Exception:
        return out


# --- Name (heuristic) -------------------------------------------------------

# Words that signal a header line, not a person's name.
_NAME_STOPWORDS = {
    "resume", "curriculum", "vitae", "cv", "profile", "contact",
    "summary", "objective", "name", "address", "phone", "email",
}
# A name token: letters (incl. accents) plus optional . - ' for initials/hyphens.
_NAME_TOKEN_RE = re.compile(r"^[^\W\d_][\w'.\-]*$", re.UNICODE)
# Lowercase particles that legitimately appear inside names (van Gogh, de Souza).
_NAME_PARTICLES = {
    "de", "van", "von", "der", "den", "da", "di", "del", "della", "la", "le",
    "bin", "al", "dos", "das", "du", "st", "mac", "mc",
}


def _looks_like_name_token(tok: str) -> bool:
    """A name token is well-formed AND either Capitalized/ALLCAPS or a known
    lowercase particle. This rejects title lines like 'Python developer' (where
    'developer' is lowercase and not a particle) while keeping real names."""
    if not _NAME_TOKEN_RE.match(tok):
        return False
    return tok[0].isupper() or tok.lower() in _NAME_PARTICLES


def extract_name(text: str) -> str | None:
    """Best-effort candidate name from the first few lines. Heuristic, not exact.

    Looks at the opening lines for one that reads like a 2-4 token proper name
    (no digits, no @, not a section header). Returns None if none qualifies.
    Used by anonymization, so it errs toward catching the candidate line."""
    try:
        lines = [ln.strip() for ln in (text or "").split("\n")]
        for line in lines[:6]:
            if not line or len(line) > 60:
                continue
            low = line.lower()
            if "@" in line or any(ch.isdigit() for ch in line):
                continue
            if any(w in low for w in _NAME_STOPWORDS):
                continue
            tokens = line.split()
            if not (2 <= len(tokens) <= 4):
                continue
            if all(_looks_like_name_token(tok) for tok in tokens):
                return line
        return None
    except Exception:
        return None


# --- Years of experience (heuristic) ----------------------------------------

_EXPLICIT_YEARS_RE = re.compile(
    r"(\d{1,2}(?:\.\d)?)\s*\+?\s*(?:years?|yrs?)\b", re.I
)
_YEAR_RE = re.compile(r"\b(19[5-9]\d|20[0-4]\d)\b")
_PRESENT_RE = re.compile(r"\b(present|current|now|till date|to date)\b", re.I)
# A current-year ceiling so "Present" date ranges don't need datetime (keeps the
# function deterministic and testable). Resumes spanning past this still work.
_CURRENT_YEAR = 2026


def estimate_years_experience(text: str) -> float | None:
    """Estimate total years of experience. Heuristic — not authoritative.

    Strategy: (1) prefer the largest explicit "N years"/"N yrs" mention (capped
    at 50 to ignore typos); (2) else infer a career span from the earliest to
    the latest 4-digit year present (treating 'present'/'current' as the current
    year). Returns None when neither signal exists."""
    try:
        t = text or ""
        explicit = [
            float(m.group(1))
            for m in _EXPLICIT_YEARS_RE.finditer(t)
        ]
        explicit = [y for y in explicit if 0 < y <= 50]
        if explicit:
            return max(explicit)

        years = [int(m.group(0)) for m in _YEAR_RE.finditer(t)]
        if _PRESENT_RE.search(t):
            years.append(_CURRENT_YEAR)
        if len(years) >= 2:
            span = max(years) - min(years)
            if span > 0:
                return float(span)
        return None
    except Exception:
        return None


# --- Skills (vocabulary match) ----------------------------------------------


def extract_skills(text: str, skill_vocab: list[str]) -> list[str]:
    """Return the subset of `skill_vocab` that appears in the text.

    Case-insensitive. For ordinary alphanumeric skills a word-boundary match is
    used ('go' won't match 'good'); for skills with symbols (C++, C#, .NET) a
    plain substring match is used since \\b doesn't bound them. Order follows the
    vocabulary; duplicates removed. Deterministic."""
    if not text or not skill_vocab:
        return []
    found: list[str] = []
    seen: set[str] = set()
    low_text = text.lower()
    for skill in skill_vocab:
        if not skill:
            continue
        key = skill.lower()
        if key in seen:
            continue
        try:
            if re.fullmatch(r"[A-Za-z0-9 ]+", skill):
                hit = re.search(rf"\b{re.escape(key)}\b", low_text) is not None
            else:
                hit = key in low_text
        except Exception:
            hit = key in low_text
        if hit:
            found.append(skill)
            seen.add(key)
    return found


# --- Single entry point -----------------------------------------------------


def parse_fields(text: str, skill_vocab: list[str] | None = None) -> dict:
    """Parse all structured fields from clean resume text. Never raises.

    `skill_vocab` is optional: pass the JD-derived skills to populate
    `skills_found`; omit it (the default) for cheap contact/identity extraction
    where skills aren't needed yet."""
    return {
        "name": extract_name(text),
        "email": extract_email(text),
        "phone": extract_phone(text),
        "links": extract_links(text),
        "years_experience": estimate_years_experience(text),
        "skills_found": extract_skills(text, skill_vocab or []),
    }
