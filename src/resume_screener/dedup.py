"""Duplicate / near-duplicate candidate detection (Phase U6).

The same person often appears twice in a pile — re-applied, or submitted a lightly
edited resume under a new filename. Reviewing 200 files one-by-one, you won't
notice; a whole-pile scan will. This is a deterministic, local flag-for-review —
it groups likely duplicates with a reason, it does NOT auto-merge or auto-remove
anyone (the recruiter decides).

Three signals, combined via union-find so a group can be linked by any of them:
  1. Same email (from parsed fields) — a near-certain duplicate.
  2. Identical normalized text — an exact re-submission (cheap hash-equality).
  3. Near-identical text — token-set Jaccard above a threshold. O(set-ops) per
     pair, so it stays fast even on a pile of many near-duplicates (~200 files).

Pure, deterministic, never raises.
"""

from __future__ import annotations

import re
from itertools import combinations

# Token-set Jaccard at/above this is flagged as a near-duplicate. Jaccard
# (|A∩B|/|A∪B| over the word sets) is a standard near-dup measure — order- and
# frequency-insensitive, O(set-ops) per pair (no O(n*m) char matching), and far
# more discriminative for natural-language text than character overlap. Two
# distinct same-field resumes share tech terms but differ in companies/projects/
# numbers (Jaccard ~0.2-0.4); a re-submitted resume scores ~0.85+.
NEAR_THRESHOLD = 0.75

_WORD_RE = re.compile(r"[a-z0-9]+")


def _norm_key(text: str) -> str:
    """Whitespace-collapsed, lowercased key for exact-duplicate detection."""
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def find_duplicates(records: list[dict], near_threshold: float = NEAR_THRESHOLD) -> list[dict]:
    """Group likely-duplicate records. Returns [{members:[filenames], reason}].

    `records` are extract_resume records (uses fields.email and text). Only groups
    of 2+ are returned; singletons are omitted. Deterministic (sorted output).
    `near_threshold` is the token-set Jaccard cutoff for near-duplicates. Never
    raises."""
    n = len(records)
    if n < 2:
        return []

    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj

    edges: list[tuple[int, int, str]] = []

    # 1. Same email.
    email_map: dict[str, list[int]] = {}
    for idx, r in enumerate(records):
        email = ((r.get("fields") or {}).get("email") or "").lower().strip()
        if email:
            email_map.setdefault(email, []).append(idx)
    for email, idxs in email_map.items():
        for a, b in combinations(idxs, 2):
            union(a, b)
            edges.append((a, b, f"same email ({email})"))

    # 2. Identical normalized text (exact re-submission).
    content_map: dict[str, list[int]] = {}
    for idx, r in enumerate(records):
        key = _norm_key(r.get("text", ""))
        if key:
            content_map.setdefault(key, []).append(idx)
    for key, idxs in content_map.items():
        if len(idxs) > 1:
            for a, b in combinations(idxs, 2):
                union(a, b)
                edges.append((a, b, "identical resume text"))

    # 3. Near-identical text via token-set Jaccard — for pairs not already grouped.
    # O(set-ops) per pair, so even a pile of many near-duplicates stays fast (no
    # O(n*m) char matching that could blow up to minutes on similar text).
    token_sets = [set(_WORD_RE.findall((r.get("text", "") or "").lower())) for r in records]
    for a, b in combinations(range(n), 2):
        if find(a) == find(b):
            continue
        sa, sb = token_sets[a], token_sets[b]
        if not sa or not sb:
            continue
        inter = len(sa & sb)
        if inter == 0:
            continue
        jaccard = inter / len(sa | sb)
        if jaccard >= near_threshold:
            union(a, b)
            edges.append(
                (a, b, f"near-identical text ({int(round(jaccard * 100))}% word overlap)")
            )

    # Assemble groups of size >= 2.
    groups: dict[int, list[int]] = {}
    for idx in range(n):
        groups.setdefault(find(idx), []).append(idx)

    result: list[dict] = []
    for root, idxs in groups.items():
        if len(idxs) < 2:
            continue
        members = sorted(records[i]["filename"] for i in idxs)
        reasons = sorted({reason for (a, b, reason) in edges if find(a) == root})
        result.append({"members": members, "reason": "; ".join(reasons)})

    result.sort(key=lambda g: g["members"])
    return result
