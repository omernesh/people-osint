#!/usr/bin/env python3
"""Deterministic search runner for the people-osint pipeline (pure script, no LLM).

Input: a plan JSON from dorks.py (scripts/dorks.py plan mode) plus optional
extra queries. Output: evidence rows appended to a JSONL file, one per query,
with engine, URL, title and a snippet — nothing decides identity.

Engines, tried in order per query: Exa via `mcporter call exa.web_search_exa`,
then Tavily (POST api.tavily.com/search, key TAVILY_API_KEY), then Firecrawl
(POST api.firecrawl.dev/v2/search, key FIRECRAWL_API_KEY). Keys come from the
process env and travel only in an Authorization header: never argv, URL, log
or evidence. An engine that returns a credit/auth/unavailable error is
skipped for the rest of the sweep; any other failure just moves that query on
to the next engine. Each row's `engine` names the engine that answered. No
shell ||-fallbacks: failures are recorded as {"error": ...} rows.

Exit codes: 0 ok (individual failures are marked status=engine_error per row),
2 no queries, 3 every query failed or every engine became unavailable (credits
exhausted / auth / no key) so the sweep stopped incomplete.

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
import urllib.error
import urllib.request
from pathlib import Path


EXIT_ENGINE_ERROR = 3  # every query failed, or every engine became unavailable

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


AUTH_ERROR = "auth error"
UNAVAILABLE = "unavailable"
# An error starting with one of these repeats on every query: memoize it.
STICKY_PREFIXES = (CREDITS_ERROR, AUTH_ERROR, UNAVAILABLE)
KEY_ENV = {"tavily": "TAVILY_API_KEY", "firecrawl": "FIRECRAWL_API_KEY"}
TAVILY_URL = "https://api.tavily.com/search"
FIRECRAWL_URL = "https://api.firecrawl.dev/v2/search"
HTTP_TIMEOUT = 30
ENGINE_NAMES = ("exa", "tavily", "firecrawl")


def is_sticky(rows):
    return any(str(r.get("error", "")).startswith(STICKY_PREFIXES) for r in rows)


def http_post_json(url, key, body, timeout=HTTP_TIMEOUT):
    """POST JSON with the key in an Authorization header. Returns
    (status, text) for any HTTP answer, or (None, reason) when the transport
    failed. The reason is the exception type only: exception text can echo
    request details, so it is never kept."""
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Authorization": "Bearer " + key,
                 "Content-Type": "application/json",
                 "Accept": "application/json",
                 "User-Agent": "people-osint-sweep/1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""  # error body deliberately not read or kept
    except Exception as e:  # URLError, timeout, ssl, reset ...
        return None, type(e).__name__


def http_error(engine, status, credit_codes):
    """Map a non-200 status to a row error. 401/403 and the engine's credit
    codes are sticky (they repeat for every query)."""
    if status in credit_codes:
        return {"error": f"{CREDITS_ERROR}: {engine} http {status}"}
    if status in (401, 403):
        return {"error": f"{AUTH_ERROR}: {engine} http {status}"}
    if status == 429:
        return {"error": f"{engine} rate limited (http 429)"}
    return {"error": f"{engine} http {status}"}


def _api_search(engine, url, body, credit_codes, extract):
    key = os.environ.get(KEY_ENV[engine], "").strip()
    if not key:
        return False, [{"error": f"{UNAVAILABLE}: {KEY_ENV[engine]} not set"}]
    status, text = http_post_json(url, key, body)
    if status is None:
        return False, [{"error": f"{engine} request failed: {text}"}]
    if status != 200:
        return False, [http_error(engine, status, credit_codes)]
    try:
        items = extract(json.loads(text))
    except (ValueError, KeyError, AttributeError, TypeError):
        return False, [{"error": f"{engine} unparsable response"}]
    rows = []
    for it in items or []:
        if not isinstance(it, dict) or not it.get("url"):
            continue
        row = {"title": str(it.get("title") or "").strip(),
               "url": str(it["url"]).strip()}
        snip = str(it.get("content") or it.get("description") or "").strip()
        if snip:
            row["snippet"] = snip[:SNIPPET_MAX]
        rows.append(row)
    return True, rows  # genuinely empty is a result, not a failure


def tavily_search(query, num=5):
    return _api_search("tavily", TAVILY_URL,
                       {"query": query, "max_results": num,
                        "search_depth": "basic"},
                       (432, 433), lambda d: d["results"])


def _firecrawl_items(d):
    data = d["data"]
    return data["web"] if isinstance(data, dict) else data


def firecrawl_search(query, num=5):
    return _api_search("firecrawl", FIRECRAWL_URL,
                       {"query": query[:500], "limit": num},
                       (402,), _firecrawl_items)


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
    if code == 127:
        return False, [{"error": f"{UNAVAILABLE}: mcporter missing: {out[:120]}"}]
    if code != 0:
        return False, [{"error": f"mcporter exit {code}: {out[:200]}"}]
    if not out:
        return False, [{"error": "empty output from exa (mcporter silent)"}]
    err = exa_error_line(out)
    if err:
        line, ecode = err
        if ecode == "402" or "exceeded your credits" in line:
            return False, [{"error": f"{CREDITS_ERROR}: {line[:200]}"}]
        if ecode in ("401", "403"):
            return False, [{"error": f"{AUTH_ERROR}: {line[:200]}"}]
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


def engines():
    """Engine order; resolved at call time."""
    return [("exa", exa_search), ("tavily", tavily_search),
            ("firecrawl", firecrawl_search)]


def initial_dead():
    """Engines unusable before the first query (no key): name -> reason."""
    dead = {}
    for name, env in KEY_ENV.items():
        if not os.environ.get(env, "").strip():
            dead[name] = f"{UNAVAILABLE}: {env} not set"
            print(f"note: {name} skipped, {env} not set", file=sys.stderr)
    return dead


def search_query(query, dead, pace=0.0):
    """Try each live engine in order; sticky failures are memoized in `dead`.
    Returns (engine, ok, rows). When every engine failed, engine is "all" and
    rows is one error row naming each engine's reason."""
    reasons, tried = [], False
    for name, fn in engines():
        if name in dead:
            reasons.append(f"{name}: skipped ({dead[name].split(':')[0]})")
            continue
        if tried and pace > 0:
            time.sleep(pace)
        tried = True
        ok, rows = fn(query)
        if ok:
            return name, True, rows
        err = str(rows[0].get("error", "")) if rows else "failed"
        if is_sticky(rows):
            dead[name] = err
        reasons.append(f"{name}: {err[:100]}")
    return "all", False, [{"error": "; ".join(reasons)}]


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
    dead = initial_dead()
    batch = queries[: args.max]
    for i, q in enumerate(batch):
        if len(dead) == len(ENGINE_NAMES):
            break  # every remaining query would fail the same way
        engine, ok, rows = search_query(q, dead, args.pace)
        status = "ok" if ok else "engine_error"
        if ok and not rows:
            rows = [{"note": "no results"}]
        write_rows(out, [{"query": q, "engine": engine, "status": status, **r}
                         for r in rows])
        done += 1
        failed += 0 if ok else 1
        print(f"[{done}/{len(batch)}] "
              f"{'OK ' if ok else 'ERR'} {engine} {q[:70]}")
        if i < len(batch) - 1:
            time.sleep(args.pace)
    print(f"done: {done} queries ({failed} failed) -> {out}")
    if len(dead) == len(ENGINE_NAMES):
        # Partial evidence after an outage must not look like "no evidence".
        why = "; ".join(f"{n}: {r[:100]}" for n, r in dead.items())
        print(f"stopped: all engines unavailable; skipped "
              f"{len(batch) - done} remaining queries", file=sys.stderr)
        print(f"engine_error: all engines unavailable ({why}); sweep incomplete",
              file=sys.stderr)
        sys.exit(EXIT_ENGINE_ERROR)
    if done and failed == done:
        # Every query failed: an outage must not look like "no evidence".
        print("engine_error: all queries failed", file=sys.stderr)
        sys.exit(EXIT_ENGINE_ERROR)


if __name__ == "__main__":
    main()
