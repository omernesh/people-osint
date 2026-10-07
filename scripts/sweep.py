#!/usr/bin/env python3
"""Deterministic search runner for the people-osint pipeline (pure script, no LLM).

Input: a plan JSON from dorks.py (scripts/dorks.py plan mode) plus optional
extra queries. Output: evidence rows appended to a JSONL file, one per query,
with engine, URL, title and a snippet — nothing decides identity.

Engines: Exa via `mcporter call exa.web_search_exa` (primary, paced), and
Exa mirror calls for company/founder context. No shell ||-fallbacks: every
engine result is captured with its exit code; failures are recorded as
{"error": ...} rows, never silently swallowed.

Usage:
  python3 sweep.py plan.json --out evidence.jsonl [--pace 2.0] [--max 30]
  python3 sweep.py --query '"Ran Margalit"' --out evidence.jsonl
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path


def run(cmd, timeout=60):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip()
    except subprocess.TimeoutExpired:
        return 124, ""


def exa_search(query, num=5):
    """One Exa search via mcporter. Returns (ok, rows)."""
    code, out = run(["mcporter", "call", "exa.web_search_exa",
                     "query=" + query, f"numResults={num}"], timeout=90)
    if code != 0:
        return False, [{"error": f"mcporter exit {code}: {out[:200]}"}]
    if not out:
        return False, [{"error": "empty output from exa (mcporter silent)"}]
    rows, cur = [], {}
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("Title:"):
            cur = {"title": line.split(":", 1)[1].strip()}
        elif line.startswith("URL:"):
            cur["url"] = line.split(":", 1)[1].strip()
            rows.append(cur)
            cur = {}
    if not rows:
        return False, [{"error": "no Title/URL lines parsed", "raw": out[:400]}]
    return True, rows


def write_rows(path: Path, rows):
    with open(path, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("plan", nargs="?", help="plan JSON from dorks.py")
    ap.add_argument("--out", required=True, help="evidence JSONL (append)")
    ap.add_argument("--query", action="append", default=[],
                    help="extra raw query; repeatable")
    ap.add_argument("--pace", type=float, default=2.0,
                    help="seconds between engine calls (default 2.0)")
    ap.add_argument("--max", type=int, default=30,
                    help="max queries to run (default 30)")
    args = ap.parse_args()

    queries = list(args.query)
    if args.plan:
        plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
        queries += [q["query"] for q in plan.get("queries", [])]

    if not queries:
        print("no queries (plan empty + no --query)", file=sys.stderr)
        sys.exit(2)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    done = 0
    for q in queries[: args.max]:
        ok, rows = exa_search(q)
        write_rows(out, [{"query": q, "engine": "exa", **r} for r in rows])
        done += 1
        print(f"[{done}/{min(len(queries), args.max)}] "
              f"{'OK ' if ok else 'ERR'} {q[:70]}")
        time.sleep(args.pace)
    print(f"done: {done} queries -> {out}")


if __name__ == "__main__":
    main()
