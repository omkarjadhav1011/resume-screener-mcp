"""Collect selected resumes into a folder (Phase U8).

After Claude produces a shortlist, the recruiter wants those exact files in one
place. This is a pure file operation: Claude decides WHO; the server copies the
files. Reuses extractor.find_resume_files for name resolution so behavior matches
the rest of the toolset.

Cardinal rule preserved: never silently lose or silently overwrite. An existing
destination file is SKIPPED and reported (not clobbered); an unknown name is
reported in not_found. Never raises.
"""

from __future__ import annotations

import os
import shutil

from resume_screener.extractor import find_resume_files


def collect_resumes(folder: str, filenames: list[str], dest_folder: str) -> dict:
    """Copy the named resumes from `folder` into `dest_folder`.

    Returns {ok, dest, copied, not_found, skipped, error?}:
      - copied: filenames successfully copied.
      - not_found: requested names not present in the source folder.
      - skipped: [{filename, reason}] — already-present or copy-failed (never
        overwritten).
    Never raises."""
    resume_paths, _legacy, error = find_resume_files(folder)
    if error:
        return {
            "ok": False, "error": error, "dest": None,
            "copied": [], "not_found": [], "skipped": [],
        }

    by_name = {os.path.basename(p): p for p in resume_paths}
    dest = os.path.abspath(dest_folder)
    try:
        os.makedirs(dest, exist_ok=True)
    except Exception as exc:
        return {
            "ok": False,
            "error": f"Couldn't create destination folder ({type(exc).__name__}): {dest}",
            "dest": dest, "copied": [], "not_found": [], "skipped": [],
        }

    copied: list[str] = []
    not_found: list[str] = []
    skipped: list[dict] = []
    for name in filenames:
        src = by_name.get(name)
        if src is None:
            not_found.append(name)
            continue
        target = os.path.join(dest, name)
        if os.path.exists(target):
            skipped.append({"filename": name, "reason": "already exists in destination"})
            continue
        try:
            shutil.copy2(src, target)  # copy2 preserves metadata (mtime etc.)
            copied.append(name)
        except Exception as exc:
            skipped.append({"filename": name, "reason": f"copy failed: {type(exc).__name__}"})

    return {
        "ok": True, "dest": dest,
        "copied": copied, "not_found": not_found, "skipped": skipped,
    }
