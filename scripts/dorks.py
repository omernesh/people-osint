#!/usr/bin/env python3
"""Deterministic dork-sweep query generator (pure script, no LLM).

Input: any subset of name / phone / email / social link.
Output: an ordered query plan — search-engine dorks + sherlock handles.

The agent runs each query via its search tool; nothing here decides identity,
it only enumerates the search surface. Canonical handles found in results are
fed to sherlock for cross-platform existence.
"""
import argparse
import json
import re

DORK_SITES = ["linkedin.com", "x.com", "facebook.com", "crunchbase.com",
              "github.com", "instagram.com", "youtube.com", "medium.com",
              "substack.com", "twitter.com"]
PHONE_SITES = ["linkedin.com", "facebook.com", "crunchbase.com"]


def slug_handle(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def plan(name="", phone="", email="", social=""):
    out = {"queries": [], "handles": [], "note": ""}
    q = out["queries"]
    digits = re.sub(r"\D", "", phone or "")

    if name:
        n = name.strip()
        for site in DORK_SITES:
            q.append({"query": f'"{n}" site:{site}',
                      "goal": f"{site} profile for {n}"})
        q.append({"query": f'"{n}"', "goal": f"general search for {n}"})
        q.append({"query": f'"{n}" Israel', "goal": f"local-context search for {n}"})
        if email:
            q.append({"query": f'"{n}" "{email}"',
                      "goal": "name+email cross-ref"})
        if phone and digits:
            q.append({"query": f'"{n}" "{digits}"',
                      "goal": "name+phone cross-ref"})
        local = re.sub(r"^972", "0", digits)
        if local and digits and len(digits) > 7:
            q.append({"query": f'"{n}" "{local}"',
                      "goal": "name+local-format phone cross-ref"})
        # handle candidates
        parts = [p for p in re.split(r"[^a-zA-Z0-9]+", n) if p]
        if len(parts) >= 2:
            f, l = parts[0], parts[-1]
            out["handles"] += [slug_handle(f + l), slug_handle(f + "." + l),
                               slug_handle(f + l) + "_", slug_handle(f) + slug_handle(l)]
            out["handles"] += [slug_handle(f) + slug_handle(l[:4]),
                               slug_handle(f[:4]) + slug_handle(l)]

    if phone:
        if digits:
            for site in PHONE_SITES:
                q.append({"query": f'"{digits}" site:{site}',
                          "goal": f"phone indexed on {site}"})
        local = re.sub(r"^972", "0", digits)
        if local and len(local) > 7:
            q.append({"query": f'"{local}"', "goal": "local-format phone search"})
        hy = f"{local[:3]}-{local[3:6]}-{local[6:]}" if len(local) >= 9 else ""
        if hy:
            q.append({"query": f'"{hy}"', "goal": "hyphenated phone search"})

    if email:
        e = email.strip()
        q.append({"query": f'"{e}"', "goal": "exact email search"})
        user = e.split("@")[0] if "@" in e else ""
        if user and len(user) > 3:
            q.append({"query": f'"{user}"', "goal": "email handle search"})
            out["handles"].append(slug_handle(user))

    if social:
        q.append({"query": f'"{social}"', "goal": "exact social profile"})
        m = re.search(r"/([^/\?]+)/?$", social)
        if m:
            out["handles"].append(m.group(1))

    # dedupe, keep order
    seen = set()
    qq = []
    for item in q:
        k = item["query"]
        if k not in seen:
            seen.add(k)
            qq.append(item)
    out["queries"] = qq
    out["handles"] = list(dict.fromkeys(h for h in out["handles"] if h))
    out["note"] = ("Zero hits on a phone/email query is a finding (clean "
                   "footprint), not a failure.")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--name", default="")
    ap.add_argument("--phone", default="")
    ap.add_argument("--email", default="")
    ap.add_argument("--social", default="")
    a = ap.parse_args()
    print(json.dumps(plan(a.name, a.phone, a.email, a.social),
                     ensure_ascii=False, indent=1))
