"""SMTP send for email drafts (Phase U10) — the one network-touching action.

Sending mail to real candidates is irreversible and outward-facing, so this
module is wrapped in guards:
  - Dry-run is the DEFAULT: it previews every email and sends nothing.
  - Real sending requires BOTH dry_run=False AND confirm=True (double gate).
  - Credentials come ONLY from env vars and are NEVER logged or returned.
  - Every send is appended to <dest>/send_log.json for auditability.
  - Per-recipient failures are collected, not fatal — one bounce won't stop the
    rest. Never raises to the caller.

Stdlib only (smtplib, ssl, email) — no new dependency.
"""

from __future__ import annotations

import json
import os
import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage

from resume_screener.email_drafts import read_draft_file

_REQUIRED_ENV = ("SMTP_HOST", "SMTP_USER", "SMTP_PASS")


def smtp_config_from_env() -> tuple[dict | None, str | None]:
    """Load and validate SMTP config from the environment. Returns (cfg, error).

    Required: SMTP_HOST, SMTP_USER, SMTP_PASS. Optional: SMTP_PORT (default 465),
    MAIL_FROM (defaults to SMTP_USER), SMTP_SECURITY ('ssl'|'starttls', inferred
    from the port if unset). The password is never logged."""
    missing = [k for k in _REQUIRED_ENV if not os.environ.get(k)]
    if missing:
        return None, (
            f"Missing SMTP env var(s): {', '.join(missing)}. Set SMTP_HOST, "
            "SMTP_PORT, SMTP_USER, SMTP_PASS and MAIL_FROM to send."
        )
    port_raw = os.environ.get("SMTP_PORT", "465")
    try:
        port = int(port_raw)
    except ValueError:
        return None, f"SMTP_PORT must be a number, got {port_raw!r}."

    security = os.environ.get(
        "SMTP_SECURITY", "ssl" if port == 465 else "starttls"
    ).lower()
    cfg = {
        "host": os.environ["SMTP_HOST"],
        "port": port,
        "user": os.environ["SMTP_USER"],
        "password": os.environ["SMTP_PASS"],
        "mail_from": os.environ.get("MAIL_FROM") or os.environ["SMTP_USER"],
        "security": security,
    }
    return cfg, None


def send_one(cfg: dict, to: str, subject: str, body: str) -> tuple[bool, str]:
    """Send a single message. Returns (ok, detail). Never raises."""
    try:
        msg = EmailMessage()
        msg["From"] = cfg["mail_from"]
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)
        context = ssl.create_default_context()
        if cfg["security"] == "starttls":
            with smtplib.SMTP(cfg["host"], cfg["port"]) as s:
                s.starttls(context=context)
                s.login(cfg["user"], cfg["password"])
                s.send_message(msg)
        else:
            with smtplib.SMTP_SSL(cfg["host"], cfg["port"], context=context) as s:
                s.login(cfg["user"], cfg["password"])
                s.send_message(msg)
        return True, "sent"
    except Exception as exc:
        # Detail is safe to surface (type + message); credentials are never here.
        return False, f"{type(exc).__name__}: {exc}"


def _load_drafts(dest_folder: str) -> tuple[list[dict] | None, str, str | None]:
    """Read every .eml in <dest>/drafts/. Returns (drafts, drafts_dir, error)."""
    drafts_dir = os.path.join(os.path.abspath(dest_folder), "drafts")
    if not os.path.isdir(drafts_dir):
        return None, drafts_dir, (
            "No drafts/ folder found — run draft_emails first."
        )
    drafts: list[dict] = []
    for fn in sorted(os.listdir(drafts_dir)):
        if fn.lower().endswith(".eml"):
            d = read_draft_file(os.path.join(drafts_dir, fn))
            d["file"] = fn
            drafts.append(d)
    return drafts, drafts_dir, None


def send_drafts(dest_folder: str, dry_run: bool = True, confirm: bool = False) -> dict:
    """Send (or preview) the drafts in <dest>/drafts/. See module docstring for
    the safety model. Never raises."""
    drafts, drafts_dir, err = _load_drafts(dest_folder)
    if err:
        return {"ok": False, "error": err}
    if not drafts:
        return {"ok": True, "count": 0, "note": "No .eml drafts to send."}

    # DRY RUN (default) — preview only, send nothing.
    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "count": len(drafts),
            "would_send": [
                {"to": d["to"], "subject": d["subject"], "body": d["body"]}
                for d in drafts
            ],
            "note": (
                "Preview only — nothing was sent. Show the recruiter these emails; "
                "to actually send, call again with dry_run=False AND confirm=True "
                "after they approve."
            ),
        }

    # Double gate: real sending requires explicit confirm.
    if not confirm:
        return {
            "ok": False,
            "error": (
                "Refusing to send: real sending requires dry_run=False AND "
                "confirm=True. Show the recruiter the dry-run preview and get "
                "explicit approval before sending."
            ),
        }

    cfg, cfg_err = smtp_config_from_env()
    if cfg_err:
        return {"ok": False, "error": cfg_err}

    results: list[dict] = []
    log_entries: list[dict] = []
    for d in drafts:
        to = d["to"]
        if not to:
            results.append(
                {"file": d["file"], "to": "", "status": "skipped", "detail": "no recipient"}
            )
            continue
        ok, detail = send_one(cfg, to, d["subject"], d["body"])
        status = "sent" if ok else "failed"
        results.append({"file": d["file"], "to": to, "status": status, "detail": detail})
        log_entries.append({
            "to": to,
            "subject": d["subject"],
            "status": status,
            "detail": detail,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
        })

    # Append to the audit log (never contains credentials).
    log_path = os.path.join(os.path.dirname(drafts_dir), "send_log.json")
    log_warning = None
    try:
        existing = []
        if os.path.isfile(log_path):
            with open(log_path, encoding="utf-8") as f:
                existing = json.load(f)
        existing.extend(log_entries)
        with open(log_path, "w", encoding="utf-8") as f:
            json.dump(existing, f, indent=2)
    except Exception as exc:
        log_warning = f"Could not write send log ({type(exc).__name__})."

    out = {
        "ok": True,
        "dry_run": False,
        "sent": sum(1 for r in results if r["status"] == "sent"),
        "failed": sum(1 for r in results if r["status"] == "failed"),
        "results": results,
        "send_log": log_path,
    }
    if log_warning:
        out["log_warning"] = log_warning
    return out
