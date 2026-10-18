"""Regression tests for the v3.1 review fixes. Synthetic data only."""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "eval"))

import cluster  # noqa: E402
import criteria_verify  # noqa: E402
import enrich  # noqa: E402
import metrics  # noqa: E402
import report_score  # noqa: E402


# ---------------------------------------------------------------- enrich

def test_plausible_phone_keeps_us_area_codes_starting_20x():
    # area codes 201-209 used to be rejected as "years"
    assert enrich.plausible_phone("2025550100")
    assert enrich.plausible_phone("(202) 555-0100")
    assert not enrich.plausible_phone("2019 2020 2021")


def test_phone_regex_does_not_truncate_long_formatted_numbers():
    m = enrich.PHONE_RE.findall("call +44 (0) 20 7946 0958 today")
    assert m and enrich.digits(m[0]) == "440207946" "0958"


def test_phone_regex_ignores_digits_inside_longer_runs():
    assert enrich.PHONE_RE.findall("order id 98765054123456789") == []


def test_gws_contacts_skips_nameless_rows(monkeypatch):
    def fake(args, timeout=30):
        if "searchContacts" in args:
            return {"results": [{"person": {"emailAddresses": [{"value": "x@example.com"}]}}]}
        return None
    monkeypatch.setattr(enrich, "gws_json", fake)
    found, _ = enrich.harvest_gws_contacts("Jane Doe")
    assert found == []


def test_gws_mail_takes_only_the_matching_mailbox(monkeypatch):
    def fake(args, timeout=30):
        if "list" in args:
            q = json.loads(args[-1])["q"]
            assert q == 'from:"Jane Doe" OR to:"Jane Doe"'
            return {"messages": [{"id": "m1"}]}
        return {"payload": {"headers": [
            {"name": "From", "value": "Jane Doe <jane@example.com>"},
            {"name": "To", "value": "Mailbox Owner <owner@example.com>, "
                                    "Other Person <other@example.com>"},
            {"name": "Subject", "value": "re: jane@example.org"},
        ]}}
    monkeypatch.setattr(enrich, "gws_json", fake)
    found, _ = enrich.harvest_gws_mail("Jane Doe")
    assert [f["value"] for f in found] == ["jane@example.com"]


def test_gws_error_body_is_no_response(monkeypatch):
    class P:
        stdout = '{"error": {"code": 401, "message": "auth"}}'
    monkeypatch.setattr(enrich.subprocess, "run", lambda *a, **k: P())
    assert enrich.gws_json(["people"]) is None


def test_merge_prefers_trusted_source_over_argument_order():
    seed = {"name": "Jane Doe"}
    found = [
        {"field": "email", "value": "web@example.com", "origin": "web:example.com", "detail": ""},
        {"field": "email", "value": "card@example.com", "origin": "gws:contacts", "detail": ""},
    ]
    alts = []
    merged, added = enrich.merge(seed, found, alts)
    assert merged["email"] == "card@example.com"
    assert [a["value"] for a in alts] == ["web@example.com"]


def test_merge_seed_value_suppresses_equivalent_found_value():
    seed = {"name": "Jane Doe", "phone": "+972 50 000 0000"}
    found = [{"field": "phone", "value": "050-000-0000", "origin": "internal", "detail": ""}]
    alts = []
    enrich.merge(seed, found, alts)
    assert alts == []  # same number, local format — not an alternate


def test_mine_evidence_reads_json_array_and_u2028(tmp_path):
    ev = tmp_path / "e.json"
    ev.write_text(json.dumps([{"url": "https://example.com/a",
                               "snippet": "mail jane@example.com"}]), encoding="utf-8")
    assert [f["value"] for f in enrich.mine_evidence(ev)[0]] == ["jane@example.com"]
    ev2 = tmp_path / "e.jsonl"
    ev2.write_text(json.dumps({"url": "https://example.com/b",
                               "snippet": "a b jane@example.com"},
                              ensure_ascii=False) + "\n", encoding="utf-8")
    assert [f["value"] for f in enrich.mine_evidence(ev2)[0]] == ["jane@example.com"]


# ---------------------------------------------------------------- cluster

def test_seed_match_is_word_bounded():
    rows = [{"url": "https://example.com/1", "title": "Jordan Cohen joins Acme"}]
    out = cluster.cluster_rows(rows, seed_name="Dan Cohen")
    assert [c["key"] for c in out["clusters"]] == ["jordan cohen"]


def test_seed_match_is_accent_insensitive():
    rows = [{"url": "https://example.com/1", "title": "Profile of José Müller, engineer"}]
    out = cluster.cluster_rows(rows, seed_name="Jose Muller")
    assert out["clusters"][0]["display"] == "Jose Muller"


def test_extract_names_handles_mc_prefix_and_accents():
    names = cluster.extract_names("Talk by Jane McDonald and Łukasz Nowak-Kowalski")
    keys = {cluster.norm_key(n) for n in names}
    assert "jane mcdonald" in keys
    assert "lukasz nowak kowalski" in keys


def test_cluster_size_counts_distinct_urls():
    rows = [{"url": "https://example.com/1", "title": "Jane Doe"},
            {"url": "https://example.com/1", "title": "Jane Doe"}]
    out = cluster.cluster_rows(rows)
    assert out["clusters"][0]["size"] == 1


def test_co_mentioned_names_create_no_empty_clusters():
    rows = [{"url": "https://example.com/1", "title": "Jane Doe with John Roe"}]
    out = cluster.cluster_rows(rows)
    assert [c["key"] for c in out["clusters"]] == ["jane doe"]


# ---------------------------------------------------------------- criteria

def test_phone_criterion_carries_the_digits():
    crit = criteria_verify.build_criteria({"name": "Jane Doe", "phone": "+1 415 555 0100"})
    phone = next(c for c in crit if c["id"] == "c_phone")
    assert "5550100" in phone["description"]


def test_insufficient_counts_as_zero_not_excluded(monkeypatch):
    answers = iter(["met", "insufficient", "insufficient"])
    monkeypatch.setattr(criteria_verify, "classify_retry",
                        lambda **k: (next(answers), 0.9))
    monkeypatch.setattr(criteria_verify, "PACE", 0)
    crit = [{"id": f"c{i}", "description": "d"} for i in range(3)]
    _, rel = criteria_verify.grade_cluster([], crit)
    assert rel == pytest.approx(1 / 3, abs=1e-3)


def test_engine_error_is_excluded_from_relevance(monkeypatch):
    answers = iter([("met", 0.9), (None, 0.0)])
    monkeypatch.setattr(criteria_verify, "classify_retry", lambda **k: next(answers))
    monkeypatch.setattr(criteria_verify, "PACE", 0)
    crit = [{"id": f"c{i}", "description": "d"} for i in range(2)]
    _, rel = criteria_verify.grade_cluster([], crit)
    assert rel == 1.0


# ---------------------------------------------------------------- metrics

def test_relevance_precision_is_ndcg_at_10_regardless_of_requested_k():
    # bench: relevance_precision = mean nDCG@10; requested_k only sets the
    # coverage denominator
    rows = [{"query_id": "q", "grades": [1.0] * 10, "requested_k": 15}]
    a = metrics.aggregate(rows)
    assert a["relevance_precision"] == pytest.approx(1.0)
    assert a["mean_coverage"] == pytest.approx(10 / 15)


# ---------------------------------------------------------------- report_score

def test_string_candidate_verdict_does_not_erase_scaffold_verdict():
    report = {"seed": {"name": "x"},
              "verdict": {"identity": "match", "confidence": 0.8, "basis": ["b"]},
              "evidence": [{"url": "u", "signal": "s", "source": "d"}],
              "candidates": [{"rank": 1, "verdict": "match", "confidence": 0.82}]}
    r = report_score.score_document(report)
    assert "verdict.identity" in r["present"]
    assert "verdict.basis" in r["present"]
    assert r["shape"] == "multi-candidate"
