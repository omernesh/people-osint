#!/usr/bin/env python3
"""Scan step: evidence file -> identity verdict (no LLM beyond the JEV call).

  python3 scan.py --name "Jane Doe" --phone +15550100 --evidence-file ev.jsonl
  python3 scan.py ... --sherlock --handle janedoe     # opt-in handle pivot

Exit codes: 0 verdict printed, 3 engine_error (the search step failed or
produced only error rows - never reported as `insufficient`).
Sherlock is OFF by default: it is slow (~400 direct site probes) and proves a
profile exists, not who owns it.
"""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import evidence  # noqa: E402
import jev_verify  # noqa: E402


def sherlock_rows(handles, timeout=120):
    """Existence-only evidence rows from sherlock text mode. [] if unavailable."""
    exe = shutil.which("sherlock")
    if not exe:
        return []
    rows = []
    for h in handles:
        try:
            r = subprocess.run([exe, h, "--print-found", "--timeout", "10"],
                               capture_output=True, text=True, timeout=timeout)
        except (subprocess.TimeoutExpired, OSError):
            continue
        for line in r.stdout.splitlines():
            if "http" in line and line.strip().startswith("[+]"):
                url = line[line.index("http"):].strip()
                rows.append({"url": url, "engine": "sherlock",
                             "signal": f"handle '{h}' exists (existence only, not identity)"})
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--name", required=True)
    ap.add_argument("--phone", default="")
    ap.add_argument("--evidence-file", required=True)
    ap.add_argument("--sherlock", action="store_true",
                    help="opt in to the sherlock handle pivot (default: off)")
    ap.add_argument("--handle", action="append", default=[])
    a = ap.parse_args(argv)

    raw = evidence.load_evidence(a.evidence_file)
    if evidence.engine_failed(raw):
        print(json.dumps({"verdict": "engine_error",
                          "reason": "no usable search results; search step failed or empty"}))
        return 3
    rows = evidence.to_jev_rows(raw)
    if a.sherlock and a.handle:
        rows += evidence.to_jev_rows(sherlock_rows(a.handle))
    verdict, conf = jev_verify.verify_identity_retry(a.name, a.phone, rows)
    print(json.dumps({"verdict": verdict, "confidence": conf,
                      "evidence_rows": len(rows)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
