"""Tests for the TF-IDF pre-filter (deterministic, local, no LLM)."""

from resume_screener.prefilter import prefilter

CANDIDATES = [
    {"filename": "backend.pdf", "text": "Python FastAPI AWS microservices PostgreSQL Kubernetes"},
    {"filename": "nurse.pdf", "text": "ICU patient care triage IV therapy Epic EHR"},
    {"filename": "frontend.pdf", "text": "React Vue CSS design systems accessibility Figma"},
    {"filename": "data.pdf", "text": "Python SQL Spark Airflow AWS Redshift data pipelines"},
]
JD = "Backend engineer: Python, AWS, microservices, PostgreSQL, Kubernetes."


def test_relevant_ranked_first():
    ranked = prefilter(CANDIDATES, JD, shortlist_size=4)
    # backend is the clear match → top
    assert ranked[0]["filename"] == "backend.pdf"
    # data also shares Python/AWS → should outrank the off-target nurse/frontend
    names = [r["filename"] for r in ranked]
    assert names.index("data.pdf") < names.index("nurse.pdf")
    assert names.index("data.pdf") < names.index("frontend.pdf")


def test_shortlist_size_limits_results():
    ranked = prefilter(CANDIDATES, JD, shortlist_size=2)
    assert len(ranked) == 2
    assert {"backend.pdf", "data.pdf"} >= {ranked[0]["filename"]}


def test_scores_attached_and_in_range():
    ranked = prefilter(CANDIDATES, JD, shortlist_size=4)
    for r in ranked:
        assert 0.0 <= r["prefilter_score"] <= 1.0
    # nurse shares nothing with the JD → ~0
    nurse = next(r for r in ranked if r["filename"] == "nurse.pdf")
    assert nurse["prefilter_score"] == 0.0


def test_empty_inputs_safe():
    assert prefilter([], JD, 5) == []
    assert prefilter(CANDIDATES, JD, 0) == []


def test_all_empty_text_falls_back_no_raise():
    cands = [{"filename": "a.pdf", "text": ""}, {"filename": "b.pdf", "text": ""}]
    out = prefilter(cands, "", 2)
    assert len(out) == 2
    assert all(c["prefilter_score"] == 0.0 for c in out)
