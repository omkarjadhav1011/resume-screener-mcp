"""Duplicate / near-duplicate candidate detection (Phase U6).

The same person often appears twice in a pile — re-applied, or submitted a lightly
edited resume under a new filename. Reviewing 200 files one-by-one, you won't
notice; a whole-pile scan will. This is a deterministic, local flag-for-review —
it groups likely duplicates with a reason, it does NOT auto-merge or auto-remove
anyone (the recruiter decides).

Three signals, combined via union-find so a group can be linked by any of them:
  1. Same email (from parsed fields) — a near-certain duplicate.
  2. Identical normalized text — an exact re-submission (cheap hash-equality).
  3. Near-identical text — difflib similarity above a threshold, with cheap gates
     (length ratio, quick_ratio) first so it stays affordable at ~200 files.

Pure, deterministic, never raises.
"""

from __future__ import annotations

import difflib
import re
from itertools import combinations

# Texts at/above this similarity ratio are flagged as near-duplicates.
NEAR_THRESHOLD = 0.90
# Skip pairs whose lengths differ by more than this (cheap pre-gate).
_LEN_RATIO_GATE = 0.8


def _norm_key(text: str) -> str:
    """Whitespace-collapsed, lowercased key for exact-duplicate detection."""
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def find_duplicates(records: list[dict], near_threshold: float = NEAR_THRESHOLD) -> list[dict]:
    """Group likely-duplicate records. Returns [{members:[filenames], reason}].

    `records` are extract_resume records (uses fields.email and text). Only groups
    of 2+ are returned; singletons are omitted. Deterministic (sorted output).
    Never raises."""
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

    # 3. Near-identical text — only for pairs not already grouped, with cheap gates.
    texts = [r.get("text", "") or "" for r in records]
    for a, b in combinations(range(n), 2):
        if find(a) == find(b):
            continue
        ta, tb = texts[a], texts[b]
        if not ta or not tb:
            continue
        la, lb = len(ta), len(tb)
        if min(la, lb) / max(la, lb) < _LEN_RATIO_GATE:
            continue
        # autojunk=False is ESSENTIAL here: difflib's autojunk heuristic treats
        # characters appearing in >1% of a >200-char sequence as junk, which for
        # resume-length text means nearly every character — collapsing ratio() to
        # near-zero on exactly the long text we want to compare.
        sm = difflib.SequenceMatcher(None, ta, tb, autojunk=False)
        # real_quick_ratio()/quick_ratio() are cheap upper bounds on ratio().
        if sm.real_quick_ratio() < near_threshold or sm.quick_ratio() < near_threshold:
            continue
        ratio = sm.ratio()
        if ratio >= near_threshold:
            union(a, b)
            edges.append((a, b, f"near-identical text ({int(round(ratio * 100))}% similar)"))

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
