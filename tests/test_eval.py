"""Eval harness tests. Fake data only; no network, no real engines."""
import csv
import json
import math
import sys
from pathlib import Path

import pytest

EVAL = Path(__file__).resolve().parent.parent / "eval"
sys.path.insert(0, str(EVAL))

import metrics  # noqa: E402
import run_bench  # noqa: E402


# ---------------------------------------------------------------- metrics

def test_dcg_first_rank_discount_is_one():
    # rank 1: 1/log2(0+2) = 1.0; rank 2: 1/log2(3) ~ 0.6309
    assert metrics.dcg([1.0]) == pytest.approx(1.0)
    assert metrics.dcg([1.0, 1.0]) == pytest.approx(1.0 + 1 / math.log2(3))


def test_dcg_respects_k():
    assert metrics.dcg([1.0] * 20, k=10) == metrics.dcg([1.0] * 10, k=10)


def test_padded_ndcg_empty_is_zero():
    assert metrics.padded_ndcg([]) == 0.0


def test_padded_ndcg_perfect_short_list_below_perfect_full():
    # the padded ideal assumes k perfect results exist: 3/3 perfect < 10/10
    assert metrics.padded_ndcg([1, 1, 1], k=10) < metrics.padded_ndcg([1] * 10, k=10)


def test_padded_ndcg_perfect_full_list_is_one():
    assert metrics.padded_ndcg([1.0] * 10, k=10) == pytest.approx(1.0)


def test_padded_ndcg_single_perfect_below_two_perfect():
    # 1/log2(2+k) discount grows with rank: 1 perfect < 2 perfect
    assert metrics.padded_ndcg([1.0], k=10) < metrics.padded_ndcg([1.0, 1.0], k=10)


def test_padded_ndcg_monotonic_in_more_good_results():
    scores = [metrics.padded_ndcg([1] * n, k=10) for n in range(11)]
    assert all(a < b for a, b in zip(scores, scores[1:]))


def test_padded_ndcg_bad_result_lower():
    # a zero at rank 1 hurts less than one at rank 1 (higher discount) —
    # swapping a perfect to the front must raise the score
    assert metrics.padded_ndcg([0, 1, 1], k=10) < metrics.padded_ndcg([1, 0, 1], k=10)


def test_precision_at_k():
    assert metrics.precision_at_k([0.9, 0.1, 0.5]) == pytest.approx(2 / 3)  # 0.5 counts
    assert metrics.precision_at_k([]) == 0.0
    assert metrics.precision_at_k([0.9] * 15, k=10) == pytest.approx(1.0)  # k cap
    assert metrics.precision_at_k([0.9, 0.1], k=10) == pytest.approx(0.5)  # len denominator


def test_coverage_score():
    assert metrics.coverage_score(5, 10) == pytest.approx(0.5)
    assert metrics.coverage_score(15, 10) == 1.0          # capped at 1
    assert metrics.coverage_score(0, 10) == 0.0
    assert metrics.coverage_score(3, 0) == 0.0            # bad k: no crash, 0


def test_aggregate_zero_pads_missing_queries():
    rows = [{"query_id": "q1", "grades": [1.0] * 10, "requested_k": 10},
            {"query_id": "q2", "grades": [], "requested_k": 10}]  # failed -> 0s
    a = metrics.aggregate(rows, total_queries=4)          # q3, q4 missing
    assert a["n_queries"] == 2
    assert a["total_queries"] == 4
    # nDCG sum is 1.0 (only q1) spread over 4 queries
    assert a["relevance_precision"] == pytest.approx(0.25)
    assert a["mean_coverage"] == pytest.approx(0.25)
    assert a["tcr"] == pytest.approx(0.25)                # 1 of 4 completed
    assert a["effective_coverage"] == pytest.approx(0.25 * 0.25)
    assert a["overall"] == pytest.approx((0.25 + 0.0625) / 2)


def test_aggregate_effective_coverage_tcr_times_mean():
    rows = ([{"query_id": f"q{i}", "grades": [1.0] * 10, "requested_k": 10}
             for i in range(3)] +
            [{"query_id": "f", "grades": [], "requested_k": 10}])
    a = metrics.aggregate(rows)
    assert a["tcr"] == pytest.approx(0.75)
    assert a["mean_coverage"] == pytest.approx(0.75)
    assert a["effective_coverage"] == pytest.approx(0.75 * 0.75)


def test_aggregate_num_qualified_override():
    rows = [{"query_id": "q", "grades": [0.1, 0.1],
             "requested_k": 10, "num_qualified": 5}]
    a = metrics.aggregate(rows)
    assert a["mean_coverage"] == pytest.approx(0.5)
    assert a["tcr"] == pytest.approx(1.0)


def test_aggregate_empty():
    a = metrics.aggregate([])
    assert a["relevance_precision"] == 0.0
    assert a["overall"] == 0.0


def test_consistency_score():
    assert metrics.consistency_score([0.6, 0.6, 0.6]) == pytest.approx(100.0)
    assert metrics.consistency_score([]) == 0.0
    assert metrics.consistency_score([0.0, 0.0]) == 0.0    # mean 0 guard
    # wider spread -> lower score; never negative
    tight = metrics.consistency_score([0.60, 0.55])
    wide = metrics.consistency_score([0.90, 0.10])
    assert wide < tight
    assert metrics.consistency_score([1.0, 0.0]) >= 0.0


# ---------------------------------------------------------------- run_bench

def _evidence_row(query, status="ok", title="Jane Doe - LinkedIn",
                  url="https://linkedin.com/in/janedoe", snippet=None):
    r = {"query": query, "engine": "exa", "status": status,
         "title": title, "url": url}
    if snippet:
        r["snippet"] = snippet
    return r


def test_normalize_ok_rows_with_url(tmp_path):
    f = tmp_path / "ev.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in [
        _evidence_row("q1"),                                         # ok + url
        _evidence_row("q1", status="engine_error"),                  # not ok
        _evidence_row("q1", title=None, url=None),                   # ok, no url
        _evidence_row("q1", title="Jane Doe - LinkedIn",
                      url="https://x.com/j", snippet="VP Eng at X"),  # with snippet
    ]) + "\n", encoding="utf-8")
    by_qid = run_bench.map_rows_to_queries(
        run_bench.load_evidence_rows(f),
        [{"query_id": "q1", "prompt": "q1", "category": "c"}])
    rows = run_bench.to_submission_rows(by_qid)
    assert [r["query_id"] for r in rows] == ["q1", "q1"]
    assert all(r["agent_name"] == "people-osint" for r in rows)
    assert rows[0]["person_data"].startswith("Jane Doe | https://linkedin")
    assert "VP Eng at X" in rows[1]["person_data"]
    assert "engine_error" not in rows[0]["person_data"]


def test_name_heuristic_two_plus_tokens():
    f = run_bench.person_name_from_title
    assert f("Jane Doe - LinkedIn") == "Jane Doe"
    assert f("Jane Doe | Acme Corp") == "Jane Doe"
    assert f("Jane Doe, VP Engineering") == "Jane Doe"
    # 2+ space-separated tokens required
    assert f("Jane") is None
    assert f("LinkedIn") is None
    assert f("Acme Corp Homepage - About") == "Acme Corp Homepage"
    assert f("  Jane  Doe  ") == "Jane Doe"          # head split, stripped


def test_name_heuristic_title_pass_through(tmp_path):
    # not name-ish -> row still emitted, title as person_data lead
    by_qid = {"q1": [_evidence_row("q1", title="Homepage",
                                   url="https://example.com")]}
    rows = run_bench.to_submission_rows(by_qid)
    assert len(rows) == 1
    assert rows[0]["person_data"].startswith("Homepage | https://example.com")


def test_submission_csv_writer(tmp_path):
    by_qid = {"q2": [_evidence_row("q2")], "q1": [_evidence_row("q1")]}
    rows = run_bench.to_submission_rows(by_qid)
    p = run_bench.write_submission_csv(tmp_path / "sub" / "mission.csv", rows)
    with open(p, encoding="utf-8", newline="") as fh:
        r = list(csv.reader(fh))
    assert r[0] == ["query_id", "agent_name", "person_data"]
    assert [row[0] for row in r[1:]] == ["q1", "q2"]  # sorted qids
    assert all(row[1] == "people-osint" for row in r[1:])


def test_max_15_results_per_query():
    by_qid = {"q1": [_evidence_row("q1", url=f"https://x.com/{i}")
                     for i in range(20)]}
    rows = run_bench.to_submission_rows(by_qid)
    assert len(rows) == 15
    assert rows[0]["person_data"].endswith("https://x.com/0")
    assert rows[14]["person_data"].endswith("https://x.com/14")


def test_select_queries_deterministic_and_filters(tmp_path):
    qs = [{"query_id": "b2b_0002", "prompt": "p2", "category": "b2b"},
          {"query_id": "b2b_0001", "prompt": "p1", "category": "b2b"},
          {"query_id": "rec_0001", "prompt": "p3", "category": "recruiting"}]
    assert [q["query_id"] for q in run_bench.select_queries(qs)] == \
        ["b2b_0001", "b2b_0002", "rec_0001"]                     # sorted
    assert [q["query_id"] for q in run_bench.select_queries(
        qs, category="b2b")] == ["b2b_0001", "b2b_0002"]
    assert [q["query_id"] for q in run_bench.select_queries(
        qs, limit=2)] == ["b2b_0001", "b2b_0002"]
    assert [q["query_id"] for q in run_bench.select_queries(
        qs, ids=["rec_0001", "b2b_0001"])] == ["b2b_0001", "rec_0001"]
    with pytest.raises(ValueError, match="nope_9999"):
        run_bench.select_queries(qs, ids=["nope_9999"])


def test_load_queries_parses_bench_schema(tmp_path):
    f = tmp_path / "b2b.jsonl"
    f.write_text(json.dumps({"query_id": "b2b_0001", "prompt": "Find leads",
                             "category": "b2b", "source_id": "b2b_0001"})
                 + "\n\n" +
                 json.dumps({"query_id": "b2b_0002", "prompt": "More",
                             "category": "b2b", "source_id": "b2b_0002"})
                 + "\n", encoding="utf-8")
    qs = run_bench.load_queries(f)
    assert len(qs) == 2 and qs[0]["query_id"] == "b2b_0001"
    f.write_text('{"query_id": "x"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="missing 'prompt'"):
        run_bench.load_queries(f)


def test_map_rows_duplicate_prompts_share_one_batch():
    # duplicate prompts are deduplicated before the sweep; every query_id
    # with that prompt shares the single batch the sweep produced
    queries = [{"query_id": "a", "prompt": "same", "category": "c"},
               {"query_id": "b", "prompt": "same", "category": "c"},
               {"query_id": "c", "prompt": "other", "category": "c"}]
    rows = ([_evidence_row("same", url="https://1")] +
            [_evidence_row("same", url="https://2")] * 2 +
            [_evidence_row("unselected", url="https://9")])
    by_qid = run_bench.map_rows_to_queries(rows, queries)
    assert [r["url"] for r in by_qid["a"]] == ["https://1", "https://2", "https://2"]
    assert [r["url"] for r in by_qid["b"]] == ["https://1", "https://2", "https://2"]
    assert by_qid["c"] == []  # its prompt never ran / produced nothing


def test_map_rows_failed_query_absent_then_zero_padded():
    queries = [{"query_id": "a", "prompt": "p1", "category": "c"},
               {"query_id": "b", "prompt": "p2", "category": "c"}]
    by_qid = run_bench.map_rows_to_queries([_evidence_row("p1")], queries)
    assert len(by_qid["a"]) == 1
    assert by_qid["b"] == []  # engine_error / nothing written -> no rows


def test_build_sweep_cmd_uses_repeatable_queries():
    cmd = run_bench.build_sweep_cmd("/sc", ["q one", "q two"], "/o.jsonl", 2.0)
    assert cmd[0] == sys.executable
    assert Path(cmd[1]) == Path("/sc/sweep.py")  # OS-native separators
    assert cmd[cmd.index("--query"):] == ["--query", "q one", "--query", "q two"]
    assert "--max 2".split() == cmd[cmd.index("--max"):cmd.index("--max") + 2]


def test_run_bench_end_to_end_with_fake_runner(tmp_path, monkeypatch):
    # the sweep invocation is one function -> monkeypatch it; no network
    def fake_run_sweep(scripts_dir, prompts, out_path, pace):
        assert pace >= run_bench.MIN_PACE
        rows = []
        for i, p in enumerate(prompts):
            if p == "boom":
                rows.append({"query": p, "engine": "exa",
                             "status": "engine_error",
                             "error": "mcporter exit 1"})
            else:
                rows.append(_evidence_row(
                    p, title="Jane Doe - LinkedIn",
                    url=f"https://linkedin.com/in/jane{i}",
                    snippet="VP Engineering at Acme"))
        out_path.write_text(
            "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        return 0

    monkeypatch.setattr(run_bench, "run_sweep", fake_run_sweep)
    monkeypatch.setattr(run_bench, "load_bench_queries", lambda d: [
        {"query_id": "q1", "prompt": "find jane", "category": "c"},
        {"query_id": "q2", "prompt": "boom", "category": "c"},
        {"query_id": "q3", "prompt": "find bob", "category": "c"},
    ])
    export = tmp_path / "export.csv"
    rc = run_bench.main(["--data-dir", str(tmp_path / "d"),
                         "--runs-dir", str(tmp_path / "runs"),
                         "--pace", "0",          # clamped to MIN_PACE
                         "--export-submission", str(export)])
    assert rc == 0
    with open(export, encoding="utf-8", newline="") as fh:
        r = list(csv.reader(fh))
    assert r[0] == ["query_id", "agent_name", "person_data"]
    assert [row[0] for row in r[1:]] == ["q1", "q3"]  # q2 failed, no rows
    assert "engine_error" not in "".join(row[2] for row in r[1:])
    assert "VP Engineering at Acme" in r[1][2]


def test_run_bench_all_failed_exits_3(tmp_path, monkeypatch):
    def fake_run_sweep(scripts_dir, prompts, out_path, pace):
        out_path.write_text(json.dumps({"query": prompts[0], "engine": "exa",
                                        "status": "engine_error",
                                        "error": "x"}) + "\n",
                            encoding="utf-8")
        return 3

    monkeypatch.setattr(run_bench, "run_sweep", fake_run_sweep)
    monkeypatch.setattr(run_bench, "load_bench_queries", lambda d: [
        {"query_id": "q1", "prompt": "p", "category": "c"}])
    rc = run_bench.main(["--data-dir", str(tmp_path), "--runs-dir", str(tmp_path)])
    assert rc == run_bench.EXIT_ENGINE_ERROR
