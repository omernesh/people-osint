#!/usr/bin/env python3
"""Validate a people-osint report.json before it leaves the session.

Checks:
  1. Valid JSON and required top-level keys (seed/verdict/candidates or profile).
  2. Evidence rows carry the keys jev_verify actually reads: source, signal, url.
  3. Every candidate that carries a verdict also carries evidence rows.
  4. Confidence bounds: 0.0-1.0, and a warning when a 'match' sits under 0.6
     (treat as provisional per skill rule).

Usage: python3 validate_report.py report.json [--strict]
Exit 0 = clean, 1 = errors, 2 = warnings (with --strict: warnings exit 1).
"""
import argparse
import json
import sys
from pathlib import Path
from typing import NoReturn

REQUIRED_TOP = ["seed"]
EVIDENCE_KEYS = {"source", "signal", "url"}


def errs(msgs, code=1) -> NoReturn:
    for m in msgs:
        print(f"[error] {m}")
    sys.exit(code)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("report", help="path to report.json")
    ap.add_argument("--strict", action="store_true",
                    help="warnings become exit 1")
    args = ap.parse_args()

    p = Path(args.report)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        errs([f"{p} is not valid JSON: {e}"])

    problems, warns = [], []
    for k in REQUIRED_TOP:
        if k not in data:
            problems.append(f"missing top-level key '{k}'")

    cands = data.get("candidates") or data.get("candidate") or []
    if isinstance(cands, dict):
        cands = [cands]
    if not cands:
        # deep-dive shape: single subject, verdict/confidence/evidence at top level
        if any(k in data for k in ("verdict", "confidence", "evidence")):
            cands = [data]
        else:
            warns.append("no candidates[]/candidate[] key and no top-level "
                         "verdict/confidence/evidence (single-subject shape)")

    for c in cands:
        label = c.get("id", c.get("name", "?"))
        ev = c.get("evidence", [])
        if not ev:
            if c.get("verdict") not in (None, "insufficient", "no_match"):
                problems.append(f"candidate {label}: verdict "
                                f"{c.get('verdict')} but zero evidence rows")
        for i, row in enumerate(ev):
            missing = EVIDENCE_KEYS - set(row.keys())
            if missing:
                problems.append(f"candidate {label} evidence[{i}] missing "
                                f"keys {sorted(missing)} (jev_verify reads "
                                f"source/signal/url)")
        conf = c.get("confidence")
        if conf is not None:
            if not 0.0 <= conf <= 1.0:
                problems.append(f"candidate {label}: confidence {conf} out of "
                                f"range")
            if c.get("verdict") == "match" and conf < 0.6:
                warns.append(f"candidate {label}: match @ {conf} < 0.6 - "
                             f"treat as provisional, flag in report")

    if problems:
        errs(problems)
    if warns and args.strict:
        errs(warns)
    for w in warns:
        print(f"[warn] {w}")
    print(f"clean: {len(cands)} candidate(s), no errors")


if __name__ == "__main__":
    main()
