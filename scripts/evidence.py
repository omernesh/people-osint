#!/usr/bin/env python3
"""Evidence file helpers shared by the scan step (stdlib only).

sweep.py writes JSONL (one JSON object per line). Older callers wrote a JSON
array. `load_evidence` accepts both, plus an empty file, so a consumer never
crashes on `json.load` for files with 2+ rows.
"""
import json
from pathlib import Path


def load_evidence(path):
    """Return a list of row dicts from a JSONL file or a JSON array/object file."""
    text = Path(path).read_text(encoding="utf-8").strip()
    if not text:
        return []
    try:  # whole-file JSON: array, or {"evidence": [...]}
        data = json.loads(text)
    except json.JSONDecodeError:
        data = None
    else:
        if isinstance(data, list):
            return [r for r in data if isinstance(r, dict)]
        if isinstance(data, dict):
            ev = data.get("evidence")
            return [r for r in ev if isinstance(r, dict)] if isinstance(ev, list) else [data]
        return []
    rows = []
    for n, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as e:
            raise ValueError(f"{path}:{n}: not valid JSON ({e.msg})") from None
        if isinstance(row, dict):
            rows.append(row)
    return rows


def is_error_row(row):
    return bool(row.get("error")) or row.get("status") == "engine_error"


def to_jev_rows(rows):
    """Usable evidence rows with provenance, deduped by URL.

    Each row keeps `source` (which query/engine surfaced it) and `signal`
    (snippet, else title) so identity is never judged on a bare URL.
    """
    out, seen = [], set()
    for r in rows:
        url = (r.get("url") or "").strip()
        if is_error_row(r) or not url:
            continue
        if url in seen:
            # duplicate URL: merge structured payloads into the kept row so a
            # later enrichment row (e.g. career) isn't dropped by dedupe
            for kept in out:
                if kept["url"] == url:
                    for extra in ("career",):
                        if r.get(extra) and not kept.get(extra):
                            kept[extra] = r[extra]
                    break
            continue
        seen.add(url)
        q = r.get("query", "")
        row = {
            "url": url,
            "source": r.get("source") or (f"{r.get('engine', 'search')}: {q}" if q else r.get("engine", "?")),
            "signal": r.get("signal") or r.get("snippet") or r.get("title") or "",
            "query": q,
        }
        # pass through optional structured payloads (career, etc.) so the
        # caller's enrichment columns survive the JEV projection
        for extra in ("career",):
            if r.get(extra):
                row[extra] = r[extra]
        out.append(row)
    return out


def engine_failed(rows):
    """True when rows exist and every one is an engine error (outage), or none at all."""
    return not rows or all(is_error_row(r) for r in rows)
