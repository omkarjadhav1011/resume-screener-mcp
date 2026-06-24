"""Tests for collecting selected resumes into a folder (Phase U8)."""

import os

from resume_screener.collect import collect_resumes
from resume_screener.server import collect_selected


def _make_src(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    for name in ("a.pdf", "b.pdf", "c.pdf"):
        (src / name).write_text("dummy " + name)
    return src


# --- Unit: collect_resumes --------------------------------------------------

def test_copies_named_only(tmp_path):
    src = _make_src(tmp_path)
    dest = tmp_path / "out"
    res = collect_resumes(str(src), ["a.pdf", "b.pdf"], str(dest))
    assert res["ok"] is True
    assert set(res["copied"]) == {"a.pdf", "b.pdf"}
    assert (dest / "a.pdf").is_file() and (dest / "b.pdf").is_file()
    assert not (dest / "c.pdf").exists()  # only the named ones


def test_unknown_name_reported(tmp_path):
    src = _make_src(tmp_path)
    res = collect_resumes(str(src), ["a.pdf", "ghost.pdf"], str(tmp_path / "out"))
    assert res["copied"] == ["a.pdf"]
    assert res["not_found"] == ["ghost.pdf"]


def test_no_clobber_skips_existing(tmp_path):
    src = _make_src(tmp_path)
    dest = tmp_path / "out"
    collect_resumes(str(src), ["a.pdf"], str(dest))  # first copy
    res = collect_resumes(str(src), ["a.pdf"], str(dest))  # again
    assert res["copied"] == []
    assert res["skipped"] and res["skipped"][0]["filename"] == "a.pdf"
    assert "exists" in res["skipped"][0]["reason"]


def test_bad_source_folder(tmp_path):
    res = collect_resumes(str(tmp_path / "nope"), ["a.pdf"], str(tmp_path / "out"))
    assert res["ok"] is False and res["error"]


# --- Server tool ------------------------------------------------------------

def test_tool_explicit_dest(tmp_path):
    src = _make_src(tmp_path)
    dest = tmp_path / "finalists"
    res = collect_selected(str(src), ["a.pdf", "b.pdf"], dest_folder=str(dest))
    assert res["ok"] and (dest / "a.pdf").is_file()


def test_tool_default_dest_uses_selected_subfolder(tmp_path):
    src = _make_src(tmp_path)
    res = collect_selected(str(src), ["a.pdf"], role_label="Backend Finalists!")
    assert res["ok"]
    # dest is <src>/selected/<sanitized-label>
    assert os.path.basename(os.path.dirname(res["dest"])) == "selected"
    assert os.path.basename(res["dest"]) == "Backend-Finalists"
    assert os.path.isfile(os.path.join(res["dest"], "a.pdf"))


def test_tool_empty_filenames(tmp_path):
    src = _make_src(tmp_path)
    assert collect_selected(str(src), [])["ok"] is False
