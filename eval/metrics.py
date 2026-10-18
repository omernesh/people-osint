#!/usr/bin/env python3
"""Scoring math for the people-osint eval harness (pure functions, no I/O).

Padded-nDCG, precision@k, coverage and aggregate scoring, ported from the
methodology of LessieAI/people-search-bench (MIT License,
https://github.com/LessieAI/people-search-bench). Relevance precision is a
zero-padded nDCG@k whose ideal DCG assumes k perfect results exist, so
returning 3 perfect results scores below returning 10; effective coverage is
task-completion rate times capped qualified yield. Per-person relevance
grades (0..1) are produced outside this module by the bench's grading
pipeline; nothing here touches a network, an LLM, or the filesystem.
"""
import math
from statistics import fmean, pstdev

RELEVANCE_THRESHOLD = 0.5  # grade >= this counts as a "qualified" person
DEFAULT_K = 10             # bench scores nDCG@10 and coverage capped at K=10


def dcg(grades, k=DEFAULT_K):
    """Discounted cumulative gain over the first k grades.

    DCG = sum(rel_i / log2(i + 2)) with i counting from 0, so rank 1 gets
    discount 1/log2(2) = 1.0. Grades beyond k are ignored.
    """
    return sum(g / math.log2(i + 2) for i, g in enumerate(grades[:k]))


def padded_ndcg(grades, k=DEFAULT_K):
    """nDCG@k padded against an ideal of k perfect results.

    The ideal DCG is DCG([1.0] * k, k) regardless of how many results were
    actually returned, so a short perfect list scores below a full one.
    Empty (or all-zero-denominator) input scores 0.0.
    """
    if not grades:
        return 0.0
    ideal = dcg([1.0] * k, k)
    return dcg(grades, k) / ideal if ideal else 0.0


def precision_at_k(grades, k=DEFAULT_K, threshold=RELEVANCE_THRESHOLD):
    """Share of qualified persons among the returned top-k.

    Denominator is len(top-k) (not k), matching the bench: returning fewer
    results shrinks the list, not the standard. Empty input scores 0.0.
    """
    top = grades[:k]
    if not top:
        return 0.0
    qualified = sum(1 for g in top if g >= threshold)
    return qualified / len(top)


def coverage_score(num_qualified, requested_k):
    """min(qualified / requested_k, 1) — qualified yield per query, capped."""
    if requested_k <= 0:
        return 0.0
    return min(num_qualified / requested_k, 1.0)


def aggregate(rows, total_queries=None):
    """Aggregate per-query rows into platform-level scores (bench formulas).

    Each row is a dict:
      {"query_id": str, "grades": [0..1 floats], "requested_k": int,
       "num_qualified": int or None}
    `num_qualified` defaults to counting grades >= RELEVANCE_THRESHOLD.
    Failed queries zero-pad: a row with no grades contributes 0 to every
    sum but still counts in the denominator. If `total_queries` exceeds
    len(rows), the missing queries are padded the same way (a platform that
    answered 3 of 119 queries is scored over 119).

    Returns a dict (all scores on a 0..1 scale; multiply by 100 to display):
      relevance_precision  mean padded nDCG over all queries (zero-padded)
      effective_coverage   TCR * mean(coverage), TCR = share of queries
                           with >= 1 qualified result
      overall              equal-weight mean of the TWO dimensions above.
                           NOT comparable to the bench's `overall`, which
                           also averages in Information Utility (an LLM-
                           graded third dimension this harness does not
                           compute). Compare dimension by dimension.
      tcr, mean_coverage, n_queries, total_queries  (breakdown)
    """
    if total_queries is None:
        total_queries = len(rows)
    total_queries = max(total_queries, len(rows))
    if total_queries == 0:
        return {"relevance_precision": 0.0, "effective_coverage": 0.0,
                "overall": 0.0, "tcr": 0.0, "mean_coverage": 0.0,
                "n_queries": 0, "total_queries": 0}
    ndcg_sum = 0.0
    cov_sum = 0.0
    completed = 0
    for r in rows:
        grades = r.get("grades") or []
        k = r.get("requested_k", DEFAULT_K) or DEFAULT_K
        qualified = r.get("num_qualified")
        if qualified is None:
            qualified = sum(1 for g in grades if g >= RELEVANCE_THRESHOLD)
        # Relevance precision is ALWAYS padded nDCG@10 in the bench
        # (benchmark/metrics.py compute_platform_scores uses q.ndcg_10);
        # requested_k only sets the coverage yield denominator. Using
        # requested_k here scored nDCG@15/@25 whenever a query asked for more.
        ndcg_sum += padded_ndcg(grades, DEFAULT_K)
        cov_sum += coverage_score(qualified, k)
        if qualified >= 1:
            completed += 1
    relevance_precision = ndcg_sum / total_queries
    mean_coverage = cov_sum / total_queries
    tcr = completed / total_queries
    effective_coverage = tcr * mean_coverage
    return {
        "relevance_precision": relevance_precision,
        "effective_coverage": effective_coverage,
        "overall": fmean([relevance_precision, effective_coverage]),
        "tcr": tcr,
        "mean_coverage": mean_coverage,
        "n_queries": len(rows),
        "total_queries": total_queries,
    }


def consistency_score(relevances):
    """Cross-category consistency on a 0..100 scale.

    100 * max(0, 1 - pstdev/mean) over per-category relevance scores:
    identical categories score 100, wildly uneven ones score low, and an
    empty list or an all-zero mean scores 0.0.
    """
    if not relevances:
        return 0.0
    mean = fmean(relevances)
    if mean == 0:
        return 0.0
    return 100.0 * max(0.0, 1.0 - pstdev(relevances) / mean)
