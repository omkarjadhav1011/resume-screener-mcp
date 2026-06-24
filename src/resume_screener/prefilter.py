"""Pure TF-IDF pre-filter logic.

Deterministic, local, NO LLM. This is a *coarse* recall filter whose only job is
to drop obviously-irrelevant resumes so we don't hand 200 full texts to Claude
at once. It is NOT a final judgment — Claude scores the shortlist it produces.

TF-IDF + cosine similarity in plain terms:
  - Term Frequency: how often a word appears in a document.
  - Inverse Document Frequency: rare words (e.g. "Kubernetes") carry more signal
    than common ones (e.g. "experience"), so they're weighted higher.
  - Each document (the JD and each resume) becomes a vector of TF-IDF weights.
  - Cosine similarity measures the angle between the JD vector and each resume
    vector: 1.0 = same direction (very similar), 0.0 = orthogonal (no shared
    meaningful terms).
"""

from __future__ import annotations

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity


def prefilter(candidates: list[dict], jd: str, shortlist_size: int) -> list[dict]:
    """Rank candidates by TF-IDF cosine similarity to the JD; return the top
    `shortlist_size`, each with a `prefilter_score` (0-1) attached.

    `candidates` are records with at least `filename` and `text`. Input order is
    preserved as a tiebreaker so results are deterministic. Never raises on the
    normal path; an empty candidate list returns []."""
    if not candidates:
        return []
    if shortlist_size <= 0:
        return []

    texts = [c.get("text", "") for c in candidates]
    # The JD is the first document; resumes follow.
    corpus = [jd] + texts

    # stop_words filters out common English words so they don't dominate; the
    # vectorizer learns IDF weights across the JD + all resumes together.
    vectorizer = TfidfVectorizer(stop_words="english")
    try:
        matrix = vectorizer.fit_transform(corpus)
    except ValueError:
        # Happens only if the entire corpus has no usable terms (e.g. empty JD
        # and empty resumes). Fall back to original order with zero scores.
        return [
            {**c, "prefilter_score": 0.0}
            for c in candidates[:shortlist_size]
        ]

    jd_vec = matrix[0]
    resume_matrix = matrix[1:]
    scores = cosine_similarity(jd_vec, resume_matrix)[0]

    ranked = sorted(
        ({**c, "prefilter_score": round(float(score), 4)}
         for c, score in zip(candidates, scores)),
        key=lambda c: c["prefilter_score"],
        reverse=True,
    )
    return ranked[:shortlist_size]
