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
from urllib.parse import urlparse

DORK_SITES = ["linkedin.com", "x.com", "facebook.com", "crunchbase.com",
              "github.com", "instagram.com", "youtube.com", "medium.com",
              "substack.com", "twitter.com"]
PHONE_SITES = ["linkedin.com", "facebook.com", "crunchbase.com"]


def slug_handle(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


# Transliteration variant rules for Latin-script surnames (Israeli contact
# cards mix spellings). Each rule maps a pattern to a replacement; applied
# per rule, never recursively. Deterministic and capped.
VARIANT_RULES = [
    (re.compile(r"(?<!c)k", re.IGNORECASE), "c"),    # Kaplan -> Caplan (not "ck")
    (re.compile(r"c(?![hk])", re.IGNORECASE), "k"),  # Cohen -> Kohen (keep ch/ck)
    (re.compile(r"ph", re.IGNORECASE), "f"),         # Yoseph -> Yosef
    (re.compile(r"berg\b", re.IGNORECASE), "burg"),  # Greenberg -> Greenburg
    (re.compile(r"burg\b", re.IGNORECASE), "berg"),
    (re.compile(r"(?<=.)y", re.IGNORECASE), "i"),    # Maymon -> Maimon (not initial Y)
    (re.compile(r"([a-z])\1", re.IGNORECASE), r"\1"),  # Sasson -> Sason
]

MAX_VARIANTS = 6


def _case_like(src: str, repl: str) -> str:
    """Replacement text in the case of the text it replaces (K -> C, k -> c)."""
    return repl[:1].upper() + repl[1:] if src[:1].isupper() else repl


def last_name_variants(last: str) -> list:
    """Deterministic transliteration variants of a Latin last name.

    Replacements keep the original capitalisation, so "Kaplan" -> "Caplan"
    (not "caplan") — the variant is shown back in goals and reports.
    """
    out = []
    if not last or not last.isascii():
        return out
    for rx, repl in VARIANT_RULES:
        if "\\" in repl:
            v = rx.sub(repl, last)
        else:
            v = rx.sub(lambda m, r=repl: _case_like(m.group(0), r), last)
        if v != last and v.isalpha() and v.lower() not in {x.lower() for x in out + [last]}:
            out.append(v)
    return out[:MAX_VARIANTS]


def local_phone(digits: str, default_cc: str = "") -> str:
    """Local-format digits for an international number, else the input as-is.

    Only strips a country code we actually know: `default_cc` or the 972
    prefix. Never guesses for other numbers.
    """
    for cc in (default_cc, "972"):
        if cc and digits.startswith(cc) and len(digits) > len(cc) + 6:
            return ("0" if cc != "1" else "") + digits[len(cc):]
    return digits


def hyphenate(local: str) -> str:
    """10 digits -> 3-3-4; anything else -> "" (no guessing)."""
    if len(local) == 10:
        return f"{local[:3]}-{local[3:6]}-{local[6:]}"
    return ""


def handle_from_url(social: str) -> str:
    """Last path segment of a profile URL, ignoring query/fragment.
    `profile.php?id=123` falls back to the id."""
    u = urlparse(social if "//" in social else "//" + social)
    seg = [x for x in u.path.split("/") if x]
    if seg and seg[-1] != "profile.php":
        return seg[-1]
    m = re.search(r"(?:^|&)id=([^&]+)", u.query)
    return m.group(1) if m else ""


def plan(name="", phone="", email="", social="", region="", default_cc=""):
    out = {"queries": [], "handles": [], "note": ""}
    q = out["queries"]
    digits = re.sub(r"\D", "", phone or "")

    if name:
        n = name.strip()
        for site in DORK_SITES:
            q.append({"query": f'"{n}" site:{site}',
                      "goal": f"{site} profile for {n}"})
        q.append({"query": f'"{n}"', "goal": f"general search for {n}"})
        # transliteration variants — capped, LinkedIn + general only
        parts = [p for p in re.split(r"[^a-zA-Z0-9]+", n) if p]
        if len(parts) >= 2:
            f_name, l_name = parts[0], parts[-1]
            for v in last_name_variants(l_name)[:3]:
                vn = f"{f_name} {v}"
                q.append({"query": f'"{vn}" site:linkedin.com',
                          "goal": f"variant spelling ({l_name}->{v}) LinkedIn"})
                q.append({"query": f'"{vn}"',
                          "goal": f"variant spelling ({l_name}->{v}) general"})
        if region:
            q.append({"query": f'"{n}" {region}',
                      "goal": f"local-context search for {n}"})
        if email:
            q.append({"query": f'"{n}" "{email}"',
                      "goal": "name+email cross-ref"})
        if phone and digits:
            q.append({"query": f'"{n}" "{digits}"',
                      "goal": "name+phone cross-ref"})
        local = local_phone(digits, default_cc)
        if local != digits and len(digits) > 7:
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
        local = local_phone(digits, default_cc)
        if local != digits and len(local) > 7:
            q.append({"query": f'"{local}"', "goal": "local-format phone search"})
        hy = hyphenate(local)
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
        h = handle_from_url(social)
        if h:
            out["handles"].append(h)

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
    ap.add_argument("--region", default="",
                    help="optional locale word added to one name query; none by default")
    ap.add_argument("--default-cc", default="",
                    help="country calling code to strip for the local-format phone (e.g. 972, 44)")
    a = ap.parse_args()
    print(json.dumps(plan(a.name, a.phone, a.email, a.social, a.region, a.default_cc),
                     ensure_ascii=False, indent=1))
