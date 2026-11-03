#!/usr/bin/env python3
"""Deterministic search runner for the people-osint pipeline (pure script, no LLM).

Input: a plan JSON from dorks.py (scripts/dorks.py plan mode) plus optional
extra queries. Output: evidence rows appended to a JSONL file, one per query,
with engine, URL, title and a snippet — nothing decides identity.

Engines: Exa via `mcporter call exa.web_search_exa` (primary, paced), and
Exa mirror calls for company/founder context. No shell ||-fallbacks: every
engine result is captured with its exit code; failures are recorded as
{"error": ...} rows, never silently swallowed.

Exit codes: 0 ok (individual failures are marked status=engine_error per row),
2 no queries, 3 every query failed or Exa credits exhausted (sweep stops there).

Usage:
  python3 sweep.py plan.json --out evidence.jsonl [--pace 2.0] [--max 30]
  python3 sweep.py --query '"Jane Q. Public"' --out evidence.jsonl
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path


EXIT_ENGINE_ERROR = 3  # every query failed, or Exa credits ran out mid-sweep

# mcporter exits 0 and prints this on stdout when the Exa tool call fails.
EXA_ERROR_RE = re.compile(r"web_search_exa error \((\d{3})\)")
CREDITS_ERROR = "credits exhausted"


def exa_error_line(out):
    """First stdout line carrying an `web_search_exa error (<code>)` marker,
    as a (line, code) pair, or None."""
    for line in out.splitlines():
        m = EXA_ERROR_RE.search(line)
        if m:
            return line.strip(), m.group(1)
    return None


def is_credits_exhausted(rows):
    return any(str(r.get("error", "")).startswith(CREDITS_ERROR) for r in rows)


def mcporter_argv():
    """How to invoke mcporter. On Windows npm installs a .cmd, which subprocess
    can't run by bare name; running it directly goes through cmd.exe, where a
    query containing & runs as a command. So call its node entry point."""
    exe = shutil.which("mcporter")
    if os.name == "nt" and exe and exe.lower().endswith((".cmd", ".bat")):
        cli = os.path.join(os.path.dirname(exe), "node_modules", "mcporter", "dist", "cli.js")
        node = shutil.which("node")
        if node and os.path.isfile(cli):
            return [node, cli]
        return ["mcporter"]
    return [exe or "mcporter"]


def run(cmd, timeout=60):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip()
    except subprocess.TimeoutExpired:
        return 124, ""
    except OSError as e:  # binary missing / not executable
        return 127, str(e)


def exa_search(query, num=5):
    """One Exa search via mcporter. Returns (ok, rows)."""
    code, out = run([*mcporter_argv(), "call", "exa.web_search_exa",
                     "query=" + query, f"numResults={num}"], timeout=90)
    if code != 0:
        return False, [{"error": f"mcporter exit {code}: {out[:200]}"}]
    if not out:
        return False, [{"error": "empty output from exa (mcporter silent)"}]
    err = exa_error_line(out)
    if err:
        line, ecode = err
        if ecode == "402" or "exceeded your credits" in line:
            return False, [{"error": f"{CREDITS_ERROR}: {line[:200]}"}]
        return False, [{"error": f"exa error {ecode}: {line[:200]}"}]
    rows = parse_exa_text(out)
    if not rows:
        return False, [{"error": "no Title/URL lines parsed", "raw": out[:400]}]
    return True, rows


SNIPPET_KEYS = ("Text:", "Highlights:", "Snippet:", "Summary:")
SNIPPET_MAX = 400


def parse_exa_text(out):
    """Parse mcporter's text output into rows {title, url, snippet?}.

    A result starts at `Title:`, gets its URL from `URL:`, and any
    Text/Highlights/Snippet/Summary lines that follow (up to the next
    `Title:`) are kept as a bounded `snippet` for provenance.
    """
    rows, cur, snip = [], None, []

    def flush():
        if cur is not None and cur.get("url"):
            text = " ".join(snip).strip()
            if text:
                cur["snippet"] = text[:SNIPPET_MAX]
            rows.append(cur)

    for raw in out.splitlines():
        line = raw.strip()
        if line.startswith("Title:"):
            flush()
            cur, snip = {"title": line.split(":", 1)[1].strip()}, []
        elif line.startswith("URL:") and cur is not None:
            cur["url"] = line.split(":", 1)[1].strip()
        elif cur is not None and cur.get("url") and line:
            for k in SNIPPET_KEYS:
                if line.startswith(k):
                    line = line[len(k):].strip()
                    break
            snip.append(line)
    flush()
    return rows


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
    done = failed = 0
    credits_out = False
    batch = queries[: args.max]
    for i, q in enumerate(batch):
        ok, rows = exa_search(q)
        status = "ok" if ok else "engine_error"
        write_rows(out, [{"query": q, "engine": "exa", "status": status, **r}
                         for r in rows])
        done += 1
        failed += 0 if ok else 1
        print(f"[{done}/{len(batch)}] "
              f"{'OK ' if ok else 'ERR'} {q[:70]}")
        if is_credits_exhausted(rows):
            # Every remaining query would fail the same way: stop, don't burn them.
            credits_out = True
            print(f"stopped: Exa credits exhausted; skipped "
                  f"{len(batch) - done} remaining queries", file=sys.stderr)
            break
        if i < len(batch) - 1:
            time.sleep(args.pace)
    print(f"done: {done} queries ({failed} failed) -> {out}")
    if credits_out:
        # Partial evidence after an outage must not look like "no evidence".
        print("engine_error: credits exhausted (Exa); sweep incomplete",
              file=sys.stderr)
        sys.exit(EXIT_ENGINE_ERROR)
    if done and failed == done:
        # Every query failed: an outage must not look like "no evidence".
        print("engine_error: all queries failed", file=sys.stderr)
        sys.exit(EXIT_ENGINE_ERROR)


if __name__ == "__main__":
    main()
