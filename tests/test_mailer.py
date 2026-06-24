"""Tests for SMTP send (Phase U10). SMTP is MOCKED — no real mail is ever sent."""

import json
import os
from unittest.mock import patch

from resume_screener.email_drafts import write_draft_file
from resume_screener.server import send_emails


def _make_drafts(tmp_path, n=2):
    dest = tmp_path / "finalists"
    drafts = dest / "drafts"
    drafts.mkdir(parents=True)
    write_draft_file("alice@example.com", "Interview", "Hi Alice", str(drafts / "alice.eml"))
    if n > 1:
        write_draft_file("henry@example.com", "Interview", "Hi Henry", str(drafts / "henry.eml"))
    return dest


def _set_smtp_env(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "465")
    monkeypatch.setenv("SMTP_USER", "recruiter@example.com")
    monkeypatch.setenv("SMTP_PASS", "super-secret")
    monkeypatch.setenv("MAIL_FROM", "recruiter@example.com")


# --- Dry-run is the default and sends nothing -------------------------------

def test_dry_run_default_sends_nothing(tmp_path):
    dest = _make_drafts(tmp_path)
    with patch("resume_screener.mailer.smtplib.SMTP_SSL") as mock_smtp:
        out = send_emails(str(dest))  # dry_run defaults True
    assert out["ok"] and out["dry_run"] is True
    assert out["count"] == 2 and len(out["would_send"]) == 2
    mock_smtp.assert_not_called()  # the cardinal guarantee


# --- Double-gate: real send needs confirm -----------------------------------

def test_send_without_confirm_is_refused(tmp_path):
    dest = _make_drafts(tmp_path)
    with patch("resume_screener.mailer.smtplib.SMTP_SSL") as mock_smtp:
        out = send_emails(str(dest), dry_run=False, confirm=False)
    assert out["ok"] is False and "confirm" in out["error"].lower()
    mock_smtp.assert_not_called()


# --- Missing credentials -> clear error, never a crash ----------------------

def test_missing_env_errors(tmp_path, monkeypatch):
    for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS"):
        monkeypatch.delenv(k, raising=False)
    dest = _make_drafts(tmp_path)
    out = send_emails(str(dest), dry_run=False, confirm=True)
    assert out["ok"] is False and "SMTP" in out["error"]


# --- Real send (mocked): sends per recipient, writes log, hides password -----

def test_real_send_mocked(tmp_path, monkeypatch):
    _set_smtp_env(monkeypatch)
    dest = _make_drafts(tmp_path)
    with patch("resume_screener.mailer.smtplib.SMTP_SSL") as mock_smtp:
        server = mock_smtp.return_value.__enter__.return_value
        out = send_emails(str(dest), dry_run=False, confirm=True)
    assert out["ok"] and out["sent"] == 2 and out["failed"] == 0
    assert server.send_message.call_count == 2  # one per recipient

    # send_log.json written next to the drafts folder
    log_path = os.path.join(str(dest), "send_log.json")
    assert os.path.isfile(log_path)
    with open(log_path, encoding="utf-8") as f:
        entries = json.load(f)
    assert len(entries) == 2 and all(e["status"] == "sent" for e in entries)
    # the password must NEVER appear in the log or the result
    assert "super-secret" not in json.dumps(entries)
    assert "super-secret" not in json.dumps(out)


def test_per_recipient_failure_is_not_fatal(tmp_path, monkeypatch):
    _set_smtp_env(monkeypatch)
    dest = _make_drafts(tmp_path)
    # First send raises, second succeeds — one bounce must not stop the rest.
    with patch("resume_screener.mailer.send_one") as mock_send:
        mock_send.side_effect = [(False, "SMTPRecipientsRefused"), (True, "sent")]
        out = send_emails(str(dest), dry_run=False, confirm=True)
    assert out["ok"] and out["sent"] == 1 and out["failed"] == 1


def test_no_drafts_folder(tmp_path):
    out = send_emails(str(tmp_path / "empty"))
    assert out["ok"] is False and "draft" in out["error"].lower()
