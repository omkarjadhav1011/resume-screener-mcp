"""Server-side anonymization (Phase U3) — the structural fairness fix.

Why this exists: Claude Code cannot un-see a name once it is in context. Telling
it to "ignore the name" is an instruction the model may still be influenced by.
The ONLY place to truly remove an identity signal is BEFORE the text reaches the
model — i.e. here, in the server. This redacts direct identifiers from the resume
text so the judging packet Claude scores contains no name/email/phone/link/school.

Honest limitation (documented, not hidden): this removes *direct* identifiers. It
does NOT remove every *indirect* signal — writing style, gendered project
language, culturally-specific phrasing can still leak. So anonymized mode
*reduces* bias risk; it does not make scoring provably bias-free.

Pure functions, no MCP, no network, no LLM. Never raise.
"""

from __future__ import annotations

import re

from resume_screener.fields import _EMAIL_RE, _PHONE_CANDIDATE_RE, _URL_RE

# Education institutions: "<Capitalized words> University/College/Institute ...".
# Catches "Stanford University", "Indian Institute of Technology". Acronym-only
# schools (e.g. "IIT Bombay") won't match — pass them via extra_terms if needed.
_SCHOOL_RE = re.compile(
    r"\b(?:[A-Z][A-Za-z.&]+\s+){0,3}"
    r"(?:University|College|Institute|Polytechnic|Academy)"
    r"(?:\s+of\s+[A-Z][A-Za-z.&]+(?:\s+[A-Za-z.&]+){0,3})?\b"
)

_EMPTY_COUNTS = {"name": 0, "email": 0, "phone": 0, "link": 0, "school": 0}


def _redact_all(text: str, pattern: re.Pattern, replacement: str, predicate=None):
    """Replace every match of `pattern` with `replacement`. Returns (text, count).

    If `predicate` is given, only matches for which predicate(match_str) is true
    are redacted (used to require a valid phone digit-count)."""
    count = 0

    def _repl(m: re.Match) -> str:
        nonlocal count
        if predicate is not None and not predicate(m.group(0)):
            return m.group(0)
        count += 1
        return replacement

    return pattern.sub(_repl, text), count


def _is_phone(s: str) -> bool:
    """A redaction candidate is a phone only if it has 10-15 digits — so years,
    zip codes, and figures like '5M transactions' aren't wrongly redacted."""
    digits = re.sub(r"\D", "", s)
    return 10 <= len(digits) <= 15


def anonymize_text(
    text: str, fields: dict, extra_terms: list[str] | None = None
) -> tuple[str, dict]:
    """Redact direct identifiers from resume text. Returns (redacted, counts).

    `fields` is a record's parsed fields (from fields.parse_fields): the detected
    name and link URLs guide redaction beyond the generic regexes. `counts` is a
    per-category tally (values only, never the redacted strings themselves) for
    transparency. Order matters: emails first (they embed names & domains), then
    links, phones, schools, and names LAST.

    Conservative by design: it also redacts each name *token* (first/last name),
    so 'Alice' alone is caught, not just 'Alice Johnson'. Over-redaction is safer
    than leaking identity. Never raises."""
    if not text:
        return "", dict(_EMPTY_COUNTS)
    counts = dict(_EMPTY_COUNTS)
    out = text

    try:
        # 1. Emails (before names/links — an email embeds the name and domain).
        out, c = _redact_all(out, _EMAIL_RE, "[EMAIL]")
        counts["email"] += c

        # 2. Links — generic http(s) URLs, plus schemeless ones from fields.
        out, c = _redact_all(out, _URL_RE, "[LINK]")
        counts["link"] += c
        links = fields.get("links") or {}
        for key in ("linkedin", "github", "portfolio"):
            url = links.get(key)
            if url and url in out:
                counts["link"] += out.count(url)
                out = out.replace(url, "[LINK]")

        # 3. Phones (validated digit-count so dates/figures aren't redacted).
        out, c = _redact_all(out, _PHONE_CANDIDATE_RE, "[PHONE]", _is_phone)
        counts["phone"] += c

        # 4. Schools — regex, plus any caller-supplied institution names.
        out, c = _redact_all(out, _SCHOOL_RE, "[SCHOOL]")
        counts["school"] += c
        for term in extra_terms or []:
            if term:
                pat = re.compile(re.escape(term), re.I)
                out, c = _redact_all(out, pat, "[SCHOOL]")
                counts["school"] += c

        # 5. Name LAST: full name, then each token (whole-word, case-insensitive).
        name = fields.get("name")
        if name:
            full = re.compile(rf"\b{re.escape(name)}\b", re.I)
            out, c = _redact_all(out, full, "[CANDIDATE]")
            counts["name"] += c
            for tok in name.split():
                if len(tok) >= 2:
                    tp = re.compile(rf"\b{re.escape(tok)}\b", re.I)
                    out, c = _redact_all(out, tp, "[CANDIDATE]")
                    counts["name"] += c
        return out, counts
    except Exception:
        # Never raise. If anything goes wrong, fail SAFE: return no text rather
        # than risk leaking un-redacted identity into the packet.
        return "[REDACTION ERROR — text withheld]", counts
