#!/usr/bin/env python3
"""report_score.py — structural completeness score for people-osint reports.

Port of the People Search Bench 'Information Utility / structural
completeness' idea (MIT, https://github.com/LessieAI/people-search-bench):
weighted field coverage over the report schema, 0-100. Pure stdlib, no LLM.

Accepts the two report shapes validate_report.py accepts (deep-dive and
multi-candidate). For multi-candidate reports the score covers the shared
scaffold plus the best candidate.

Usage: report_score.py report.json [--out scores.json]
"""

import argparse
import json
import sys
from pathlib import Path

# field-path -> weight (identity-report weights, adapted from the bench's
# per-scenario tables). Deeper keys imply their parent must exist.
WEIGHTS = {
    "seed": 1.0,
    "verdict.identity": 1.0,
    "verdict.confidence": 0.8,
    "verdict.basis": 0.8,
    "profile.socials": 0.8,
    "profile.career": 0.8,
    "profile.location": 0.5,
    "profile.ventures": 0.5,
    "footprint": 0.5,
    "evidence": 1.0,
    "recommendations": 0.4,
}


def _get(obj: dict, path: str):
    cur = obj
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _filled(value) -> bool:
    if value is None:
        return False
    if isinstance(value, (dict,)):
        return bool(value)
    if isinstance(value, list):
        return bool(value)
    if isinstance(value, str):
        return bool(value.strip())
    return True  # numbers / bools


def score_report(report: dict) -> dict:
    if not isinstance(report, dict):
        return {"score": 0, "present": [], "missing": list(WEIGHTS)}
    present, missing, got, total = [], [], 0.0, 0.0
    for path, weight in WEIGHTS.items():
        total += weight
        value = _get(report, path)
        if _filled(value):
            present.append(path)
            got += weight
        else:
            missing.append(path)
    return {
        "score": round(100 * got / total, 1) if total else 0.0,
        "present": present,
        "missing": missing,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("report", help="report JSON file (either shape)")
    ap.add_argument("--out", help="optional output file")
    args = ap.parse_args(argv)

    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    # multi-candidate shape: score scaffold + best candidate merged.
    # "best" = rank 1 (the ranked verdict order), NOT max raw confidence —
    # a confident no_match is not a good result.
    candidates = report.get("candidates") or report.get("candidate")
    if isinstance(candidates, list) and candidates:
        ranked = [c for c in candidates if c.get("rank") is not None]
        best = min(ranked, key=lambda c: c["rank"]) if ranked else candidates[0]
        merged = {**report, **best}
        result = score_report(merged)
        result["shape"] = "multi-candidate"
    else:
        result = score_report(report)
        result["shape"] = "deep-dive"
    text = json.dumps(result, indent=2, ensure_ascii=False)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
        print(f"report-quality score: {result['score']}/100 -> {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
