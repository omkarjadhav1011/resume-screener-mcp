"""Contract tests for the MCP tool functions (called directly, no MCP layer)."""

import os

import pytest

from resume_screener.server import (
    compare_candidates,
    list_resumes,
    rerank,
    screen_resumes,
    screen_resumes_bulk,
)

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLES = os.path.join(HERE, "sample_resumes")
JD = "Senior Backend Engineer: Python, FastAPI, AWS, microservices, PostgreSQL."


@pytest.fixture(scope="session", autouse=True)
def ensure_samples():
    if not os.path.isdir(SAMPLES) or not os.listdir(SAMPLES):
        import generate_samples

        generate_samples.main()


def test_list_resumes_reports_failures():
    out = list_resumes(SAMPLES)
    assert out["ok"] and out["parsed"] >= 8
    reasons = {f["filename"]: f["reason"] for f in out["failed"]}
    assert "scanned_resume.pdf" in reasons
    assert "old_resume.doc" in reasons


def test_list_resumes_bad_folder():
    out = list_resumes(os.path.join(SAMPLES, "nope"))
    assert out["ok"] is False and out["error"]


def test_screen_resumes_packet_shape():
    out = screen_resumes(SAMPLES, JD, top_k=5)
    assert out["ok"]
    assert out["scoring_rubric"] and out["candidate_count"] >= 8
    assert all({"filename", "text"} <= c.keys() for c in out["candidates"])
    # server must NOT invent scores
    assert all("score" not in c for c in out["candidates"])


def test_screen_resumes_empty_jd():
    assert screen_resumes(SAMPLES, "  ", 5)["ok"] is False


def test_screen_resumes_top_k_clamped():
    out = screen_resumes(SAMPLES, JD, top_k=999)
    assert out["top_k"] == out["candidate_count"]
    assert "top_k_note" in out


def test_bulk_metadata_present():
    out = screen_resumes_bulk(SAMPLES, JD, top_k=5, shortlist_size=4)
    assert out["ok"]
    assert out["shortlisted"] == 4
    assert out["total_extracted"] >= 8
    assert out["prefiltered_out"] == out["total_extracted"] - 4
    assert "prefilter_note" in out
    # strongest backend resume survives a tight shortlist
    names = [c["filename"] for c in out["candidates"]]
    assert "alice_backend.pdf" in names


def test_compare_candidates():
    out = compare_candidates(SAMPLES, ["alice_backend.pdf", "ghost.pdf"], JD)
    assert out["ok"]
    assert out["not_found"] == ["ghost.pdf"]
    assert any(c["filename"] == "alice_backend.pdf" and c["text"] for c in out["candidates"])


def test_compare_requires_2_to_4():
    assert compare_candidates(SAMPLES, ["alice_backend.pdf"], JD)["ok"] is False


def test_rerank_shifts_order_and_carries_emphasis():
    out = rerank(SAMPLES, JD, emphasis="weight data pipeline, Spark, Airflow, streaming",
                 top_k=5, shortlist_size=9)
    assert out["ok"] and out["emphasis"]
    assert "ADDITIONAL EMPHASIS" in out["scoring_rubric"]
    # data emphasis should pull the data engineer toward the top of the shortlist
    names = [c["filename"] for c in out["candidates"]]
    assert names.index("carol_data.pdf") < names.index("bob_fullstack.pdf")


def test_rerank_requires_emphasis():
    assert rerank(SAMPLES, JD, "   ")["ok"] is False
