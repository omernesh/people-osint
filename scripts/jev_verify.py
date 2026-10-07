#!/usr/bin/env python3
"""Typed-choice verification client (TypeSafe AI System One).

Returns a choice + calibrated confidence, never prose — ideal for identity
decisions (match / no_match / insufficient) and category tagging.
Stdlib only. Key resolution order:
    1. env TYPESAFE_API_KEY
    2. ~/.hermes/.env line TYPESAFE_API_KEY=...
"""
import json
import os
import re
import time
import urllib.request

API_URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-1.13.0"
HEBREW_RE = re.compile(r"[\u0590-\u05FF]")
MIN_CONFIDENCE = 0.35  # below this → None (leave uncategorized, retry later)

# Latin-script support is solid; non-Latin scripts (Hebrew, Arabic,
# Cyrillic, CJK) are unverified → keep them out of criteria keys. Allowed chars: ASCII
# printable + Latin-1 Supplement + Latin Extended-A/B + a few extras.
_LATIN_ALLOWED = (set(range(0x20, 0x7F))      # ASCII printable
                  | set(range(0xC0, 0x250))   # Latin-1 supp + Ext-A/B
                  | set(range(0x1E00, 0x1F00))  # Latin Ext Additional
                  | set(range(0x2C60, 0x2C80))  # Latin Ext-C
                  | set(range(0xA720, 0xA740)))  # Latin Ext-D


def _latin_ok(s):
    return all(ord(ch) in _LATIN_ALLOWED for ch in (s or ""))


def _load_key():
    key = os.environ.get("TYPESAFE_API_KEY", "").strip()
    if key:
        return key
    env_path = os.path.expanduser("~/.hermes/.env")
    try:
        with open(env_path, encoding="utf-8") as fh:
            for line in fh:
                if line.strip().startswith("TYPESAFE_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def key_available() -> bool:
    return bool(_load_key())


def is_english(categories) -> bool:
    """Legacy alias — see supports_jev()."""
    return supports_jev(categories)


def supports_jev(categories) -> bool:
    """Routing gate: criteria keys must be Latin-script only."""
    for c in categories or []:
        if c and not _latin_ok(c):
            return False
    return True


def classify(title, text, categories, timeout=60):
    """One typed choice call → (category, confidence) or (None, 0.0).

    For OSINT identity checks call verify_identity() instead. Raises on
    transport/API errors after the caller's retry policy.
    """
    cats = [c for c in categories if c]
    if not cats:
        return None, 0.0
    state = (
        "Pick the single best answer.\n\n"
        "Title: %s\n\nBody: %s" % ((title or "").strip()[:300],
                                   (text or "").strip()[:1500])
    )
    questions = {
        "q": {
            "type": "choice",
            "instructions": (
                "Pick the single best-fitting category for the post. "
                "Choose strictly from the criteria keys."),
            "criteria": {c: c for c in cats},
        }
    }
    body = json.dumps({"state": state, "model": MODEL,
                       "questions": questions}).encode()
    req = urllib.request.Request(
        API_URL, data=body,
        headers={"Authorization": "Bearer " + _load_key(),
                 "Content-Type": "application/json"},
        method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode())
    ans = ((data.get("answers") or {}).get("q") or {})
    choice = ans.get("choice")
    conf = float(ans.get("confidence") or 0.0)
    if choice in cats and conf >= MIN_CONFIDENCE:
        return choice, conf
    return None, conf


def classify_retry(title, text, categories, attempts=3, backoff=2.0):
    """classify() with retry/backoff; returns (category, confidence)."""
    for i in range(1, attempts + 1):
        try:
            return classify(title, text, categories)
        except Exception:  # noqa: BLE001 — transport/API hiccup
            if i == attempts:
                return None, 0.0
            time.sleep(backoff * i)
    return None, 0.0


def verify_identity(seed_name, seed_phone, evidence, timeout=60):
    """Typed OSINT identity check → (verdict, confidence).

    verdict: 'match' | 'no_match' | 'insufficient'
    Confidence < 0.5 on 'match' → treat as insufficient (skill rule).
    """
    ev_lines = "\n".join(
        "- [%s] %s (%s)" % (e.get("source", "?"), e.get("signal", ""),
                            e.get("url", "")) for e in (evidence or [])[:12])
    state = (
        "Seed identity: name=%r phone=%r\n\nPublic evidence found:\n%s\n\n"
        "Does the public evidence refer to the SAME person as the seed?"
        % (seed_name, seed_phone, ev_lines or "- (none)"))
    questions = {"q": {
        "type": "choice",
        "instructions": (
            "Decide whether the public evidence identifies the seed person. "
            "Choose strictly from the criteria keys."),
        "criteria": {
            "match": "evidence clearly refers to the seed person (2+ independent signals)",
            "no_match": "evidence clearly refers to a different person",
            "insufficient": "evidence is too weak or ambiguous to decide",
        },
    }}
    body = json.dumps({"state": state, "model": MODEL,
                       "questions": questions}).encode()
    req = urllib.request.Request(
        API_URL, data=body,
        headers={"Authorization": "Bearer " + _load_key(),
                 "Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode())
    ans = (data.get("answers") or {}).get("q") or {}
    choice = ans.get("choice")
    conf = float(ans.get("confidence") or 0.0)
    if choice in ("match", "no_match", "insufficient"):
        if choice == "match" and conf < 0.5:
            return "insufficient", conf
        return choice, conf
    return "insufficient", conf
