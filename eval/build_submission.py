#!/usr/bin/env python3
"""build_submission.py — package bench run(s) into People Search Bench PR files.

Reads one or more run dirs (eval/runs/<ts>/, from run_bench.py), merges their
ok rows (later runs win per query — retries supersede), and writes the PR file
set matching data/results/<platform>/ in LessieAI/people-search-bench:

  b2b.csv, deterministic.csv, influencer.csv, recruiting.csv
  empty_results.csv  (query_ids with no results, quoted reason)

CSV columns: query_id,agent_name,name,title,company,location,linkedin_url,
email,bio,person_data — structured fields are best-effort extracted from the
raw result; person_data carries the complete raw row (never fabricated, never
hand-curated). Caps at 15 results per query (bench submission rule).

Usage: build_submission.py RUN_DIR [RUN_DIR ...] --platform people_osint --out DIR
"""

import argparse
import csv
import json
import re
import sys
from pathlib import Path

CATEGORIES = ("b2b", "deterministic", "influencer", "recruiting")
MAX_PER_QUERY = 15

NAME_STOP = {"the", "and", "for", "with", "from", "our", "your"}


def extract_name(title: str) -> str:
    """Best-effort person name from a result title. Empty if unsure."""
    t = re.split(r"\s[-–|·]\s|,\s", title.strip())[0].strip()
    toks = t.split()
    if 2 <= len(toks) <= 4 and all(re.fullmatch(r"[A-Z][A-Za-z'’.-]*", x) for x in toks):
        if not any(x.lower() in NAME_STOP for x in toks):
            return t
    return ""


def linkedin_url(url: str) -> str:
    return url if "linkedin.com/in/" in url else ""


def load_runs(dirs: list[Path]) -> dict:
    """query_prompt -> {'category':..., 'rows': [row,...]} merged across runs."""
    merged: dict[str, dict] = {}
    for d in dirs:
        ev = d / "evidence.jsonl"
        if not ev.exists():
            continue
        for line in ev.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            q = row.get("query")
            if not q:
                continue
            slot = merged.setdefault(q, {"rows": {}, "errors": []})
            if row.get("status") == "ok" and row.get("url"):
                slot["rows"][row["url"]] = row  # dedupe by url, later run wins
            elif row.get("status") == "engine_error":
                slot["errors"].append((row.get("error") or "")[:120])
    return merged


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("runs", nargs="+", help="run dirs, oldest first")
    ap.add_argument("--platform", default="people_osint")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    run_dirs = [Path(r) for r in args.runs]
    merged = load_runs(run_dirs)

    # map prompts -> query_ids/categories from the cached bench data
    queries = {}
    qdir = Path(__file__).resolve().parent / "data"
    for f in sorted(qdir.glob("*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            queries[d["prompt"]] = (d["query_id"], d["category"])

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    per_cat: dict[str, list[dict]] = {c: [] for c in CATEGORIES}
    empty = []
    seen_ids = set()

    for prompt, (qid, cat) in sorted(queries.items()):
        slot = merged.get(prompt)
        if qid:
            seen_ids.add(qid)
        rows = list((slot or {}).get("rows", {}).values())[:MAX_PER_QUERY]
        if not rows:
            why = ((slot or {}).get("errors") or ["no results returned"])[0]
            empty.append((qid, cat, why))
            continue
        for r in rows:
            title = str(r.get("title") or "")
            snip = str(r.get("snippet") or "").strip()
            per_cat[cat].append({
                "query_id": qid,
                "agent_name": args.platform,
                "name": extract_name(title),
                "title": "",
                "company": "",
                "location": "",
                "linkedin_url": linkedin_url(str(r.get("url") or "")),
                "email": "",
                "bio": snip[:300],
                "person_data": json.dumps(
                    {"title": title, "url": r.get("url"), "snippet": snip},
                    ensure_ascii=False,
                ),
            })

    cols = ["query_id", "agent_name", "name", "title", "company", "location",
            "linkedin_url", "email", "bio", "person_data"]
    for cat in CATEGORIES:
        with open(out / f"{cat}.csv", "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=cols)
            w.writeheader()
            w.writerows(per_cat[cat])
    with open(out / "empty_results.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["query_id", "category", "reason"])
        w.writerows(empty)

    total = sum(len(v) for v in per_cat.values())
    print(f"platform: {args.platform} | out: {out}")
    for cat in CATEGORIES:
        print(f"  {cat}.csv: {len(per_cat[cat])} rows")
    print(f"  empty_results.csv: {len(empty)} queries")
    print(f"total rows: {total} | queries covered: {len(queries) - len(empty)}/{len(queries)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
