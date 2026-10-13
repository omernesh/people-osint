#!/usr/bin/env python3
"""Bench harness: run people-search-bench queries through the sweep pipeline.

Queries come from LessieAI/people-search-bench (MIT License,
https://github.com/LessieAI/people-search-bench): 4 JSONL files in
data/queries/ with rows {query_id, prompt, category, source_id}. This
harness downloads them once into eval/data/ (cache), selects a deterministic
subset (sorted by query_id), runs every prompt through scripts/sweep.py —
the exact Exa/mcporter path used by investigations, never a reimplementation
— and turns the collected evidence rows into a bench submission CSV with
columns query_id,agent_name,person_data (<= 15 rows per query, no
fabrication: every row traces to an ok evidence row with a URL).

One failing query never kills the run: sweep.py already records failures as
status=engine_error rows, and this harness zero-pads them (they contribute
no submission rows but stay in evidence and the summary). Scoring of the
produced CSV happens on the bench side; eval/metrics.py reimplements that
math for local estimates.

Usage:
  python3 eval/run_bench.py                          # all 119 queries
  python3 eval/run_bench.py --category recruiting --limit 5
  python3 eval/run_bench.py --queries rec_0001,b2b_0003
  python3 eval/run_bench.py --export-submission /tmp/submission.csv

Output: eval/runs/<timestamp>/{evidence.jsonl,submission.csv} plus a printed
summary. Exit codes: 0 ok, 2 no queries selected, 3 every query failed
(mirroring sweep.py).
"""
import argparse
import csv
import json
import re
import subprocess
import sys
import time
import urllib.request
from collections import OrderedDict
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = EVAL_DIR.parent / "scripts"

BENCH_BASE_URL = ("https://raw.githubusercontent.com/LessieAI/"
                  "people-search-bench/HEAD/data/queries")
BENCH_CATEGORIES = ("b2b", "deterministic", "influencer", "recruiting")

AGENT_NAME = "people-osint"
SUBMISSION_COLUMNS = ("query_id", "agent_name", "person_data")
MAX_RESULTS_PER_QUERY = 15   # bench rule: up to 15 results per query
SNIPPET_MAX_CHARS = 200      # person_data snippet budget
MIN_PACE = 1.5               # seconds between engine calls (bench etiquette)

EXIT_NO_QUERIES = 2
EXIT_ENGINE_ERROR = 3


# ---------------------------------------------------------------- queries

def fetch_bench_file(name, data_dir):
    """Return a local path to <name>.jsonl, downloading it once if needed.

    Cached files win: an existing non-empty eval/data/<name>.jsonl is used
    as-is, so repeat runs (and graded re-runs) are offline-friendly.
    """
    path = Path(data_dir) / f"{name}.jsonl"
    if path.exists() and path.stat().st_size > 0:
        return path
    url = f"{BENCH_BASE_URL}/{name}.jsonl"
    Path(data_dir).mkdir(parents=True, exist_ok=True)
    print(f"downloading {url}")
    with urllib.request.urlopen(url, timeout=60) as resp:
        body = resp.read()
    path.write_bytes(body)
    return path


def load_queries(path):
    """Parse one bench JSONL file into a list of query dicts."""
    queries = []
    for n, line in enumerate(Path(path).read_text(encoding="utf-8")
                             .splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            q = json.loads(line)
        except ValueError as e:
            raise ValueError(f"{path}:{n}: bad JSON: {e}") from None
        for key in ("query_id", "prompt", "category"):
            if not q.get(key):
                raise ValueError(f"{path}:{n}: missing '{key}'")
        queries.append(q)
    return queries


def load_bench_queries(data_dir, categories=BENCH_CATEGORIES):
    """Load (and if needed download) the bench files. Failures skip the
    category with a warning on stderr; an empty result means no data."""
    out = []
    for name in categories:
        try:
            path = fetch_bench_file(name, data_dir)
            out += load_queries(path)
        except Exception as e:  # network down / bad cache: keep going
            print(f"warning: cannot load '{name}': {e}", file=sys.stderr)
    return out


def select_queries(queries, category=None, limit=None, ids=None):
    """Deterministic subset: filter, sort by query_id, then cap.

    `ids` is a collection of query_ids; unknown ids raise ValueError so a
    typo fails loudly instead of silently benching nothing.
    """
    if category:
        queries = [q for q in queries if q["category"] == category]
    if ids is not None:
        wanted = set(ids)
        have = {q["query_id"] for q in queries}
        unknown = sorted(wanted - have)
        if unknown:
            raise ValueError(f"unknown query_id(s): {', '.join(unknown)}")
        queries = [q for q in queries if q["query_id"] in wanted]
    queries = sorted(queries, key=lambda q: q["query_id"])
    if limit is not None:
        queries = queries[:limit]
    return queries


# ---------------------------------------------------------------- sweep

def build_sweep_cmd(scripts_dir, prompts, out_path, pace):
    """The one place the engine is invoked: scripts/sweep.py with one
    repeatable --query per prompt. --max must cover every prompt or sweep
    would silently stop at its default 30."""
    cmd = [sys.executable, str(Path(scripts_dir) / "sweep.py"),
           "--out", str(out_path), "--pace", str(pace),
           "--max", str(len(prompts))]
    for p in prompts:
        cmd += ["--query", p]
    return cmd


def run_sweep(scripts_dir, prompts, out_path, pace):
    """Run scripts/sweep.py over all prompts. Returns its exit code; a
    non-zero code still leaves whatever rows it managed on disk."""
    cmd = build_sweep_cmd(scripts_dir, prompts, out_path, pace)
    return subprocess.run(cmd).returncode


def load_evidence_rows(path):
    """Read the evidence JSONL; malformed lines fail with line numbers."""
    rows = []
    p = Path(path)
    if not p.exists():
        return rows
    for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError as e:
            raise ValueError(f"{path}:{n}: bad JSON: {e}") from None
    return rows


def map_rows_to_queries(rows, queries):
    """Group evidence rows by query_id via the row's `query` field.

    Every selected query_id whose prompt ran shares that prompt's rows
    (prompts are deduplicated before the sweep, so one prompt = one batch).
    Rows whose prompt was not selected are dropped. A query whose prompt
    produced nothing (engine_error-only or absent) stays empty ->
    zero-padded."""
    out = OrderedDict((q["query_id"], []) for q in queries)
    by_prompt = {}
    for q in queries:
        by_prompt.setdefault(q["prompt"], []).append(q["query_id"])
    batches = OrderedDict()
    for row in rows:
        p = row.get("query")
        if p in by_prompt:
            batches.setdefault(p, []).append(row)
    for p, batch in batches.items():
        for qid in by_prompt[p]:
            out[qid].extend(batch)
    return out


# ---------------------------------------------------------------- submission

def person_name_from_title(title):
    """Best-effort person name: the title before the first dash/pipe/comma,
    if that head has 2+ space-separated tokens ('Jane Doe - LinkedIn' ->
    'Jane Doe'). Returns None when the title is not name-ish."""
    head = re.split(r"[-\u2013\u2014|,]", title.strip(), maxsplit=1)[0]
    tokens = head.split()
    return " ".join(tokens) if len(tokens) >= 2 else None


def build_person_data(row):
    """person_data cell: name-ish lead (or the raw title) + ' | ' + URL,
    plus the first 200 chars of snippet when present."""
    title = (row.get("title") or "").strip()
    name = person_name_from_title(title)
    lead = name if name else title
    parts = [p for p in (lead, (row.get("url") or "").strip()) if p]
    snippet = (row.get("snippet") or "").strip()[:SNIPPET_MAX_CHARS]
    if snippet:
        parts.append(snippet)
    return " | ".join(parts)


def to_submission_rows(rows_by_qid, max_results=MAX_RESULTS_PER_QUERY):
    """ok evidence rows with a URL -> submission rows, sorted by query_id,
    capped at max_results per query. Failures fabricate nothing."""
    out = []
    for qid in sorted(rows_by_qid):
        ok = [r for r in rows_by_qid[qid]
              if r.get("status") == "ok" and r.get("url")]
        for r in ok[:max_results]:
            out.append({"query_id": qid, "agent_name": AGENT_NAME,
                        "person_data": build_person_data(r)})
    return out


def write_submission_csv(path, result_rows):
    """Write the bench CSV: exact header query_id,agent_name,person_data."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(SUBMISSION_COLUMNS)
        for r in result_rows:
            w.writerow([r["query_id"], r["agent_name"], r["person_data"]])
    return path


# ---------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Run people-search-bench queries through sweep.py")
    ap.add_argument("--category", choices=BENCH_CATEGORIES,
                    help="only one bench category")
    ap.add_argument("--limit", type=int,
                    help="run only the first N queries (after sorting)")
    ap.add_argument("--queries",
                    help="comma-separated query_ids to run")
    ap.add_argument("--pace", type=float, default=2.0,
                    help=f"seconds between engine calls "
                         f"(default 2.0, minimum {MIN_PACE})")
    ap.add_argument("--export-submission",
                    help="also write the submission CSV to this path")
    ap.add_argument("--data-dir", type=Path, default=EVAL_DIR / "data",
                    help="bench query cache dir (default: eval/data)")
    ap.add_argument("--runs-dir", type=Path, default=EVAL_DIR / "runs",
                    help="run output root (default: eval/runs)")
    args = ap.parse_args(argv)

    ids = [s.strip() for s in args.queries.split(",") if s.strip()] \
        if args.queries else None
    try:
        queries = select_queries(load_bench_queries(args.data_dir),
                                 category=args.category,
                                 limit=args.limit, ids=ids)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_NO_QUERIES
    if not queries:
        print("no queries selected (bad --category/--limit/--queries "
              "or no bench data)", file=sys.stderr)
        return EXIT_NO_QUERIES

    pace = max(args.pace, MIN_PACE)
    stamp = time.strftime("%Y%m%dT%H%M%S")
    run_dir = Path(args.runs_dir) / stamp
    run_dir.mkdir(parents=True, exist_ok=True)
    evidence_path = run_dir / "evidence.jsonl"
    submission_path = run_dir / "submission.csv"

    code = run_sweep(SCRIPTS_DIR,
                     list(dict.fromkeys(q["prompt"] for q in queries)),
                     evidence_path, pace)
    if code not in (0, EXIT_ENGINE_ERROR):
        # sweep crashed before/without writing (missing binary, bad args):
        # keep going with whatever evidence exists, never crash the run.
        print(f"warning: sweep.py exited {code}; using evidence on disk",
              file=sys.stderr)

    try:
        rows = load_evidence_rows(evidence_path)
    except ValueError as e:
        print(f"warning: {e}; treating rest as empty", file=sys.stderr)
        rows = []

    by_qid = map_rows_to_queries(rows, queries)
    result_rows = to_submission_rows(by_qid)
    write_submission_csv(submission_path, result_rows)
    if args.export_submission:
        write_submission_csv(args.export_submission, result_rows)

    engine_errors = sum(1 for r in rows if r.get("status") == "engine_error")
    failed_queries = sum(
        1 for qid in by_qid
        if not any(r.get("status") == "ok" and r.get("url")
                   for r in by_qid[qid]))
    print(f"bench run {stamp}: {len(queries)} queries, "
          f"{len(result_rows)} result rows, "
          f"{engine_errors} engine_error rows, "
          f"{failed_queries} queries with no results")
    print(f"evidence: {evidence_path}")
    print(f"submission: {submission_path}")
    if args.export_submission:
        print(f"submission (export): {args.export_submission}")

    if failed_queries == len(queries):
        # an outage must not look like "no evidence" (same rule as sweep.py)
        return EXIT_ENGINE_ERROR
    return 0


if __name__ == "__main__":
    sys.exit(main())
