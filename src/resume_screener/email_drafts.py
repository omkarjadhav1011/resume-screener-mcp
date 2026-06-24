"""Editable email drafts (Phase U9). Pure, NO network.

The division of labor: the server ships starter TEMPLATES and writes drafts to
disk; Claude personalizes the wording from each candidate's resume + JD + outcome.
"Editable" means real files on disk — the recruiter edits them, and the send step
(U10) reads them back, so hand-edits always win.

Drafts are stored as a simple, human-editable .eml-style file:

    To: alice@example.com
    Subject: Interview for the Backend Engineer role
    ---
    Hi Alice,
    ...body...

Never raises on the normal path; bad input becomes a structured value.
"""

from __future__ import annotations

import os
import re
from string import Template

# Starter templates. Placeholders use $name syntax (string.Template). Claude is
# expected to personalize these; anything left unfilled is marked [FILL: x] so a
# gap is obvious and never silently blank.
TEMPLATES: dict[str, dict] = {
    "interview_invite": {
        "subject": "Interview for the $role role at $company",
        "body": (
            "Hi $candidate_name,\n\n"
            "Thank you for applying for the $role role at $company. Your background "
            "stood out and we'd like to invite you to an interview.\n\n"
            "Format: $interview_format\n"
            "Proposed times: $time_options\n\n"
            "Please let us know what suits you.\n\n"
            "Best regards,\n$recruiter_name\n$company"
        ),
        "placeholders": [
            "candidate_name", "role", "company",
            "interview_format", "time_options", "recruiter_name",
        ],
    },
    "rejection": {
        "subject": "Update on your application for $role at $company",
        "body": (
            "Hi $candidate_name,\n\n"
            "Thank you for applying for the $role role at $company and for sharing "
            "your experience with us.\n\n"
            "After careful consideration we've decided not to move forward at this "
            "time. This was a difficult decision given the strength of applicants.\n\n"
            "We genuinely appreciate your interest and wish you the very best in "
            "your search.\n\n"
            "Warm regards,\n$recruiter_name\n$company"
        ),
        "placeholders": ["candidate_name", "role", "company", "recruiter_name"],
    },
    "request_info": {
        "subject": "A few more details for your $role application at $company",
        "body": (
            "Hi $candidate_name,\n\n"
            "Thanks for applying for the $role role at $company. To continue "
            "reviewing your application, could you share the following:\n\n"
            "$requested_items\n\n"
            "Thanks in advance!\n\n"
            "Best regards,\n$recruiter_name\n$company"
        ),
        "placeholders": [
            "candidate_name", "role", "company", "requested_items", "recruiter_name",
        ],
    },
}

# Matches a leftover $placeholder or ${placeholder} after substitution.
_PLACEHOLDER_RE = re.compile(r"\$\{?(\w+)\}?")


def _fill(text: str, variables: dict) -> str:
    """Substitute provided variables; mark any leftover placeholder as [FILL: x]
    so unfilled gaps are visible, never silently blank."""
    filled = Template(text).safe_substitute(variables or {})
    return _PLACEHOLDER_RE.sub(lambda m: f"[FILL: {m.group(1)}]", filled)


def render_draft(template_name: str, variables: dict | None = None) -> dict:
    """Render a starter template with `variables`. Returns
    {ok, subject, body} or {ok: False, error}. Never raises."""
    tpl = TEMPLATES.get(template_name)
    if tpl is None:
        return {
            "ok": False,
            "error": f"Unknown template {template_name!r}. Options: {list(TEMPLATES)}",
        }
    return {
        "ok": True,
        "subject": _fill(tpl["subject"], variables or {}),
        "body": _fill(tpl["body"], variables or {}),
    }


def write_draft_file(to_addr: str, subject: str, body: str, out_path: str) -> str:
    """Write an editable .eml-style draft file. Returns the absolute path."""
    abspath = os.path.abspath(out_path)
    content = f"To: {to_addr}\nSubject: {subject}\n---\n{body}\n"
    with open(abspath, "w", encoding="utf-8") as f:
        f.write(content)
    return abspath


def read_draft_file(path: str) -> dict:
    """Parse a draft file back into {to, subject, body}. Headers precede the first
    '---' line; everything after is the body. Used by the send step (U10) so
    hand-edits are picked up. Never raises — missing fields come back empty."""
    try:
        with open(path, encoding="utf-8") as f:
            raw = f.read()
    except Exception as exc:
        return {"to": "", "subject": "", "body": "", "error": type(exc).__name__}

    lines = raw.split("\n")
    headers: dict[str, str] = {}
    sep_idx = None
    for i, line in enumerate(lines):
        if line.strip() == "---":
            sep_idx = i
            break
        if ":" in line:
            key, val = line.split(":", 1)
            headers[key.strip().lower()] = val.strip()
    body = "\n".join(lines[sep_idx + 1:]) if sep_idx is not None else ""
    return {
        "to": headers.get("to", ""),
        "subject": headers.get("subject", ""),
        "body": body.strip("\n"),
    }
