#!/usr/bin/env python3
"""verify_role.py — deterministic current-role verifier (no LLM).

The stale-contact-card killer. A contact card's org/title is a LEAD, not a
fact. This module extracts structured title/employer signals from fetched
evidence text (LinkedIn pages, employer management pages, stock-profile
sites) and grades what they say about the claimed role:

  confirmed   — a source with a date >= RECENCY_YEARS shows org+title
  stale       — sources only show the claimed org in dated PAST entries
                (or the LinkedIn "current" badge predates RECENCY_YEARS)
  unknown     — no usable title evidence at all

Usage (agent-facing): feed it evidence rows (title/url/snippet/date-like
strings) + the claimed org; it returns the grade + the deciding source.
"""
import argparse
import json
import re
from datetime import date

RECENCY_YEARS = 3


def _year(text: str) -> int:
    """First 4-digit year 1960-2099 in text, else 0."""
    m = re.search(r"\b(19[6-9]\d|20[0-9]\d)\b", text or "")
    return int(m.group(1)) if m else 0


def extract_titles(text: str) -> list:
    """Structured (title, org, year, is_current_flag) tuples from page text.

    Handles the common LinkedIn/cache/stock-profile layouts:
      "Title at Org ... Apr 2017 - Present (9 years)"
      "### Dev group manager - cal (Current) Apr 2017 - Present"
      "Name | Title at Org | pay | -- | 1961"          (Yahoo-style rows)
    """
    out = []
    if not text:
        return out

    def _clean(s: str) -> str:
        return re.sub(r"[\s.]+$", "", s.strip())

    def _org_ok(org: str) -> bool:
        return bool(org) and len(re.sub(r"[^A-Za-z]", "", org)) >= 2

    org_at = re.compile(
        r"([A-Za-z][A-Za-z&.,'’\- ]{2,60}?)\s+at\s+([A-Za-z][A-Za-z&.'’\- ]{2,60})",
        re.UNICODE)
    for m in org_at.finditer(text):
        title, org = _clean(m.group(1)), _clean(m.group(2))
        if not _org_ok(org) or len(title) < 2:
            continue
        win = text[m.end():m.end() + 120]
        y = _year(win) or _year(text[max(0, m.start() - 60):m.start()])
        present = bool(re.search(r"\bPresent\b|\bCurrent\b|עד היום", win, re.IGNORECASE))
        out.append({"title": title, "org": org, "year": y, "current": present})
    # LinkedIn cache headers: "### <title> - <org> (Current) <dates>"
    for m in re.finditer(
            r"###\s*([A-Za-z][A-Za-z&.,'’\- ]{2,60}?)\s+-\s+([A-Za-z][A-Za-z&.'’\- ]{2,60})",
            text):
        title, org = _clean(m.group(1)), _clean(m.group(2))
        if not _org_ok(org) or len(title) < 2:
            continue
        win = text[m.end():m.end() + 120]
        y = _year(win)
        present = bool(re.search(r"\bPresent\b|\bCurrent\b", win, re.IGNORECASE))
        out.append({"title": title, "org": org, "year": y, "current": present})
    # stock-profile pipe rows: | Name | Deputy CEO, ... Org | ... |
    for m in re.finditer(
            r"\|\s*([A-Z][\w.' -]{2,40})\s*\|\s*([^|]{5,120}?(?:CEO|CIO|CTO|CFO|EVP|VP|"
            r"Manager|Director|Head|Chief|Officer|President|Chairman)[^|]{0,120})\s*\|",
            text):
        title = _clean(m.group(2))
        win = text[m.end():m.end() + 60]
        y = _year(win) or _year(text[max(0, m.start() - 80):m.start()])
        out.append({"title": title, "org": "", "org_from_title": True,
                    "year": y, "current": True})  # stock profiles list current mgmt
    return out


def grade(claimed_org: str, evidence: list) -> dict:
    """Grade the claimed org against extracted title evidence."""
    claimed = (claimed_org or "").lower().strip()
    if not claimed:
        return {"grade": "unknown", "reason": "no claimed org"}
    cutoff = date.today().year - RECENCY_YEARS
    confirms, stale_only, other_now = [], [], []
    for ev in evidence:
        text = " ".join(str(ev.get(k, "")) for k in ("title", "url", "snippet"))
        for t in extract_titles(text):
            org = (t.get("org") or "").lower()
            year = t.get("year") or 0
            if claimed in org or org in claimed:
                if t.get("current") and year and year >= cutoff:
                    confirms.append({"src": ev.get("url"), "title": t["title"],
                                     "org": t["org"], "year": year})
                else:
                    stale_only.append({"src": ev.get("url"), "title": t["title"],
                                       "org": t["org"], "year": year})
            elif t.get("current") and year and year >= cutoff:
                other_now.append({"src": ev.get("url"), "title": t["title"],
                                  "org": t["org"], "year": year})
    if confirms:
        return {"grade": "confirmed", "sources": confirms[:3],
                "reason": f"current role at {claimed_org} within {RECENCY_YEARS}y"}
    if other_now:
        return {"grade": "stale", "sources": other_now[:3],
                "reason": "recent evidence shows a DIFFERENT current org — "
                          "claimed card org is historical"}
    if stale_only:
        return {"grade": "stale", "sources": stale_only[:3],
                "reason": "only dated/past entries at the claimed org"}
    return {"grade": "unknown", "reason": "no title evidence for claimed org"}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--claimed-org", required=True)
    ap.add_argument("--evidence-file", help="JSONL rows with title/url/snippet")
    ap.add_argument("--text", help="raw text to grade directly")
    a = ap.parse_args()
    rows = []
    if a.evidence_file:
        rows = [json.loads(l) for l in open(a.evidence_file, encoding="utf-8")
                if l.strip()]
    elif a.text:
        rows = [{"title": "", "url": "inline", "snippet": a.text}]
    print(json.dumps(grade(a.claimed_org, rows), ensure_ascii=False, indent=1))
