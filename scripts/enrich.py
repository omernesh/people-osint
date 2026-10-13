#!/usr/bin/env python3
"""enrich.py — seed enrichment: never settle for a name-only seed.

Gathers additional identity indicators (phone / email / handle) for a seed
person, from three source classes, then emits an enriched seed ready for
scripts/dorks.py — where name+phone / name+email cross-references become the
strongest match basis.

Sources (all optional, all local, best-effort):
  1. --indicators-file JSON   agent-provided hints (e.g. from Chatlytics /
                              Lextrove MCP lookups): {"phones": [], "emails":
                              [], "handles": [], "source": "..."}
  2. --sources gws             Google Contacts (searchContacts + otherContacts)
                              and Gmail message headers via the gws CLI.
  3. --evidence-file JSONL     mine existing sweep evidence (title/snippet)
                              for phones / emails / profile handles.

Rules: explicit CLI seeds are NEVER overwritten (they win); new values are
deduplicated and carry provenance. PII stays on this machine — the enriched
seed file must be written under the local osint data dir, never committed.

Usage:
  enrich.py --name "First Last" [--phone P] [--email E] [--social S]
            [--indicators-file hints.json] [--sources gws]
            [--evidence-file evidence.jsonl] [--out seed.json]
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# international-ish phone: optional +, 9-15 digits with optional separators
PHONE_RE = re.compile(r"(?:\+\d[\d\s\-().]{7,15}\d)|(?:\(?0\d{1,2}\)?[\s\-]?\d{3}[\s\-]?\d{4})")
HANDLE_URL_RE = re.compile(
    r"https?://(?:www\.)?(?:linkedin\.com/in/|x\.com/|twitter\.com/|github\.com/|"
    r"instagram\.com/|facebook\.com/|medium\.com/@)([A-Za-z0-9._-]{2,50})"
)


def digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def plausible_phone(raw: str) -> bool:
    d = digits(raw)
    if not (9 <= len(d) <= 15):
        return False
    if re.fullmatch(r"(19|20)\d{2}[\d\s]*", raw.strip()):  # years / dates
        return False
    return True


def gws_json(args: list[str], timeout: int = 30):
    """Run a gws command, return parsed JSON or None (never raises)."""
    try:
        p = subprocess.run(["gws", *args], capture_output=True, text=True, timeout=timeout)
    except Exception:
        return None
    out = p.stdout or ""
    start = out.find("{")
    if start < 0:
        return None
    try:
        return json.loads(out[start:])
    except Exception:
        return None


def _name_matches(name: str, tokens: list[str]) -> bool:
    """All seed tokens must appear as whole words (not substrings)."""
    for t in tokens:
        if not re.search(r"\b" + re.escape(t) + r"\b", name.lower()):
            return False
    return True


def harvest_gws_contacts(name: str) -> tuple[list[dict], list[str]]:
    found, notes = [], []
    tokens = [t.lower() for t in name.split() if t]
    for resource, cmd in (
        ("contacts", ["people", "people", "searchContacts"]),
        ("otherContacts", ["people", "otherContacts", "search"]),
    ):
        data = gws_json(
            [*cmd, "--params",
             json.dumps({"query": name, "readMask": "names,emailAddresses,phoneNumbers"})]
        )
        if not data:
            notes.append(f"gws {resource}: no response")
            continue
        rows = []
        for key in ("results", "otherContacts", "people", "connections"):
            if isinstance(data.get(key), list):
                rows = data[key]
                break
        for row in rows:
            person = row.get("person", row)
            names = [n.get("displayName", "") for n in person.get("names", []) or []]
            if names and not any(_name_matches(n, tokens) for n in names if n):
                continue  # name must match seed tokens on word boundaries
            for e in person.get("emailAddresses", []) or []:
                v = (e.get("value") or "").strip().lower()
                if v:
                    found.append({"field": "email", "value": v,
                                  "origin": f"gws:{resource}",
                                  "detail": names[0] if names else ""})
            for ph in person.get("phoneNumbers", []) or []:
                v = (ph.get("value") or "").strip()
                if v and plausible_phone(v):
                    found.append({"field": "phone", "value": v,
                                  "origin": f"gws:{resource}",
                                  "detail": names[0] if names else ""})
    return found, notes


def harvest_gws_mail(name: str, max_msgs: int = 3) -> tuple[list[dict], list[str]]:
    found, notes = [], []
    data = gws_json(
        ["gmail", "users", "messages", "list", "--params",
         json.dumps({"userId": "me", "q": name, "maxResults": max_msgs})]
    )
    if not data:
        notes.append("gws gmail: no response")
        return found, notes
    ids = [m.get("id") for m in (data.get("messages") or [])][:max_msgs]
    for mid in ids:
        msg = gws_json(
            ["gmail", "users", "messages", "get", "--params",
             json.dumps({"userId": "me", "id": mid, "format": "metadata",
                         "metadataHeaders": ["From", "To"]})]
        )
        if not msg:
            continue
        headers = (msg.get("payload", {}) or {}).get("headers", []) or []
        for h in headers:
            for v in EMAIL_RE.findall(h.get("value", "") or ""):
                found.append({"field": "email", "value": v.lower(),
                              "origin": "gws:gmail", "detail": f"header {h.get('name')}"})
    return found, notes


def mine_evidence(path: Path) -> tuple[list[dict], list[str]]:
    found, notes = [], []
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        return found, [f"evidence unreadable: {e}"]
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("status") == "engine_error" or not row.get("url"):
            continue
        blob = " | ".join(str(row.get(k) or "") for k in ("title", "snippet", "signal"))
        for m in EMAIL_RE.findall(blob):
            if not m.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")):
                found.append({"field": "email", "value": m.lower(),
                              "origin": f"web:{url_host(row['url'])}",
                              "detail": "evidence snippet"})
        for m in PHONE_RE.findall(blob):
            if plausible_phone(m):
                found.append({"field": "phone", "value": m.strip(),
                              "origin": f"web:{url_host(row['url'])}",
                              "detail": "evidence snippet"})
        for h in HANDLE_URL_RE.findall(row["url"]):
            found.append({"field": "handle", "value": h,
                          "origin": "web:profile-url", "detail": row["url"]})
    return found, notes


def url_host(url: str) -> str:
    try:
        return url.split("/")[2].lower().removeprefix("www.")
    except Exception:
        return "web"


def load_indicators(path: Path) -> tuple[list[dict], list[str]]:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        return [], [f"indicators unreadable: {e}"]
    src = d.get("source", "internal")
    found = []
    for ph in d.get("phones") or []:
        if plausible_phone(str(ph)):
            found.append({"field": "phone", "value": str(ph).strip(),
                          "origin": src, "detail": "internal resource"})
    for e in d.get("emails") or []:
        if EMAIL_RE.fullmatch(str(e).strip()):
            found.append({"field": "email", "value": str(e).strip().lower(),
                          "origin": src, "detail": "internal resource"})
    for h in d.get("handles") or []:
        found.append({"field": "handle", "value": str(h).strip(),
                      "origin": src, "detail": "internal resource"})
    return found, []


def merge(seed: dict, found: list[dict]) -> tuple[dict, list[dict]]:
    """Fill seed fields from found values; explicit seed values always win."""
    added, seen = [], set()
    def norm(field, v):
        return (field, digits(v) if field == "phone" else v.lower()) if field == "phone" \
            else (field, v.strip().lower())
    for item in found:
        field, value = item["field"], item["value"].strip()
        key = norm(field, value)
        if key in seen:
            continue
        seen.add(key)
        if field == "handle":
            continue  # handles ride in notes; dorks derives its own handle candidates
        if not seed.get(field):
            seed[field] = value
            added.append(item)
    return seed, added


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", required=True)
    ap.add_argument("--phone", default="")
    ap.add_argument("--email", default="")
    ap.add_argument("--social", default="")
    ap.add_argument("--indicators-file")
    ap.add_argument("--sources", default="", help="comma list: gws (contacts+gmail)")
    ap.add_argument("--evidence-file")
    ap.add_argument("--out")
    args = ap.parse_args(argv)

    seed = {"name": args.name.strip()}
    for k in ("phone", "email", "social"):
        v = getattr(args, k).strip()
        if v:
            seed[k] = v
    found: list[dict] = []
    notes: list[str] = []

    if args.indicators_file:
        f, n = load_indicators(Path(args.indicators_file))
        found += f
        notes += n
    if "gws" in args.sources.lower():
        f, n = harvest_gws_contacts(args.name)
        found += f
        notes += n
        f, n = harvest_gws_mail(args.name)
        found += f
        notes += n
    if args.evidence_file:
        f, n = mine_evidence(Path(args.evidence_file))
        found += f
        notes += n

    seed, added = merge(seed, found)
    handles = [i["value"] for i in found if i["field"] == "handle"]
    result = {
        "seed": seed,
        "added": added,
        "handles": sorted(set(handles)),
        "notes": notes,
        "dorks_args": ["--name", seed["name"]]
        + (["--phone", seed["phone"]] if seed.get("phone") else [])
        + (["--email", seed["email"]] if seed.get("email") else [])
        + (["--social", seed["social"]] if seed.get("social") else []),
    }
    text = json.dumps(result, indent=2, ensure_ascii=False)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
        print(f"wrote {args.out}: +{len(added)} indicator(s)")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
