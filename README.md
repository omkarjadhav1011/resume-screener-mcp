# Resume Screener MCP

A local [MCP](https://modelcontextprotocol.io) server for **Claude Code** that screens a
folder of resumes (PDF + DOCX) against a job description and returns a ranked **top-K** of
candidates, each with a **score (0–100)** and a **one-line reason**.

> **The central design choice:** this server **never calls an LLM.** It has no API key, no
> `anthropic` package, no network calls to any model. It is a *text-extraction and
> pre-filtering* engine. **Claude Code is the judge** — the server prepares clean
> "judging packets" (resume text + the JD + a scoring rubric) and Claude Code does the
> actual scoring and reasoning inside your session. No API cost, and it's the idiomatic
> way to build an MCP: the server provides *capabilities and data*; the model provides
> *intelligence*.

---

## The problem

Recruiters screen hundreds of resumes one-by-one through an ATS. It's slow, inconsistent,
and the unreadable files (scanned PDFs, password-protected, legacy `.doc`) silently get
lost. "Good" looks like: a ranked shortlist with reasons, scored on merit, with every
unreadable file reported — not dropped.

## Architecture — the two-stage flow

```
                                    ┌─────────────────────────────────────────┐
  folder of resumes                 │  ≤ ~25 readable?  → screen_resumes        │
  (.pdf / .docx)                    │                     (return ALL as packet)│
        │                           │                                           │
        ▼                           │  > ~25 readable?  → screen_resumes_bulk   │
   extract text  ───► readable? ───►│     local TF-IDF pre-filter narrows to    │
   (pypdf /            │            │     top `shortlist_size` (~25)            │
    python-docx)       │ no         │                     ↓                     │
        │              ▼            │              judging packet               │
        │         report in        └─────────────────────┬─────────────────────┘
        │         `failed` list                           │
        │         (never dropped)                         ▼
        │                                    Claude Code scores each candidate
        │                                    0–100 on merit, ranks top-K + reasons
        └──────────────────────────────────────────────────────────────────────►
```

Why two stages: we can't dump 200 full resumes into one context window. A cheap, local,
**deterministic** TF-IDF + cosine-similarity pre-filter drops the obviously-irrelevant
resumes so Claude only judges a relevant shortlist. The pre-filter is *coarse keyword
matching, not a final judgment* — it carries a recall risk (a borderline-relevant resume
can be dropped), and the tools surface that honestly to the recruiter.

---

## Prerequisites

- **Python 3.11+**
- **[uv](https://docs.astral.sh/uv/)** — `curl -LsSf https://astral.sh/uv/install.sh | sh`
- **Claude Code** CLI on your PATH
- Claude Code must be **restarted** after registering the server (stdio MCP servers load
  only at startup).

## Install

There are two registration forms. Use **A** while developing locally; **B** is the
one-command install for sharing.

**A — local dev (editable):**
```bash
claude mcp add --scope user resume-screener -- \
  uv run --directory /ABSOLUTE/PATH/TO/resume-screener-mcp resume-screener-mcp
```

**B — one-command install (the deliverable; works once pushed to a Git host):**
```bash
claude mcp add --scope user resume-screener -- \
  uvx --from git+https://github.com/<your-github-username>/resume-screener-mcp resume-screener-mcp
```
`uvx` downloads the package and all its dependencies into an isolated cache and runs it in
one step — the Python equivalent of `npx`. No manual `pip install`, no virtualenv juggling.

After registering, **restart Claude Code**, then run `/mcp` to confirm `resume-screener`
is **connected**.

### Verify
In Claude Code:
- *"call the ping tool"* → `{"ok": true, "message": "Resume Screener MCP is alive"}`
- *"list resumes in /path/to/folder"* → counts + a `failed` list of unreadable files.

---

## Tool reference

| Tool | Use it when | Server scores? |
|------|-------------|----------------|
| `ping` | Health check before screening. | n/a |
| `list_resumes(folder)` | **First, always.** Inventory a folder and see which files are unreadable (scanned/encrypted/legacy `.doc`) before trusting results. | no |
| `screen_resumes(folder, job_description, top_k=5)` | Small folder (≤ ~25 resumes). Returns all as a judging packet. | no — Claude judges |
| `screen_resumes_bulk(folder, job_description, top_k=5, shortlist_size=25)` | Large folder (dozens to ~200). TF-IDF pre-filters, then returns the shortlist as a judging packet. | pre-filter only (local TF-IDF) |
| `compare_candidates(folder, filenames, job_description)` | Deep side-by-side of 2–4 named finalists after an initial screen. | no — Claude judges |
| `rerank(folder, job_description, emphasis, top_k=5, shortlist_size=25)` | Re-rank the same pile under a new priority ("weight AWS more heavily") without re-stating the JD. | pre-filter only |

Every screening tool returns a **judging packet** — the JD, a scoring rubric, and candidate
texts — and instructs Claude Code to score **on merit only** (ignoring name, gender, age,
nationality, photos, school prestige).

### Example session
```
You:    list resumes in C:\resumes\backend-role
Claude: Found 142 files, 138 readable. 4 unreadable:
        - 2 scanned PDFs (no extractable text — OCR not supported)
        - 1 password-protected PDF
        - 1 legacy .doc (convert to .docx or PDF)

You:    screen them against this JD: [paste JD]. Give me the top 5.
Claude: [calls screen_resumes_bulk → pre-filter narrows 138 → 25]
        Top 5 (138 extracted, 113 set aside by a coarse keyword pre-filter):
        1. henry_principal.pdf — 92 — 12 yrs Python/Go on AWS, led 50M-event platform
        2. alice_backend.pdf  — 88 — 8 yrs Python/AWS microservices, p99 latency work
        ...

You:    compare henry and alice in depth.
You:    now rerank weighting Kubernetes/EKS experience higher.
```

---

## Development

```bash
uv sync                      # create venv, install package + all deps
uv run resume-screener-mcp   # run the server standalone (Ctrl-C to stop)
uv run python -m pytest -q   # run the test suite (extractor, prefilter, tools)
uv run python tests/generate_samples.py   # regenerate sample resumes
```

Project layout (a real installable package — this is what makes the one-command install work):
```
resume-screener-mcp/
├── pyproject.toml                 # deps + console-script entry point
├── src/resume_screener/
│   ├── server.py                  # FastMCP app + the 6 tools
│   ├── extractor.py               # pure PDF/DOCX text extraction (never raises)
│   └── prefilter.py               # pure TF-IDF cosine-similarity pre-filter
├── tests/                         # pytest suite + sample-resume generator
└── docs/HOW_IT_WORKS.md           # deep walkthrough of the whole design
```

---

## Limitations & fairness (read this honestly)

- **Merit-only scoring, but not bias-free.** The rubric tells Claude to score on skills and
  relevant experience only and to ignore name/gender/age/nationality/photos/school prestige.
  But an LLM can still pick up *indirect* signals from resume text, so we do **not** claim
  this is bias-free. Final hiring decisions remain with humans.
- **No OCR.** Scanned/image PDFs have no extractable text and are reported as unreadable,
  never silently skipped.
- **The TF-IDF pre-filter is coarse.** On large folders it can drop a borderline-relevant
  resume before Claude ever sees it (a recall risk). The tools tell the recruiter how many
  were set aside and why, so those can be reviewed manually.
- **Scope is deliberately tight:** no web UI, no database, no embeddings/vector store, no
  API layer.

See [`docs/HOW_IT_WORKS.md`](docs/HOW_IT_WORKS.md) for a full explanation of every design
decision.
