#!/usr/bin/env python3
"""cluster.py — deterministic evidence clustering by person-name key.

Groups evidence rows (JSONL from sweep.py, or any {title,url,snippet} rows)
into candidate-person clusters. Same evidence input = same clusters: pure
stdlib, no network, no LLM. Rows that carry no extractable person name go to
an explicit 'unattributed' bucket (kept, never dropped).

Output: JSON {clusters: [{key, display, size, urls, domains}], unattributed: N}
to stdout, or --out file.
"""

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

from evidence import is_error_row, load_evidence

# Latin-script person-name pattern: two capitalized tokens, optionally with
# up to two middle initials. A token may carry Latin-1/Extended-A letters
# (José, Müller, Łukasz), a Mc/Mac/O'/D' prefix (McDonald, O'Brien) and a
# hyphenated second part (Smith-Jones). Deliberately conservative.
_UP = "A-ZÀ-ÖØ-ÞĀ-Ž"
_LO = "a-zß-öø-ÿā-ž"
_TOKEN = (rf"(?:Mc|Mac|O['’]|D['’])?[{_UP}][{_LO}'’]+"
          rf"(?:-[{_UP}][{_LO}'’]+)?")
NAME_RE = re.compile(rf"(?<![\w'’])({_TOKEN}(?:\s+[{_UP}]\.){{0,2}}\s+{_TOKEN})(?![\w])")
STOPWORDS = {
    "The", "This", "That", "And", "For", "With", "From", "About", "Contact",
    "Search", "Results", "Home", "Page", "Official", "Welcome", "Join", "Sign",
    "New", "News", "Video", "Photos", "Best", "Top", "All", "Our", "Your",
    # org-ish tokens — a person display name rarely ends in these
    "Studios", "Studio", "Product", "Core", "Group", "Labs", "Media",
    "Digital", "Systems", "Technologies", "Solutions", "Services", "Capital",
    "Ventures", "Team", "App", "Apps", "Tech", "Software", "Platform",
    "Official", "Profile", "Public", "School", "College", "University",
    # months — titles like "Israel at the UN-Geneva January 26" are dates,
    # not people
    "January", "February", "March", "April", "May", "June", "July",
    "August", "September", "October", "November", "December",
}


_FOLD = str.maketrans({"ł": "l", "Ł": "L", "ø": "o", "Ø": "O", "đ": "d", "Đ": "D",
                       "ß": "ss", "æ": "ae", "Æ": "AE", "œ": "oe", "Œ": "OE",
                       "ı": "i", "þ": "th", "Þ": "Th"})


def norm_key(name: str) -> str:
    """Accent-folded, lowercase, single-spaced key ("José  Müller" -> "jose muller")."""
    # letters NFKD does not decompose
    folded = (name or "").translate(_FOLD)
    folded = unicodedata.normalize("NFKD", folded)
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    folded = re.sub(r"[^a-z ]", "", folded.lower().replace("-", " "))
    return re.sub(r"\s+", " ", folded).strip()


def mentions(text_key: str, name_key: str) -> bool:
    """Whole-word phrase match on normalized keys — "dan cohen" must not
    match "jordan cohen" or "dan cohener"."""
    return bool(name_key) and re.search(
        r"(?<![a-z])" + re.escape(name_key) + r"(?![a-z])", text_key) is not None


def extract_names(text: str) -> list[str]:
    out = []
    for m in NAME_RE.finditer(text or ""):
        tokens = m.group(1).split()
        if any(t in STOPWORDS for t in tokens):
            continue
        if len(norm_key(m.group(1))) >= 5:  # skip degenerate matches
            out.append(m.group(1))
    return out


def domain_of(url: str) -> str:
    try:
        return url.split("/")[2].lower().removeprefix("www.")
    except Exception:
        return ""


def cluster_rows(rows: list[dict], seed_name: str = "") -> dict:
    seed_key = norm_key(seed_name) if seed_name else ""
    buckets: dict[str, dict] = {}
    unattributed = 0
    for row in rows:
        if is_error_row(row) or not row.get("url"):
            continue
        text = " | ".join(
            str(row.get(k) or "") for k in ("title", "snippet", "signal")
        )
        names = extract_names(text)
        # seed-aware attribution: a row mentioning the seed name belongs to
        # the seed's cluster even when the title leads with an org name.
        # Matched on accent-folded keys at word boundaries (see mentions()).
        if seed_key and mentions(norm_key(text), seed_key):
            names = [seed_name] + [n for n in names if norm_key(n) != seed_key]
        if not names:
            unattributed += 1
            continue
        # the row is attributed to its PRIMARY (first) name only; co-mentioned
        # names do not get the row (that would inflate every associate)
        key = norm_key(names[0])
        b = buckets.setdefault(
            key, {"key": key, "display": names[0], "urls": [], "domains": set()}
        )
        b["urls"].append(row["url"])
        d = domain_of(row["url"])
        if d:
            b["domains"].add(d)
    clusters = []
    for b in buckets.values():
        clusters.append(
            {
                "key": b["key"],
                "display": b["display"],
                # distinct URLs — a URL surfaced by two queries is one source
                "size": len(set(b["urls"])),
                "urls": sorted(set(b["urls"])),
                "domains": sorted(b["domains"]),
            }
        )
    clusters.sort(key=lambda c: (-c["size"], c["key"]))  # deterministic order
    return {"clusters": clusters, "unattributed": unattributed}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("evidence_file", help="evidence JSONL/JSON from sweep.py")
    ap.add_argument("--seed-name", default="", help="attribute rows mentioning this name to its cluster")
    ap.add_argument("--top", type=int, default=5, help="keep top N clusters")
    ap.add_argument("--out", help="optional output file (default stdout)")
    args = ap.parse_args(argv)

    rows = load_evidence(Path(args.evidence_file))
    result = cluster_rows(rows, seed_name=args.seed_name)
    result["clusters"] = result["clusters"][: args.top]
    text = json.dumps(result, indent=2, ensure_ascii=False)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
        print(f"wrote {args.out}: {len(result['clusters'])} cluster(s)")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
