"""Deterministic knockout / hard-requirement filters (Phase U4).

Why this exists: hiring needs *reproducible, auditable* hard filters ("must have
>= 5 years", "must list Python"). An LLM's judgment varies run-to-run; a rule
gives the same verdict every time and can be defended later. So the server
applies these BEFORE Claude judges — Claude scores only the candidates who clear
the hard bar.

Honest caveats (documented, surfaced — never hidden):
  - Rules run on heuristic field extraction (fields.py). Years-of-experience is
    estimated; a resume that simply doesn't *mention* a skill it has will be
    knocked out on a required-skills rule. This is a recall risk.
  - When years can't be determined we do NOT silently drop the candidate (that
    would violate "never silently lose a resume"). They PASS, but a warning is
    raised so a human can verify.

Pure functions, no MCP, no network, no LLM. Never raise.
"""

from __future__ import annotations

from resume_screener.fields import extract_skills


def apply_knockouts(record: dict, rules: dict) -> tuple[bool, list[str], list[str]]:
    """Evaluate a candidate against hard-requirement rules.

    `record` is an extract_resume record (uses record['fields'] for years and
    record['text'] for skill matching — skills are matched against each rule's
    own vocabulary, not the empty default skills_found). Returns
    (passes, reasons_failed, warnings):
      - passes: True if the candidate clears every rule.
      - reasons_failed: human-readable reason per failed rule (empty if passes).
      - warnings: indeterminate cases that did NOT cause a drop but need a human
        look (e.g. years couldn't be parsed).

    Supported rules (all optional):
      - min_years_experience: float — estimated years must be >= this.
      - required_skills: list[str] — ALL must appear in the resume text.
      - any_of_skills: list[str]  — at least ONE must appear.

    Deterministic and never raises."""
    reasons: list[str] = []
    warnings: list[str] = []
    if not rules:
        return True, reasons, warnings

    fields = record.get("fields") or {}
    text = record.get("text", "") or ""

    # --- min_years_experience ---
    bar = rules.get("min_years_experience")
    if bar is not None:
        try:
            bar = float(bar)
            yrs = fields.get("years_experience")
            if yrs is None:
                warnings.append(
                    f"years of experience could not be determined "
                    f"(rule: >= {bar:g}) — verify manually"
                )
            elif yrs < bar:
                reasons.append(
                    f"requires >= {bar:g} years, estimated {yrs:g}"
                )
        except (TypeError, ValueError):
            warnings.append("min_years_experience rule was not a number — ignored")

    # --- required_skills (ALL must be present) ---
    required = rules.get("required_skills") or []
    if required:
        found = {s.lower() for s in extract_skills(text, required)}
        missing = [s for s in required if s.lower() not in found]
        if missing:
            reasons.append(
                f"missing required skill(s): {', '.join(missing)}"
            )

    # --- any_of_skills (at least ONE must be present) ---
    any_of = rules.get("any_of_skills") or []
    if any_of:
        if not extract_skills(text, any_of):
            reasons.append(
                f"has none of the required skills: {', '.join(any_of)}"
            )

    return (len(reasons) == 0), reasons, warnings
