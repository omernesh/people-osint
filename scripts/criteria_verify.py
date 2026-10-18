#!/usr/bin/env python3
"""criteria_verify.py — criteria-grounded candidate verification + ranking.

Upgrades the flat one-question verdict (scan.py) with the People Search
Bench 'Criteria-Grounded Verification' idea (MIT,
https://github.com/LessieAI/people-search-bench): each seed field becomes an
explicit criterion, each candidate cluster (cluster.py) is graded
three-level per criterion (met 1.0 / partially_met 0.5 / not_met 0.0) via
the typed-choice JEV call, then ranked. The final identity verdict per
candidate still comes from the existing typed-choice verify_identity —
this module adds granularity and ranking, never replaces the typed verdict.

Input: evidence file (sweep output) + seed (--name, optional --phone/--email/--social).
Output: JSON {seed, engine, candidates: [{key, display, size, relevance_grade,
criteria: [{id, description, grade, confidence, sample_url}], verdict,
confidence, rank}]} — stdout or --out file. Exit 0 ok / 3 engine error.
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

from cluster import cluster_rows
from evidence import engine_failed, load_evidence, to_jev_rows
from jev_verify import classify_retry, verify_identity_retry

PACE = 1.5  # seconds between JEV calls — upstream bans are real


def build_criteria(seed: dict) -> list[dict]:
    """Deterministic criteria from the seed (same seed = same criteria)."""
    criteria = []
    if seed.get("name"):
        criteria.append(
            {
                "id": "c_name",
                "description": (
                    f"The page/profile refers to a person named '{seed['name']}' "
                    "— BOTH first and last name must appear (exact or a clear "
                    "variant spelling). A bare first name alone does NOT "
                    "satisfy this criterion."
                ),
            }
        )
    if seed.get("phone"):
        tail = re.sub(r"\D", "", seed["phone"])[-7:]
        criteria.append(
            {
                "id": "c_phone",
                # the judge must SEE the number it is asked to find — the old
                # text named "the seed number" without ever including it
                "description": (
                    f"The page associates a phone number ending in '{tail}' "
                    "(the seed number's last 7 digits, any formatting) with "
                    "this person."
                ),
            }
        )
    if seed.get("email"):
        criteria.append(
            {
                "id": "c_email",
                "description": (
                    f"The page associates the seed email address "
                    f"('{seed['email']}') or its local part with this person."
                ),
            }
        )
    if seed.get("social"):
        criteria.append(
            {
                "id": "c_social",
                "description": (
                    f"The page links to the seed social profile "
                    f"('{seed['social']}') or its handle."
                ),
            }
        )
    criteria.append(
        {
            "id": "c_identity",
            "description": (
                "The evidence provides at least one identifying signal beyond "
                "the bare name (role, company, location, photo context, "
                "co-mentioned associates)."
            ),
        }
    )
    return criteria


# "insufficient" scores 0.0, like the bench (a criterion is met, partially
# met, or not demonstrated). Leaving it out of the mean let a candidate with
# one "met" and four "insufficient" criteria rank at relevance 1.0. Only an
# engine failure (choice None) is excluded from the mean.
GRADE_MAP = {"met": 1.0, "partially_met": 0.5, "not_met": 0.0,
             "insufficient": 0.0}
CHOICES = {
    "met": "criterion is clearly satisfied by the evidence",
    "partially_met": "criterion is suggested but not fully confirmed",
    "not_met": "criterion is not satisfied (or contradicted)",
    "insufficient": "evidence is too weak to judge this criterion",
}


def grade_cluster(rows: list[dict], criteria: list[dict]) -> tuple[list[dict], float | None]:
    """One typed-choice JEV call per criterion. Returns (results, grade)."""
    evidence_text = "\n".join(
        f"- [{r.get('source', 'web')}] {r.get('signal', '')} ({r.get('url')})"
        for r in rows[:12]
    )
    results = []
    grades = []
    for crit in criteria:
        choice, conf = classify_retry(
            title=crit["id"],
            text=(
                f"Criterion: {crit['description']}\n\n"
                f"Evidence rows attributed to this candidate (not yet "
                f"verified as the seed person):\n"
                f"{evidence_text}\n\nDoes the evidence satisfy the criterion?"
            ),
            categories=CHOICES,
        )
        grade = GRADE_MAP.get(choice)
        results.append(
            {
                "id": crit["id"],
                "description": crit["description"],
                "choice": choice,
                "grade": grade,
                "confidence": round(conf, 3),
            }
        )
        if grade is not None:
            grades.append(grade)
        time.sleep(PACE)
    relevance = round(sum(grades) / len(grades), 3) if grades else None
    return results, relevance


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--name", default="")
    ap.add_argument("--phone", default="")
    ap.add_argument("--email", default="")
    ap.add_argument("--social", default="")
    ap.add_argument("--evidence-file", required=True)
    ap.add_argument("--top", type=int, default=5, help="verify top N clusters")
    ap.add_argument("--out", help="optional output file")
    args = ap.parse_args(argv)

    seed = {k: v for k, v in vars(args).items() if k in
            ("name", "phone", "email", "social") and v}
    if not seed.get("name"):
        print("error: --name is required", file=sys.stderr)
        return 2

    rows = load_evidence(Path(args.evidence_file))
    if engine_failed(rows):
        print(
            json.dumps({"verdict": "engine_error",
                        "reason": "all queries failed or no rows"}),
        )
        return 3

    clustered = cluster_rows(rows, seed_name=seed.get("name", ""))
    criteria = build_criteria(seed)
    candidates = []
    all_jev_rows = to_jev_rows(rows)
    for c in (clustered.get("clusters") or [])[: args.top]:
        c_rows = [r for r in all_jev_rows if r.get("url") in set(c["urls"])]
        crit_results, relevance = grade_cluster(c_rows, criteria)
        verdict, confidence = verify_identity_retry(
            seed.get("name", ""), seed.get("phone", ""), c_rows
        )
        candidates.append(
            {
                "key": c["key"],
                "display": c["display"],
                "size": c["size"],
                "domains": c["domains"],
                "relevance_grade": relevance,
                "criteria": crit_results,
                "verdict": verdict,
                "confidence": round(confidence, 3),
            }
        )
        time.sleep(PACE)

    order = {"match": 0, "insufficient": 1, "no_match": 2}
    candidates.sort(
        key=lambda c: (
            order.get(str(c["verdict"]), 3),
            -(c["relevance_grade"] or 0),
            -c["size"],
            c["key"],
        )
    )
    for i, c in enumerate(candidates, 1):
        c["rank"] = i

    result = {
        "seed": seed,
        "criteria_count": len(criteria),
        "unattributed_rows": clustered["unattributed"],
        "candidates": candidates,
    }
    text = json.dumps(result, indent=2, ensure_ascii=False)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
        print(f"wrote {args.out} ({len(candidates)} candidate(s))")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
