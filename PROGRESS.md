# PROGRESS — Resume Screener MCP

## Phase -1 — Prerequisites check ✅
- Python 3.14.4 (need ≥3.11) ✓
- uv 0.11.7 ✓
- claude 2.1.186 ✓
- `TEST_FOLDER`: `tests/sample_resumes/` (generated sample resumes — see Phase 0 notes).
  Contains text-based PDFs, DOCX (incl. one with a table), and one unreadable
  file to prove failure-reporting.

## Phase 0 — Scaffold packaged project & prove MCP loop ✅ (pending human restart verify)
- `src/` layout package created: `server.py`, `extractor.py` (placeholder), `prefilter.py` (placeholder).
- `pyproject.toml` with console-script entry point `resume-screener-mcp = resume_screener.server:main`.
  - Needed `[tool.hatch.build.targets.wheel] packages = ["src/resume_screener"]` because the
    distribution name (`resume-screener-mcp`) differs from the package dir (`resume_screener`).
- Dev deps group added: `pytest`, `reportlab` (reportlab used only to generate sample resumes).
- `uv sync` succeeds; `fastmcp/pypdf/docx/sklearn` all import.
- `uv run resume-screener-mcp` starts, waits on stdio, exits clean on EOF, prints nothing to stdout.
- `ping` tool implemented.
- Sample resumes generated at `tests/sample_resumes/` via `tests/generate_samples.py`:
  8 text PDFs (varying relevance to a backend/Python/AWS role), 1 DOCX with a skills TABLE
  (priya_backend.docx), 1 image-only "scanned" PDF (scanned_resume.pdf, no extractable text),
  1 legacy `old_resume.doc`.
- Registered with Claude Code (dev form, user scope):
  `uv run --directory C:/Users/nonst/Learning/resume-screener-mcp resume-screener-mcp`.
  `claude mcp get resume-screener` → ✔ Connected.
- VERIFIED: human restarted Claude Code, `/mcp` connected, `ping` returned the alive message.

## Phase 1 — Resume ingestion & text extraction ✅ (logic-verified; MCP verify batched)
- `extractor.py` pure functions: `extract_pdf`, `extract_docx` (paragraphs + table cells),
  `normalize_text` (NFKC, control-char strip, whitespace collapse), `extract_resume` (dispatch +
  <100-char quality gate), `find_resume_files` (separates .doc, sorts, validates folder).
  None raise — every failure becomes a structured value.
- `list_resumes` tool added to `server.py`; `failed` list always present.
- `tests/test_extractor.py`: 8 tests, all pass (`uv run python -m pytest -q`).
- Direct logic dry-run on `tests/sample_resumes/`: found 11, parsed 9; scanned PDF + legacy .doc
  both appear in `failed` with correct reasons; DOCX table content captured (priya 610 chars).
- NOTE (real-world follow-up): pypdf can interleave two-column layouts; pdfplumber is the upgrade
  path if real resumes show garbled multi-column extraction.

## Process decision
- User chose to BATCH MCP verification: build Phases 2-5, logic-test each via direct Python calls,
  then ONE Claude Code restart to verify the full tool surface + judging behavior.

## Phase 2 — Single-stage screening ✅ (logic-verified; MCP verify batched)
- `screen_resumes(folder, job_description, top_k=5)` returns a judging packet:
  job_description, scoring_rubric (shared `SCORING_RUBRIC` constant), candidate_count,
  candidates[{filename, text}], parse_failures.
- Server never scores — Claude judges from the packet (docstring instructs merit-only scoring).
- Context-size guard: `SINGLE_STAGE_MAX = 25`; >25 readable resumes → ok:false pointing to
  screen_resumes_bulk.
- Logic dry-run: 9 candidates, rubric present, failures surfaced; empty-JD and bad-folder guards ok.

## Phase 3 — Robustness & recruiter-facing UX ✅ (logic-verified; MCP verify batched)
- In-memory extraction cache `_extract_cache` keyed by (path, mtime); invalidates on mtime change.
  Measured cold 370ms → warm 1ms on the sample folder. Used by `_gather_resumes` (and thus all tools).
- `_clamp_top_k`: top_k>count → return all (note); top_k<=0 → min(5,count) (note). Adds `top_k_note`.
- Edge cases verified (no crash, structured msg): bad/missing folder, not-a-directory, empty folder
  ("No .pdf or .docx resumes found here — check the path?"), empty/whitespace JD, corrupt PDF
  (PdfStreamError caught), locked/vanished file (mtime OSError falls through to extract_resume).
- `list_resumes` refactored to use the cached `_gather_resumes` (single source of truth).
- All 8 extractor tests still pass.

## Phase 4 — Two-stage scaling (TF-IDF pre-filter) ✅ (logic-verified; MCP verify batched)
- `prefilter.py`: `prefilter(candidates, jd, shortlist_size)` — TfidfVectorizer(stop_words="english")
  over [JD + resumes], cosine similarity to JD, sorted desc, top N with `prefilter_score` (0-1).
  Deterministic (stable sort preserves input order on ties); empty/degenerate inputs return safely.
- `screen_resumes_bulk(folder, jd, top_k=5, shortlist_size=25)` — extract → prefilter → judging packet
  of only the shortlist; adds total_extracted, shortlisted, prefiltered_out, prefilter_note (honest
  recall-risk wording for the recruiter).
- Built `tests/bulk_resumes/` (~146 readable copies + scanned + .doc). Bulk run: 146 → 25 shortlist,
  121 set aside, failures still reported, strong (alice/henry) survive, nurse dropped. ~7.9s cold.
- Real-folder ranking: alice/henry/priya/emma top; grace_nurse 0.0000.
- `tests/test_prefilter.py` added; full suite 13 tests pass.

## Phase 5 — Recruiter follow-up tools ✅ (logic-verified; MCP verify batched)
- `compare_candidates(folder, filenames[2-4], jd)` — returns full text of named finalists + JD +
  rubric; reports `not_found` for unknown names; guards 2-4 count and empty JD.
- `rerank(folder, jd, emphasis, top_k=5, shortlist_size=25)` — biases the JD with `emphasis` so the
  pre-filter reflects the new priority, reuses the cache, augments the rubric, carries `emphasis`.
  Verified: data-pipeline emphasis pulled carol_data from #5 → #1 in the shortlist.
- `tests/test_server.py` added. Full suite: 23 tests pass.
- All 6 tools registered (introspected via mcp.list_tools): ping, list_resumes, screen_resumes,
  screen_resumes_bulk, compare_candidates, rerank.

## NEXT: one batched Claude Code restart to verify the full tool surface + judging behavior.
## Phase 6 — Polish & documentation ✅
- README.md rewritten: problem statement, ASCII two-stage architecture diagram, the
  "server never calls an LLM — Claude is the judge" feature framing, prerequisites, BOTH
  install forms (dev `uv run --directory`, one-command `uvx --from git+...`), restart note,
  full tool-reference table, example session, dev commands, honest limitations & fairness note.
- `ping` kept as a documented health check.

## Phase 7 — Deep teaching document ✅
- docs/HOW_IT_WORKS.md written, all 11 sections: problem; what MCP is (client/server, stdio,
  stdout-sacred, discovery, request/response trace); the judge architecture & its trade-off;
  the two-stage pipeline (TF-IDF/cosine in plain terms + recall risk); module-by-module
  (extractor/prefilter/server with annotated snippets); tool-by-tool + why each docstring
  reads as it does; packaging & uvx one-command install; robustness philosophy; fairness &
  limitations; how to extend; interview talking points.

## STATUS: BUILD COMPLETE (code + docs). 23 tests pass. All 6 tools registered.
## FINAL GATE PASSED ✅ — after restart, verified live in Claude Code:
## - list_resumes: 11 found, 9 parsed, scanned PDF + legacy .doc reported.
## - screen_resumes: ranked top 5 (Henry/Alice/Priya top; nurse/frontend sink).
## - compare_candidates: Henry vs Alice side-by-side, not_found empty.
## - rerank (data-pipeline emphasis): Carol jumped #5 → #1. Order visibly shifted.
## BUILD 100% COMPLETE — all 7 phases done, all gates passed.
