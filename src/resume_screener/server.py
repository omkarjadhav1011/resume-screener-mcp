import logging
import os
import re
from datetime import datetime

from fastmcp import FastMCP

from resume_screener.anonymize import anonymize_text
from resume_screener.collect import collect_resumes
from resume_screener.email_drafts import (
    TEMPLATES,
    render_draft,
    write_draft_file,
)
from resume_screener.export import WRITERS
from resume_screener.extractor import extract_resume, find_resume_files
from resume_screener.knockout import apply_knockouts
from resume_screener.mailer import send_drafts
from resume_screener.prefilter import prefilter

# Logs go to STDERR by default — NEVER stdout. Stdout is the MCP transport;
# printing to it corrupts the JSON-RPC protocol and disconnects the server.
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("resume-screener")

mcp = FastMCP(name="Resume Screener")

# The rubric is returned inside every judging packet so Claude Code scores
# consistently across calls. The server never applies it — Claude does.
SCORING_RUBRIC = (
    "Score each candidate 0-100 based ONLY on skills, experience, and "
    "qualifications relevant to the job description. "
    "90-100: clearly exceeds the role's requirements. "
    "70-89: strong match, meets most requirements. "
    "50-69: partial match, some relevant skills but notable gaps. "
    "1-49: weak or off-target match. "
    "SCORE ON MERIT ONLY: ignore name, gender, age, nationality, photos, and "
    "school prestige. Judge skills and relevant experience against the JD. "
    "Give a one-line reason per candidate, then return the top candidates "
    "ranked highest-to-lowest."
)

# Above this many parseable resumes, returning every full text in one packet is
# unwieldy — the caller should use screen_resumes_bulk (TF-IDF pre-filter first).
SINGLE_STAGE_MAX = 25

# Returned in every anonymized packet so Claude scores blind and honestly.
FAIRNESS_NOTE = (
    "ANONYMIZED MODE: candidate names, emails, phones, links, and listed schools "
    "were removed from the text BEFORE you received it; each candidate is labeled "
    "with an opaque id (candidate_NN). Score PURELY from the anonymized text. Use "
    "id_map only to map your final ranking back to real files for the recruiter — "
    "do not let it influence scoring. Honest caveat: this removes direct "
    "identifiers but cannot remove every indirect signal (writing style, gendered "
    "phrasing), so it reduces bias risk but is not provably bias-free."
)


# In-memory extraction cache. The server is a long-lived process, so caching
# parsed text avoids re-parsing on re-screens and re-ranks. Keyed by path and
# invalidated when the file's mtime changes.
_extract_cache: dict[str, tuple[float, dict]] = {}


def _extract_cached(path: str) -> dict:
    """extract_resume with an (path, mtime) cache. Never raises."""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        # File vanished or is locked — let extract_resume produce the
        # structured error rather than caching a transient failure.
        return extract_resume(path)
    cached = _extract_cache.get(path)
    if cached and cached[0] == mtime:
        log.info("cache hit: %s", os.path.basename(path))
        return cached[1]
    record = extract_resume(path)
    _extract_cache[path] = (mtime, record)
    return record


def _clamp_top_k(top_k: int, count: int) -> tuple[int, str | None]:
    """Clamp top_k to what's available. Returns (effective_top_k, note)."""
    if count == 0:
        return 0, None
    if top_k <= 0:
        effective = min(5, count)
        return effective, (
            f"top_k was {top_k} (must be positive); using {effective}."
        )
    if top_k > count:
        return count, (
            f"Only {count} candidate(s) available, so returning all {count} "
            f"instead of the requested {top_k}."
        )
    return top_k, None


def _build_candidates(
    records: list[dict], anonymize: bool, include_prefilter: bool = False
) -> tuple[list[dict], dict | None]:
    """Build the packet's candidate list from extract records.

    Plain mode → {filename, text} (unchanged from before). Anonymized mode →
    each candidate's text is redacted via fields, filename becomes an opaque
    'candidate_NN', and a separate id_map (opaque id → real filename) is returned
    so results can be de-anonymized AFTER judging. Returns (candidates, id_map);
    id_map is None when not anonymizing."""
    candidates: list[dict] = []
    id_map: dict | None = {} if anonymize else None
    for i, r in enumerate(records, 1):
        if anonymize:
            text, _counts = anonymize_text(r["text"], r.get("fields") or {})
            label = f"candidate_{i:02d}"
            id_map[label] = r["filename"]
        else:
            text = r["text"]
            label = r["filename"]
        candidate = {"filename": label, "text": text}
        if include_prefilter:
            candidate["prefilter_score"] = r.get("prefilter_score")
        candidates.append(candidate)
    return candidates, id_map


def _add_anonymization_meta(packet: dict, id_map: dict | None) -> dict:
    """Attach anonymization metadata to a packet when id_map is present."""
    if id_map is not None:
        packet["anonymized"] = True
        packet["id_map"] = id_map
        packet["fairness_note"] = FAIRNESS_NOTE
    return packet


def _partition_knockouts(
    records: list[dict], knockouts: dict | None
) -> tuple[list[dict], list[dict], list[dict]]:
    """Split records into those that clear the hard rules and those that don't.

    Returns (passed, knocked_out, warnings). `knocked_out` entries are
    {filename, reasons}; `warnings` are {filename, warnings} for indeterminate
    cases that were NOT dropped (never silently lose a resume). With no rules,
    everyone passes."""
    if not knockouts:
        return records, [], []
    passed: list[dict] = []
    knocked_out: list[dict] = []
    warnings: list[dict] = []
    for r in records:
        ok, reasons, warns = apply_knockouts(r, knockouts)
        if warns:
            warnings.append({"filename": r["filename"], "warnings": warns})
        if ok:
            passed.append(r)
        else:
            knocked_out.append({"filename": r["filename"], "reasons": reasons})
    return passed, knocked_out, warnings


def _add_knockout_meta(
    packet: dict, knockouts: dict | None,
    knocked_out: list[dict], warnings: list[dict],
) -> dict:
    """Attach knockout results to a packet when rules were applied. The
    knocked_out list is ALWAYS present when rules ran — knocked-out candidates
    are surfaced for review, never silently dropped."""
    if knockouts:
        packet["knocked_out"] = knocked_out
        if warnings:
            packet["knockout_warnings"] = warnings
    return packet


def _gather_resumes(folder: str) -> tuple[list[dict], list[dict], str | None]:
    """Extract every resume in a folder. Returns (ok_records, failures, error).

    Shared by all screening tools. `ok_records` are full extract_resume records;
    `failures` are {filename, reason}; `error` is set only for a bad folder."""
    resume_paths, legacy_paths, error = find_resume_files(folder)
    if error:
        return [], [], error

    ok_records: list[dict] = []
    failures: list[dict] = []
    for path in resume_paths:
        record = _extract_cached(path)
        if record["ok"]:
            ok_records.append(record)
        else:
            failures.append(
                {"filename": record["filename"], "reason": record["error"]}
            )
    for path in legacy_paths:
        failures.append(
            {
                "filename": os.path.basename(path),
                "reason": "legacy .doc format not supported — convert to .docx or PDF",
            }
        )
    return ok_records, failures, None


@mcp.tool
def ping() -> dict:
    """Health check. Confirms the Resume Screener MCP server is alive and
    reachable. Use this to verify the connection before screening."""
    log.info("ping called")
    return {"ok": True, "message": "Resume Screener MCP is alive"}


@mcp.tool
def list_resumes(folder: str) -> dict:
    """Scan a folder for resume files (.pdf and .docx) and report what was
    found and whether each file could be read. ALWAYS call this FIRST, before
    screening, so the recruiter sees the size of the pile and learns which
    files are unreadable (scanned PDFs, password-protected, legacy .doc) BEFORE
    relying on results. Never assume every file was read — check the failures.

    Args:
        folder: Absolute path to the folder containing resumes.

    Returns counts of found/parsed files plus an explicit list of failures
    with a human-readable reason for each."""
    log.info("list_resumes called: %s", folder)
    ok_records, failed, error = _gather_resumes(folder)
    if error:
        return {"ok": False, "error": error}

    found = len(ok_records) + len(failed)
    if found == 0:
        return {
            "ok": True,
            "folder": folder,
            "found": 0,
            "parsed": 0,
            "files": [],
            "failed": [],
            "note": "No .pdf or .docx resumes found here — check the path?",
        }

    files = [
        {"filename": r["filename"], "char_count": r["char_count"], "ok": True}
        for r in ok_records
    ]
    return {
        "ok": True,
        "folder": folder,
        "found": found,
        "parsed": len(ok_records),
        "failed": failed,
        "files": files,
    }


@mcp.tool
def screen_resumes(
    folder: str,
    job_description: str,
    top_k: int = 5,
    anonymize: bool = False,
    knockouts: dict | None = None,
) -> dict:
    """Prepare resumes from a folder to be screened against a job description.

    This tool extracts and returns the cleaned text of every parseable resume
    in the folder, together with the job description and a scoring rubric.
    YOU (Claude Code) are the judge: after calling this tool, score EACH
    candidate from 0-100 based ONLY on skills, experience, and qualifications
    relevant to the job description, give a one-line reason per candidate, then
    return the top {top_k} ranked highest-to-lowest.

    SCORE ON MERIT ONLY. Ignore name, gender, age, nationality, photos, and
    school prestige. Judge skills and relevant experience against the JD.

    Use this for SMALL folders (up to ~25 resumes). For larger piles use
    screen_resumes_bulk, which pre-filters before judging.

    Args:
        folder: Absolute path to the folder of resumes.
        job_description: The full JD text to screen against.
        top_k: How many top candidates to return (default 5).
        anonymize: If True, names/emails/phones/links/schools are REMOVED from
            each candidate's text before you see it, and candidates are labeled
            candidate_NN. The packet then includes an id_map (candidate_NN → real
            filename) and a fairness_note. Score from the anonymized text; use
            id_map only to report results. Use this for bias-resistant screening.
        knockouts: Optional hard-requirement rules applied DETERMINISTICALLY
            before judging. Candidates failing any rule are removed from the
            packet and listed under 'knocked_out' with reasons (never silently
            dropped). Supported keys: min_years_experience (float),
            required_skills (list, ALL must appear), any_of_skills (list, at
            least one). These use heuristic field extraction, so tell the
            recruiter knocked-out borderline cases are worth a manual look.

    Returns a judging packet: the JD, the scoring rubric, and a list of
    candidates each with filename and extracted text."""
    log.info(
        "screen_resumes called: %s (top_k=%s, anonymize=%s)",
        folder, top_k, anonymize,
    )

    if not job_description or not job_description.strip():
        return {
            "ok": False,
            "error": "Please provide the job description text to screen against.",
        }

    ok_records, failures, error = _gather_resumes(folder)
    if error:
        return {"ok": False, "error": error}

    if not ok_records:
        return {
            "ok": True,
            "candidate_count": 0,
            "candidates": [],
            "parse_failures": failures,
            "note": "No readable resumes to screen in that folder.",
        }

    passed, knocked_out, ko_warnings = _partition_knockouts(ok_records, knockouts)
    if not passed:
        packet = {
            "ok": True,
            "candidate_count": 0,
            "candidates": [],
            "parse_failures": failures,
            "note": "All readable resumes were removed by the knockout rules.",
        }
        return _add_knockout_meta(packet, knockouts, knocked_out, ko_warnings)

    if len(passed) > SINGLE_STAGE_MAX:
        return {
            "ok": False,
            "error": (
                f"This folder has {len(passed)} readable resumes (after "
                f"knockouts), which is too many to screen in one packet (limit "
                f"{SINGLE_STAGE_MAX}). Use screen_resumes_bulk, which "
                "pre-filters the pile first."
            ),
            "candidate_count": len(passed),
        }

    candidates, id_map = _build_candidates(passed, anonymize)
    effective_top_k, top_k_note = _clamp_top_k(top_k, len(candidates))
    packet = {
        "ok": True,
        "job_description": job_description,
        "top_k": effective_top_k,
        "scoring_rubric": SCORING_RUBRIC,
        "candidate_count": len(candidates),
        "candidates": candidates,
        "parse_failures": failures,
    }
    if top_k_note:
        packet["top_k_note"] = top_k_note
    packet = _add_anonymization_meta(packet, id_map)
    return _add_knockout_meta(packet, knockouts, knocked_out, ko_warnings)


@mcp.tool
def screen_resumes_bulk(
    folder: str,
    job_description: str,
    top_k: int = 5,
    shortlist_size: int = 25,
    anonymize: bool = False,
    knockouts: dict | None = None,
) -> dict:
    """Screen a LARGE folder of resumes (dozens to ~200) against a JD.

    Pipeline: (1) extract all resumes, (2) a local keyword/TF-IDF pre-filter
    cheaply narrows them to the {shortlist_size} most relevant, (3) this tool
    returns ONLY those shortlisted resumes as a judging packet. YOU (Claude
    Code) then score the shortlist and return the top {top_k} with reasons,
    exactly as in screen_resumes. SCORE ON MERIT ONLY (skills & experience).

    Use this instead of screen_resumes when the folder has more than ~25
    resumes. Tell the recruiter how many were pre-filtered out and why
    (the pre-filter is coarse keyword matching, not a final judgment) — a
    borderline-relevant resume could be dropped here and is worth a manual look.

    Args:
        folder: Absolute path to the folder of resumes.
        job_description: The full JD text to screen against.
        top_k: How many top candidates to return after judging (default 5).
        shortlist_size: How many resumes survive the pre-filter (default 25).
        anonymize: If True, redact names/emails/phones/links/schools from the
            shortlisted texts and label candidates candidate_NN; the packet then
            includes id_map and a fairness_note (see screen_resumes).
        knockouts: Optional hard-requirement rules (min_years_experience,
            required_skills, any_of_skills) applied DETERMINISTICALLY before the
            pre-filter. Failing candidates are removed and listed under
            'knocked_out' with reasons (never silently dropped). See screen_resumes.

    Returns a judging packet (JD, rubric, shortlisted candidates with text and a
    prefilter_score) plus pre-filter metadata: total_extracted, shortlisted,
    prefiltered_out."""
    log.info(
        "screen_resumes_bulk called: %s (top_k=%s, shortlist=%s, anonymize=%s)",
        folder, top_k, shortlist_size, anonymize,
    )

    if not job_description or not job_description.strip():
        return {
            "ok": False,
            "error": "Please provide the job description text to screen against.",
        }

    ok_records, failures, error = _gather_resumes(folder)
    if error:
        return {"ok": False, "error": error}

    total_extracted = len(ok_records)
    if total_extracted == 0:
        return {
            "ok": True,
            "candidate_count": 0,
            "candidates": [],
            "parse_failures": failures,
            "note": "No readable resumes to screen in that folder.",
        }

    # Knockouts run BEFORE the pre-filter — cheaper to shrink the pile first,
    # and a hard-failed candidate should never occupy a shortlist slot.
    passed, knocked_out, ko_warnings = _partition_knockouts(ok_records, knockouts)
    pool = len(passed)

    shortlisted = prefilter(passed, job_description, shortlist_size)
    prefiltered_out = pool - len(shortlisted)

    candidates, id_map = _build_candidates(
        shortlisted, anonymize, include_prefilter=True
    )
    effective_top_k, top_k_note = _clamp_top_k(top_k, len(candidates))
    packet = {
        "ok": True,
        "job_description": job_description,
        "top_k": effective_top_k,
        "scoring_rubric": SCORING_RUBRIC,
        "candidate_count": len(candidates),
        "candidates": candidates,
        "parse_failures": failures,
        "total_extracted": total_extracted,
        "shortlisted": len(shortlisted),
        "prefiltered_out": prefiltered_out,
        "prefilter_note": (
            f"A local TF-IDF keyword pre-filter narrowed {pool} resume(s) to "
            f"the {len(shortlisted)} most relevant; {prefiltered_out} were set "
            "aside. This is coarse keyword matching, not a final judgment — "
            "borderline resumes may have been dropped and can be reviewed "
            "manually."
        ),
    }
    if top_k_note:
        packet["top_k_note"] = top_k_note
    packet = _add_anonymization_meta(packet, id_map)
    return _add_knockout_meta(packet, knockouts, knocked_out, ko_warnings)


@mcp.tool
def compare_candidates(
    folder: str,
    filenames: list[str],
    job_description: str,
    anonymize: bool = False,
) -> dict:
    """Return the full extracted text of 2-4 named candidates side by side,
    with the JD, so YOU (Claude Code) can compare them in depth against the
    role. Use after an initial screen when the recruiter wants to drill into
    specific finalists. Score on merit only.

    Args:
        folder: Absolute path to the folder of resumes.
        filenames: The 2-4 candidate filenames to compare (as returned by a
            previous screen, e.g. ["alice_backend.pdf", "henry_principal.pdf"]).
        job_description: The JD text to compare them against.
        anonymize: If True, redact identity from each candidate's text and label
            them candidate_NN; the packet then includes id_map and a
            fairness_note (see screen_resumes). not_found echoes the names you
            supplied and is never anonymized.

    Returns a comparison packet: the JD, the rubric, and each requested
    candidate's full text (or a not-found/unreadable note per file)."""
    log.info("compare_candidates called: %s (anonymize=%s)", filenames, anonymize)

    if not job_description or not job_description.strip():
        return {
            "ok": False,
            "error": "Please provide the job description text to compare against.",
        }
    if not filenames:
        return {
            "ok": False,
            "error": "Name 2-4 candidate files to compare.",
        }
    if not 2 <= len(filenames) <= 4:
        return {
            "ok": False,
            "error": (
                f"Compare 2-4 candidates at a time (you named {len(filenames)})."
            ),
        }

    resume_paths, _legacy, error = find_resume_files(folder)
    if error:
        return {"ok": False, "error": error}
    by_name = {os.path.basename(p): p for p in resume_paths}

    compared: list[dict] = []
    not_found: list[str] = []
    id_map: dict | None = {} if anonymize else None
    idx = 0
    for name in filenames:
        path = by_name.get(name)
        if path is None:
            not_found.append(name)
            continue
        record = _extract_cached(path)
        idx += 1
        label = f"candidate_{idx:02d}" if anonymize else name
        if anonymize:
            id_map[label] = name
        if record["ok"]:
            text = record["text"]
            if anonymize:
                text, _counts = anonymize_text(text, record.get("fields") or {})
            compared.append({"filename": label, "text": text})
        else:
            compared.append(
                {"filename": label, "text": "", "error": record["error"]}
            )

    packet = {
        "ok": True,
        "job_description": job_description,
        "scoring_rubric": SCORING_RUBRIC,
        "candidates": compared,
        "not_found": not_found,
    }
    return _add_anonymization_meta(packet, id_map)


@mcp.tool
def rerank(
    folder: str,
    job_description: str,
    emphasis: str,
    top_k: int = 5,
    shortlist_size: int = 25,
    anonymize: bool = False,
    knockouts: dict | None = None,
) -> dict:
    """Re-screen with an adjusted priority. 'emphasis' is a free-text
    instruction like 'weight cloud/AWS experience more heavily' or 'prioritize
    healthcare domain experience'. Returns the judging packet (same as
    screen_resumes_bulk) plus the emphasis instruction, which YOU must factor
    into the scoring. Use when the recruiter wants to re-rank the same pile
    under different priorities — no need to re-state the whole JD.

    Args:
        folder: Absolute path to the folder of resumes.
        job_description: The JD text (same pile as the prior screen).
        emphasis: Free-text re-ranking priority to apply on top of the JD.
        top_k: How many top candidates to return (default 5).
        shortlist_size: How many resumes survive the pre-filter (default 25).
        anonymize: If True, redact identity and label candidates candidate_NN
            (see screen_resumes); passed through to the underlying screen.
        knockouts: Optional hard-requirement rules (see screen_resumes); applied
            to the same pile before re-ranking.

    Reuses the extraction cache, so re-ranks are fast."""
    log.info("rerank called: emphasis=%r (anonymize=%s)", emphasis, anonymize)

    if not emphasis or not emphasis.strip():
        return {
            "ok": False,
            "error": "Provide an emphasis instruction to re-rank by.",
        }

    # The pre-filter should reflect the emphasis too, so a resume relevant under
    # the new priority isn't pre-filtered out. Bias the JD with the emphasis.
    biased_jd = f"{job_description}\n\nEMPHASIS: {emphasis}"
    packet = screen_resumes_bulk(
        folder, biased_jd, top_k=top_k, shortlist_size=shortlist_size,
        anonymize=anonymize, knockouts=knockouts,
    )
    if not packet.get("ok"):
        return packet

    # Return the original JD to the caller; carry the emphasis explicitly.
    packet["job_description"] = job_description
    packet["emphasis"] = emphasis
    packet["scoring_rubric"] = (
        SCORING_RUBRIC
        + f" ADDITIONAL EMPHASIS from the recruiter — factor this into every "
        f"score: {emphasis}"
    )
    return packet


def _enrich_rows(results: list[dict], folder: str) -> list[dict]:
    """Fill missing email/years_experience on each row from the resume's parsed
    fields, matched by filename. Best-effort: if the folder is unreadable or a
    filename doesn't match, the row is returned unchanged."""
    resume_paths, _legacy, error = find_resume_files(folder)
    if error:
        return results
    by_name = {os.path.basename(p): p for p in resume_paths}
    out: list[dict] = []
    for r in results:
        row = dict(r)
        path = by_name.get(row.get("filename"))
        if path:
            rec = _extract_cached(path)
            fields = (rec.get("fields") or {}) if rec.get("ok") else {}
            if "email" not in row and fields.get("email"):
                row["email"] = fields["email"]
            if "years_experience" not in row and fields.get("years_experience") is not None:
                row["years_experience"] = fields["years_experience"]
        out.append(row)
    return out


@mcp.tool
def export_shortlist(
    results: list[dict],
    out_path: str,
    fmt: str = "csv",
    folder: str | None = None,
) -> dict:
    """Save a judged shortlist to a CSV or XLSX file for the recruiter.

    Call this AFTER you (Claude Code) have produced the ranked results. The
    server only formats and writes the file — it does NOT score.

    Args:
        results: The judged, ranked candidates (highest first). Each is an object
            like {"filename", "score", "reason"} and may add "rank"/"email"/
            "years_experience". 'filename' is required on every row.
        out_path: Absolute path for the output file; its parent folder must exist.
        fmt: "csv" (default) or "xlsx".
        folder: Optional source folder of the resumes — if given, missing
            email/years_experience columns are filled from each resume's parsed
            fields.

    Returns the absolute path written plus row count, or a structured error."""
    n = len(results) if isinstance(results, list) else -1
    log.info("export_shortlist called: %s (fmt=%s, rows=%s)", out_path, fmt, n)

    fmt = (fmt or "").lower()
    if fmt not in WRITERS:
        return {"ok": False, "error": f"Unsupported format {fmt!r} — use 'csv' or 'xlsx'."}
    if not isinstance(results, list) or not results:
        return {"ok": False, "error": "Provide a non-empty list of judged results to export."}
    if not all(isinstance(r, dict) for r in results):
        return {"ok": False, "error": "Each result must be an object (e.g. {\"filename\":..., \"score\":..., \"reason\":...})."}
    if not all("filename" in r for r in results):
        return {
            "ok": False,
            "error": "Every result needs at least a 'filename'. Example row: "
                     "{\"filename\": \"alice.pdf\", \"score\": 90, \"reason\": \"...\"}.",
        }

    abspath = os.path.abspath(out_path)
    parent = os.path.dirname(abspath)
    if parent and not os.path.isdir(parent):
        return {"ok": False, "error": f"The output folder doesn't exist: {parent}"}

    rows = _enrich_rows(results, folder) if folder else results
    try:
        written = WRITERS[fmt](rows, abspath)
    except Exception as exc:
        return {"ok": False, "error": f"Couldn't write the file ({type(exc).__name__}): {exc}"}

    return {
        "ok": True,
        "path": written,
        "rows": len(rows),
        "format": fmt,
        "enriched": bool(folder),
    }


def _safe_label(label: str) -> str:
    """Make a free-text label safe for a folder name."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", label.strip()).strip("-")
    return cleaned or "selected"


@mcp.tool
def collect_selected(
    folder: str,
    filenames: list[str],
    dest_folder: str | None = None,
    role_label: str | None = None,
) -> dict:
    """Copy the chosen candidates' resume files into a new folder so the recruiter
    has the shortlist in one place. Call this AFTER you (Claude Code) have decided
    the shortlist. The server only copies files — it does not choose candidates.

    Args:
        folder: Source folder the resumes were screened from.
        filenames: The selected resume filenames (as returned by a screen, e.g.
            ["alice_backend.pdf", "henry_principal.pdf"]).
        dest_folder: Where to copy them. If omitted, defaults to
            '<folder>/selected/<role_label or timestamp>'.
        role_label: Optional label used to name the default destination folder
            (e.g. "backend-finalists").

    Returns {ok, dest, copied, not_found, skipped}. Existing files are skipped and
    reported, never overwritten; unknown names appear in not_found."""
    log.info("collect_selected called: %s -> %s", filenames, dest_folder)

    if not filenames or not isinstance(filenames, list):
        return {"ok": False, "error": "Name at least one resume file to collect."}

    if dest_folder is None:
        label = (
            _safe_label(role_label) if role_label
            else datetime.now().strftime("%Y%m%d-%H%M%S")
        )
        dest_folder = os.path.join(folder, "selected", label)

    return collect_resumes(folder, filenames, dest_folder)


@mcp.tool
def get_email_templates() -> dict:
    """Return the starter email templates so YOU (Claude Code) can personalize
    them per candidate before drafting. Each template lists its subject, body,
    and placeholders ($name style). Personalize the wording from the candidate's
    resume + JD + outcome, then call draft_emails with the final subject/body
    (or pass template + variables to draft_emails to render a starter).

    Templates: interview_invite, rejection (sensitive — always have the recruiter
    review before sending), request_info."""
    log.info("get_email_templates called")
    return {"ok": True, "templates": TEMPLATES}


@mcp.tool
def draft_emails(dest_folder: str, emails: list[dict]) -> dict:
    """Write editable email drafts (one .eml file per candidate) into
    dest_folder/drafts/. Call AFTER collect_selected. YOU (Claude Code) craft the
    wording per candidate; the server only writes the files. The recruiter then
    EDITS these files on disk and reviews them before any send (U10 reads them
    back, so edits win).

    Args:
        dest_folder: The selected-candidates folder (from collect_selected). The
            drafts/ subfolder is created inside it.
        emails: One object per candidate. Provide EITHER final wording
            {"filename", "to", "subject", "body"} OR a starter to render
            {"filename", "to", "template", "variables": {...}}. 'to' is the
            recipient email; a candidate with no 'to' is reported in
            missing_email, never silently skipped.

    Returns {ok, drafts_dir, written:[{filename,to,path}], missing_email, count}."""
    log.info("draft_emails called: %s (%s rows)", dest_folder,
             len(emails) if isinstance(emails, list) else -1)

    if not emails or not isinstance(emails, list):
        return {"ok": False, "error": "Provide a non-empty list of emails to draft."}

    drafts_dir = os.path.join(os.path.abspath(dest_folder), "drafts")
    try:
        os.makedirs(drafts_dir, exist_ok=True)
    except Exception as exc:
        return {
            "ok": False,
            "error": f"Couldn't create the drafts folder ({type(exc).__name__}): {drafts_dir}",
        }

    written: list[dict] = []
    missing_email: list[str] = []
    errors: list[dict] = []
    for i, e in enumerate(emails, 1):
        if not isinstance(e, dict):
            continue
        to_addr = e.get("to")
        label = e.get("filename") or e.get("to") or f"draft_{i}"
        if not to_addr:
            missing_email.append(e.get("filename") or "(unknown)")
            continue

        # Final wording wins; otherwise render a starter template.
        subject = e.get("subject")
        body = e.get("body")
        if (subject is None or body is None) and e.get("template"):
            rendered = render_draft(e["template"], e.get("variables") or {})
            if not rendered["ok"]:
                errors.append({"filename": label, "error": rendered["error"]})
                continue
            subject = subject if subject is not None else rendered["subject"]
            body = body if body is not None else rendered["body"]
        subject = subject or ""
        body = body or ""

        base = os.path.splitext(os.path.basename(label))[0]
        try:
            path = write_draft_file(
                to_addr, subject, body, os.path.join(drafts_dir, base + ".eml")
            )
        except Exception as exc:
            errors.append({"filename": label, "error": type(exc).__name__})
            continue
        written.append({"filename": e.get("filename"), "to": to_addr, "path": path})

    result = {
        "ok": True,
        "drafts_dir": drafts_dir,
        "written": written,
        "missing_email": missing_email,
        "count": len(written),
    }
    if errors:
        result["errors"] = errors
    return result


@mcp.tool
def send_emails(
    dest_folder: str, dry_run: bool = True, confirm: bool = False
) -> dict:
    """Send the email drafts in dest_folder/drafts/ over SMTP.

    SAFETY: dry_run is TRUE by default and previews every email WITHOUT sending.
    To actually send you MUST pass dry_run=False AND confirm=True, and you must
    ONLY do that after showing the recruiter the dry-run preview and getting their
    explicit approval. Never send on your own initiative. Credentials come from
    env vars (SMTP_HOST/PORT/USER/PASS, MAIL_FROM); if they're unset this returns
    a clear error, never a crash. Every send is logged to dest_folder/send_log.json.

    Args:
        dest_folder: The folder whose drafts/ subfolder holds the .eml files
            (from draft_emails).
        dry_run: Preview only (default True). Sends nothing.
        confirm: Must be True (with dry_run=False) to actually send.

    Returns the dry-run preview, or per-recipient send results + the log path."""
    log.info("send_emails called: %s (dry_run=%s, confirm=%s)",
             dest_folder, dry_run, confirm)
    return send_drafts(dest_folder, dry_run=dry_run, confirm=confirm)


def main() -> None:
    """Console-script entry point (see [project.scripts] in pyproject.toml)."""
    mcp.run()  # stdio transport by default


if __name__ == "__main__":
    main()
