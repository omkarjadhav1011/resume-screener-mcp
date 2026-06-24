"""Write a judged shortlist to a recruiter artifact — CSV or XLSX (Phase U5).

Why this exists: Claude produces the ranking *in the conversation*, which vanishes
when the chat ends. A recruiter needs a file to hand to a hiring manager. The
server doesn't score (Claude does) — it only FORMATS and PERSISTS the rows Claude
already judged. That division keeps the "no scoring in the server" rule intact.

Pure formatting/IO: CSV via the stdlib, XLSX via openpyxl. These build the file
and may raise on a genuine IO error; the calling tool wraps them so the client
only ever sees a structured result.
"""

from __future__ import annotations

import csv
import os

# Columns we emit first, when present, in this order. Any extra keys Claude
# included follow in first-seen order, so nothing is dropped.
_PREFERRED = ["rank", "filename", "score", "reason", "email", "phone", "years_experience"]


def _columns(rows: list[dict]) -> list[str]:
    """Ordered column list: preferred keys that appear, then any extras."""
    cols = [k for k in _PREFERRED if any(k in r for r in rows)]
    for r in rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    return cols


def _cell(value):
    """Coerce a value to something a spreadsheet cell can hold."""
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple, set)):
        return str(value)
    return value


def write_csv(rows: list[dict], out_path: str) -> str:
    """Write rows to a UTF-8 CSV (BOM so Excel opens accents correctly). Returns
    the absolute path written."""
    cols = _columns(rows)
    abspath = os.path.abspath(out_path)
    # utf-8-sig: Excel needs the BOM to render non-ASCII names correctly.
    with open(abspath, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(cols)
        for r in rows:
            writer.writerow([_cell(r.get(c)) for c in cols])
    return abspath


def write_xlsx(rows: list[dict], out_path: str) -> str:
    """Write rows to an .xlsx with a bold header row. Returns the absolute path."""
    from openpyxl import Workbook
    from openpyxl.styles import Font

    cols = _columns(rows)
    abspath = os.path.abspath(out_path)
    wb = Workbook()
    ws = wb.active
    ws.title = "Shortlist"
    ws.append(cols)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for r in rows:
        ws.append([_cell(r.get(c)) for c in cols])
    wb.save(abspath)
    return abspath


WRITERS = {"csv": write_csv, "xlsx": write_xlsx}
