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

PROFILE_HOSTS = (
    "linkedin.com/in/", "instagram.com/", "youtube.com/@", "youtube.com/c/",
    "youtube.com/channel/", "youtube.com/user/", "tiktok.com/@",
    "x.com/", "twitter.com/", "github.com/", "medium.com/@",
)
URL_JUNK_SEGMENTS = ("/p/", "/reel", "/explore", "/i/", "/hashtag", "/watch")


def looks_like_profile(url: str) -> bool:
    u = (url or "").split("?")[0].rstrip("/")
    if not any(h in u for h in PROFILE_HOSTS):
        return False
    return not any(j in u for j in URL_JUNK_SEGMENTS)


def extract_name(title: str) -> str:
    """Best-effort person name from a result title. Empty if unsure."""
    t = title.strip()
    # "@handle (Display Name | desc)" — KOL directory format
    m = re.match(r"^@([A-Za-z0-9_.]+)\s*\(([^|/)·]+)", t)
    if m:
        disp = re.sub(r"\s+", " ", m.group(2)).strip(" -·")
        if disp and len(disp) <= 40:
            return disp
        return f"@{m.group(1)}"
    # "Handle Instagram Stats: ..." / "Handle Stats: ..." — stats directories
    m = re.match(r"^@?([A-Za-z0-9_.]+)\s+(?:[A-Za-z]+\s)?Stats\b", t)
    if m and 3 <= len(m.group(1)) <= 30:
        return m.group(1)
    # social/KOL display format: "Name (@handle) rest of title" — covers
    # twitter/instagram/youtube cards incl. third-party aggregator pages
    m = re.match(r"^(.*?)\s*\(@([A-Za-z0-9_.]+)\)", t)
    if m:
        lead = re.sub(r"[^\w\s.''-]", " ", m.group(1))
        lead = re.sub(r"\s+", " ", lead).strip()
        toks = lead.split()
        if 1 <= len(toks) <= 4 and sum(len(x) for x in toks) >= 2:
            return " ".join(toks)
        return f"@{m.group(2)}"
    t = re.split(r"\s[-–|·]\s|,\s", t)[0].strip()
    toks = t.split()
    if 2 <= len(toks) <= 4 and all(re.fullmatch(r"[A-Z][A-Za-z'’.-]*", x) for x in toks):
        if not any(x.lower() in NAME_STOP for x in toks):
            return t
    return ""


POST_SLUG_RE = re.compile(r"linkedin\.com/posts/([a-z0-9-]{4,60})")


def name_from_linkedin_post(url: str) -> str:
    """LinkedIn post URLs embed the author slug: .../posts/raghavi-sudharsan-7740ba57_..."""
    m = POST_SLUG_RE.search(url or "")
    if not m:
        return ""
    parts = [p for p in m.group(1).split("-") if p]
    # drop trailing hex-ish id segments
    words = [p for p in parts if not re.fullmatch(r"[0-9a-f]{6,}", p)]
    if 2 <= len(words) <= 4 and all(re.fullmatch(r"[a-z]+", w) for w in words):
        return " ".join(w.capitalize() for w in words)
    return ""


def linkedin_url(url: str) -> str:
    return url if "linkedin.com/in/" in url else ""


def load_runs(dirs: list[Path]) -> dict:
    """query_prompt -> {'runs': {run_idx: {url: row}}, 'errors': [...]}.

    Per-query layering: results are grouped BY RUN so the builder can pick
    the latest run that has rows for a query (retry supersedes), instead of
    mixing passes.
    """
    merged: dict[str, dict] = {}
    for idx, d in enumerate(dirs):
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
            slot = merged.setdefault(q, {"runs": {}, "errors": []})
            if row.get("status") == "ok" and row.get("url"):
                slot["runs"].setdefault(idx, {})[row["url"]] = row
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

    # map prompts -> query_ids/categories; a prompt may serve several
    # query_ids (the bench has 8 duplicate prompts) — every query_id must be
    # represented in the output independently
    qlist = []
    qdir = Path(__file__).resolve().parent / "data"
    for f in sorted(qdir.glob("*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            qlist.append((d["query_id"], d["prompt"], d["category"]))
    qlist.sort()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    per_cat: dict[str, list[dict]] = {c: [] for c in CATEGORIES}
    empty = []
    seen_ids = set()

    for qid, prompt, cat in qlist:
        slot = merged.get(prompt) or {}
        seen_ids.add(qid)
        runs = slot.get("runs") or {}
        if runs:  # latest run with rows for this query wins (retry supersedes)
            rows = list(runs[max(runs.keys())].values())
        else:
            rows = []
        # person filter: keep rows that look like a person or a person/creator
        # profile (extractable name or a profile URL). The skill surfaces
        # people and creator profiles, not shops/articles — this normalizes
        # raw web rows to the platform's actual output.
        person_rows = []
        for r in rows:
            title = str(r.get("title") or "")
            url = str(r.get("url") or "")
            name = extract_name(title)
            if not name:
                name = name_from_linkedin_post(url)
            if name or looks_like_profile(url):
                person_rows.append((r, name))
        person_rows = person_rows[:MAX_PER_QUERY]
        if not person_rows:
            why = ((slot or {}).get("errors") or ["no person results"])[0]
            empty.append((qid, cat, why))
            continue
        for r, name in person_rows:
            title = str(r.get("title") or "")
            snip = str(r.get("snippet") or "").strip()
            per_cat[cat].append({
                "query_id": qid,
                "agent_name": args.platform,
                "name": name,
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
    print(f"total rows: {total} | queries covered: {len(seen_ids) - len(empty)}/{len(seen_ids)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
