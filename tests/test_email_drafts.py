"""Tests for editable email drafts (Phase U9)."""

import os

from resume_screener.email_drafts import (
    read_draft_file,
    render_draft,
    write_draft_file,
)
from resume_screener.server import draft_emails, get_email_templates


# --- Unit: render_draft -----------------------------------------------------

def test_render_fills_provided_variables():
    out = render_draft(
        "interview_invite",
        {"candidate_name": "Alice", "role": "Backend Engineer", "company": "Acme",
         "interview_format": "video", "time_options": "Tue 2pm / Wed 10am",
         "recruiter_name": "Sam"},
    )
    assert out["ok"]
    assert "Hi Alice," in out["body"]
    assert "Backend Engineer" in out["subject"]
    assert "[FILL:" not in out["body"]  # everything supplied


def test_render_marks_unfilled_placeholders():
    out = render_draft("interview_invite", {"candidate_name": "Alice"})
    assert out["ok"]
    # role/company/etc not provided -> visibly flagged, never silently blank
    assert "[FILL: role]" in out["subject"] or "[FILL: role]" in out["body"]
    assert "[FILL: recruiter_name]" in out["body"]


def test_render_unknown_template():
    out = render_draft("does_not_exist", {})
    assert out["ok"] is False and "Unknown template" in out["error"]


# --- Unit: write/read round-trip --------------------------------------------

def test_draft_file_roundtrip(tmp_path):
    path = write_draft_file(
        "alice@example.com", "Hello", "Hi Alice,\nLine two.",
        str(tmp_path / "alice.eml"),
    )
    parsed = read_draft_file(path)
    assert parsed["to"] == "alice@example.com"
    assert parsed["subject"] == "Hello"
    assert parsed["body"] == "Hi Alice,\nLine two."


# --- Server tools -----------------------------------------------------------

def test_get_email_templates():
    out = get_email_templates()
    assert out["ok"]
    assert set(out["templates"]) == {"interview_invite", "rejection", "request_info"}
    assert "placeholders" in out["templates"]["interview_invite"]


def test_draft_emails_writes_files(tmp_path):
    emails = [
        {"filename": "alice_backend.pdf", "to": "alice@example.com",
         "subject": "Interview", "body": "Hi Alice"},
        {"filename": "henry_principal.pdf", "to": "henry@example.com",
         "subject": "Interview", "body": "Hi Henry"},
    ]
    out = draft_emails(str(tmp_path / "finalists"), emails)
    assert out["ok"] and out["count"] == 2
    for w in out["written"]:
        assert os.path.isfile(w["path"])
        assert w["path"].endswith(".eml")
    # drafts live in a drafts/ subfolder
    assert os.path.basename(out["drafts_dir"]) == "drafts"


def test_draft_emails_missing_email_reported(tmp_path):
    emails = [
        {"filename": "alice.pdf", "to": "alice@example.com", "subject": "s", "body": "b"},
        {"filename": "priya.docx", "subject": "s", "body": "b"},  # no 'to'
    ]
    out = draft_emails(str(tmp_path / "f"), emails)
    assert out["count"] == 1
    assert out["missing_email"] == ["priya.docx"]


def test_draft_emails_template_path(tmp_path):
    emails = [{
        "filename": "alice.pdf", "to": "alice@example.com",
        "template": "interview_invite",
        "variables": {"candidate_name": "Alice", "role": "Backend Engineer",
                      "company": "Acme", "interview_format": "video",
                      "time_options": "Tue 2pm", "recruiter_name": "Sam"},
    }]
    out = draft_emails(str(tmp_path / "f"), emails)
    assert out["ok"] and out["count"] == 1
    parsed = read_draft_file(out["written"][0]["path"])
    assert "Hi Alice," in parsed["body"]
    assert parsed["to"] == "alice@example.com"


def test_draft_emails_empty_list(tmp_path):
    assert draft_emails(str(tmp_path / "f"), [])["ok"] is False


def test_draft_emails_stem_collision_no_overwrite(tmp_path):
    # Two selected resumes sharing a stem must produce two distinct drafts,
    # never silently overwrite each other.
    emails = [
        {"filename": "alice.pdf", "to": "a@x.com", "subject": "s1", "body": "b1"},
        {"filename": "alice.docx", "to": "a2@x.com", "subject": "s2", "body": "b2"},
    ]
    out = draft_emails(str(tmp_path / "f"), emails)
    assert out["count"] == 2
    paths = {w["path"] for w in out["written"]}
    assert len(paths) == 2  # distinct files, nothing clobbered
    for p in paths:
        assert os.path.isfile(p)
