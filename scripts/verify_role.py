#!/usr/bin/env python3
"""verify_role.py — deterministic current-role verifier (no LLM).

The stale-contact-card killer. A contact card's org/title is a LEAD, not a
fact. This module extracts structured title/employer signals from fetched
evidence text (LinkedIn pages, employer management pages, stock-profile
sites) and grades what they say about the claimed role:

  confirmed   — the claimed org carries an explicit Present/Current marker
                in evidence that is itself FRESH (dated within
                RECENCY_YEARS). The job's START year does not matter: a
                role held since 2010 and shown as "Present" on a fresh page
                is current.
  stale       — the claimed org appears only in dated PAST entries, its
                "Present" marker sits on evidence with no fresh date, or
                fresh evidence shows a DIFFERENT current org
  unknown     — no usable title evidence at all

Evidence freshness = the newest of: a date field on the row (date /
published / fetched_at / crawled_at), any year in the row text, and the
date a LinkedIn tenure badge implies ("Apr 2017 - Present (9 years)" was
rendered in 2026).

Usage (agent-facing): feed it evidence rows (title/url/snippet/date-like
strings) + the claimed org; it returns the grade + the deciding source.
Feed it INDEPENDENT evidence only — never the row the claim came from.
"""
import argparse
import json
import re
import unicodedata
from datetime import date

RECENCY_YEARS = 3

# words that never form an org on their own ("Title at the ...")
_ORG_STOP = {"the", "a", "an", "this", "that", "least", "most", "all", "home",
             "work", "first", "last", "once", "times", "night", "noon"}

_PRESENT_RE = re.compile(r"\bPresent\b|\bCurrent\b|עד היום",
                         re.IGNORECASE)
# an entry ends where the next one begins: a header, a table cell, a line
# break, or a bullet/middle-dot separator between experience items
_ENTRY_END_RE = re.compile(r"###|\||\n|•")


def _norm_text(text: str) -> str:
    """NFKC folds styled Unicode (𝗕𝗼𝗹𝗱 post text, fullwidth) to ASCII so the
    Latin regexes below see it; also unifies dash variants."""
    t = unicodedata.normalize("NFKC", text or "")
    return t.replace("–", "-").replace("—", "-")


def _year(text: str) -> int:
    """First 4-digit year 1960-2099 in text, else 0."""
    m = re.search(r"\b(19[6-9]\d|20[0-9]\d)\b", text or "")
    return int(m.group(1)) if m else 0


_DURATION_RE = re.compile(r"\(\s*(\d{1,2})\s*(?:yrs?|years?)\b", re.IGNORECASE)
_YEARS_RE = re.compile(r"\b(19[6-9]\d|20[0-9]\d)\b")
_DATE_FIELDS = ("date", "published", "published_at", "fetched_at", "crawled_at")


def _max_year(text: str) -> int:
    ys = [int(y) for y in _YEARS_RE.findall(text or "")]
    return max(ys) if ys else 0


def _seen_year(start: int, win: str) -> int:
    """Year the entry was observed: start + rendered tenure, when shown."""
    m = _DURATION_RE.search(win or "")
    if start and m:
        return start + int(m.group(1))
    return start


def evidence_year(row: dict) -> int:
    """Freshness of one evidence row (0 = undated)."""
    parts = [str(row.get(k, "")) for k in ("title", "snippet", "signal") + _DATE_FIELDS]
    return _max_year(_norm_text(" ".join(parts)))


def _window(text: str, start: int, limit: int, next_start: int) -> str:
    """Text after a match, cut at the next entry so a neighbour's dates or
    'Present' badge are never attributed to this entry."""
    end = min(start + limit, next_start)
    win = text[start:end]
    m = _ENTRY_END_RE.search(win)
    # keep the first line even if it starts with a separator
    return win[:m.start()] if m and m.start() > 0 else win


def extract_titles(text: str) -> list:
    """Structured (title, org, year, is_current_flag) tuples from page text.

    Handles the common LinkedIn/cache/stock-profile layouts:
      "Title at Org ... Apr 2017 - Present (9 years)"   (also "Title @ Org")
      "### Dev group manager - Org (Current) Apr 2017 - Present"
      "### Title · Org" / "### Title\\nOrg · Full-time\\nJan 2020 - Present"
      "Name | Title at Org | pay | -- | 1961"          (Yahoo-style rows)
    """
    out = []
    if not text:
        return out
    text = _norm_text(text)

    def _clean(s: str) -> str:
        return re.sub(r"[\s.,]+$", "", s.strip())

    def _org_ok(org: str) -> bool:
        letters = re.sub(r"[^A-Za-z]", "", org)
        return len(letters) >= 2 and org.lower().split()[0] not in _ORG_STOP

    org_at = re.compile(
        r"([A-Za-z][A-Za-z&.,'’\- ]{2,60}?)\s+(?:at|@)\s+([A-Za-z][A-Za-z&.'’\- ]{1,60})")
    matches = list(org_at.finditer(text))
    for i, m in enumerate(matches):
        title, org = _clean(m.group(1)), _clean(m.group(2))
        if not _org_ok(org) or len(title) < 2:
            continue
        nxt = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        win = _window(text, m.end(), 120, nxt)
        y = _year(win) or _year(text[max(0, m.start() - 60):m.start()])
        present = bool(_PRESENT_RE.search(win))
        out.append({"title": title, "org": org, "year": y, "current": present,
                    "seen": _seen_year(y, win)})

    # LinkedIn cache headers: "### <title> <sep> <org> (Current) <dates>"
    # sep is " - " / " · " on one line, or a newline with "Org · Full-time"
    hdr = re.compile(
        r"###\s*([A-Za-z][A-Za-z&.,'’\- ]{1,60}?)\s*(?:\s-\s|·|\n+)\s*"
        r"([A-Za-z][A-Za-z&.'’\- ]{1,60}?)(?=\s*(?:·|\(|\n|$|"
        r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{4}|\d{4}))")
    hmatches = list(hdr.finditer(text))
    for i, m in enumerate(hmatches):
        title, org = _clean(m.group(1)), _clean(m.group(2))
        if not _org_ok(org) or len(title) < 2:
            continue
        nxt = hmatches[i + 1].start() if i + 1 < len(hmatches) else len(text)
        # headers span lines (title / org / dates), so only cut at the next
        # header, not at line breaks
        win = text[m.end():min(m.end() + 160, nxt)]
        win = win.split("###", 1)[0]
        y = _year(win)
        present = bool(_PRESENT_RE.search(win))
        out.append({"title": title, "org": org, "year": y, "current": present,
                    "seen": _seen_year(y, win)})

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


def _org_tokens(s: str) -> list:
    return re.findall(r"[a-z0-9]+", _norm_text(s).lower())


def org_matches(claimed: str, org: str) -> bool:
    """Whole-token containment either way ("Acme" ~ "Acme Card Ltd").

    Substring matching was wrong both ways: claimed "cal" matched "Musical
    Group" / "Local Bank", and an EMPTY org matched every claim.
    """
    c, o = _org_tokens(claimed), _org_tokens(org)
    if not c or not o:
        return False

    def contains(hay, needle):
        n = len(needle)
        return any(hay[i:i + n] == needle for i in range(len(hay) - n + 1))

    return contains(o, c) or contains(c, o)


def grade(claimed_org: str, evidence: list) -> dict:
    """Grade the claimed org against extracted title evidence."""
    claimed = (claimed_org or "").strip()
    if not _org_tokens(claimed):
        return {"grade": "unknown", "reason": "no claimed org"}
    cutoff = date.today().year - RECENCY_YEARS
    confirms, stale_only, other_now = [], [], []
    for ev in evidence:
        text = " ".join(str(ev.get(k, "")) for k in ("title", "snippet"))
        row_year = evidence_year(ev)
        for t in extract_titles(text):
            org = t.get("org") or ""
            year = t.get("year") or 0
            # freshness of the EVIDENCE, not the job start: an explicit
            # Present/Current marker on a fresh page means current, however
            # long ago the role began
            fresh = max(t.get("seen") or 0, row_year, year)
            # stock-profile rows carry the org inside the title text
            hit = org_matches(claimed, org) if org else (
                t.get("org_from_title") and org_matches(claimed, t["title"]))
            entry = {"src": ev.get("url"), "title": t["title"],
                     "org": org, "year": year, "evidence_year": fresh}
            if hit:
                if t.get("current") and fresh >= cutoff:
                    confirms.append(entry)
                else:
                    stale_only.append(entry)
            elif org and t.get("current") and fresh >= cutoff:
                other_now.append(entry)
    if confirms:
        return {"grade": "confirmed", "sources": confirms[:3],
                "reason": f"Present/current at {claimed_org} on evidence dated "
                          f"within {RECENCY_YEARS}y"}
    if other_now:
        return {"grade": "stale", "sources": other_now[:3],
                "reason": "recent evidence shows a DIFFERENT current org — "
                          "claimed card org is historical"}
    if stale_only:
        return {"grade": "stale", "sources": stale_only[:3],
                "reason": "claimed org only in past entries, or its "
                          "Present marker is on undated/old evidence"}
    return {"grade": "unknown", "reason": "no title evidence for claimed org"}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--claimed-org", required=True)
    ap.add_argument("--evidence-file", help="JSONL rows with title/url/snippet")
    ap.add_argument("--text", help="raw text to grade directly")
    a = ap.parse_args()
    rows = []
    if a.evidence_file:
        with open(a.evidence_file, encoding="utf-8") as fh:
            rows = [json.loads(line) for line in fh if line.strip()]
    elif a.text:
        rows = [{"title": "", "url": "inline", "snippet": a.text}]
    print(json.dumps(grade(a.claimed_org, rows), ensure_ascii=False, indent=1))
