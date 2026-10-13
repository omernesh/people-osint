"""Tests for scripts/enrich.py — seed enrichment. Fake data only, no network."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import enrich  # noqa: E402


# ---------- phone plausibility ----------

def test_plausible_phone_accepts_international():
    assert enrich.plausible_phone("+972-54-123-4567")
    assert enrich.plausible_phone("054 123 4567")


def test_plausible_phone_rejects_years_and_short():
    assert not enrich.plausible_phone("2026")
    assert not enrich.plausible_phone("1985-1990")
    assert not enrich.plausible_phone("12345")


# ---------- evidence mining ----------

def test_mine_evidence_extracts_phone_email_handle(tmp_path):
    ev = tmp_path / "evidence.jsonl"
    ev.write_text(
        "\n".join(
            [
                json.dumps({"status": "ok", "url": "https://example.com/page",
                            "title": "John Smith - CEO",
                            "snippet": "Contact: john.smith@example.com or +1 415 555 0100"}),
                json.dumps({"status": "engine_error", "error": "x"}),
                json.dumps({"status": "ok", "url": "https://linkedin.com/in/johnsmith",
                            "title": "John Smith", "snippet": "profile"}),
            ]
        ),
        encoding="utf-8",
    )
    found, _ = enrich.mine_evidence(ev)
    by_field = {}
    for f in found:
        by_field.setdefault(f["field"], []).append(f["value"])
    assert "john.smith@example.com" in by_field["email"]
    assert by_field["email"] == ["john.smith@example.com"]  # engine_error row skipped
    assert any("415 555 0100" in p for p in by_field["phone"])
    assert "johnsmith" in by_field["handle"]


def test_mine_evidence_skips_image_emails(tmp_path):
    ev = tmp_path / "evidence.jsonl"
    ev.write_text(
        json.dumps({"status": "ok", "url": "https://example.com",
                    "title": "logo@2x.png", "snippet": "img sprite@3x.webp"}),
        encoding="utf-8",
    )
    found, _ = enrich.mine_evidence(ev)
    assert found == []


# ---------- merge precedence ----------

def test_merge_explicit_seed_wins():
    seed = {"name": "John Smith", "phone": "+1 200 000 0000"}
    found = [
        {"field": "phone", "value": "+1 999 999 9999", "origin": "web:x", "detail": ""},
        {"field": "email", "value": "New@Example.com", "origin": "gws:contacts", "detail": ""},
    ]
    merged, added = enrich.merge(seed, found)
    assert merged["phone"] == "+1 200 000 0000"  # explicit wins
    assert merged["email"] == "New@Example.com"  # gap filled
    assert [a["field"] for a in added] == ["email"]


def test_merge_dedupes_by_normalized_phone():
    seed = {"name": "X"}
    found = [
        {"field": "phone", "value": "+972 54-123-4567", "origin": "a", "detail": ""},
        {"field": "phone", "value": "+972541234567", "origin": "b", "detail": ""},
    ]
    merged, added = enrich.merge(seed, found)
    assert len(added) == 1


# ---------- indicators file ----------

def test_load_indicators_validates(tmp_path):
    p = tmp_path / "hints.json"
    p.write_text(json.dumps({
        "source": "chatlytics",
        "phones": ["+972 50 000 0000", "not-a-phone"],
        "emails": ["a@b.co", "bad@@nope"],
        "handles": ["somehandle"],
    }), encoding="utf-8")
    found, notes = enrich.load_indicators(p)
    assert any(f["value"] == "+972 50 000 0000" for f in found)
    assert any(f["value"] == "a@b.co" for f in found)
    assert any(f["field"] == "handle" for f in found)
    assert not any("not-a-phone" in str(f["value"]) for f in found)


# ---------- gws adapters (patch point: enrich.gws_json) ----------

def test_harvest_gws_contacts_matches_name(monkeypatch):
    def fake_gws_json(args, timeout=30):
        if "searchContacts" in args:
            return {"results": [
                {"person": {"names": [{"displayName": "John Smith"}],
                            "emailAddresses": [{"value": "JS@corp.com"}],
                            "phoneNumbers": [{"value": "+1 415 555 0100"}]}},
                {"person": {"names": [{"displayName": "Johnny Smithe"}],
                            "emailAddresses": [{"value": "other@x.com"}]}},
            ]}
        return None

    monkeypatch.setattr(enrich, "gws_json", fake_gws_json)
    found, _ = enrich.harvest_gws_contacts("John Smith")
    values = {f["value"] for f in found}
    assert "js@corp.com" in values
    assert "other@x.com" not in values  # non-matching display name filtered


def test_gws_absent_never_raises(monkeypatch):
    monkeypatch.setattr(enrich, "gws_json", lambda *a, **k: None)
    found, notes = enrich.harvest_gws_contacts("John Smith")
    assert found == []
    assert notes  # degraded, documented


def test_dorks_args_built_from_enriched_seed():
    seed = {"name": "John Smith", "phone": "+1 415 555 0100", "email": "j@x.co"}
    args = ["--name", seed["name"]]
    args += ["--phone", seed["phone"]]
    args += ["--email", seed["email"]]
    assert args[0] == "--name" and "--phone" in args and "--email" in args
