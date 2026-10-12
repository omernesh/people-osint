"""Regression tests. Fake data only; no network, no real engines."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import dorks  # noqa: E402
import evidence  # noqa: E402
import jev_verify  # noqa: E402
import scan  # noqa: E402
import sweep  # noqa: E402


def _write(path, rows):
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


class _Resp:
    def __init__(self, choice, conf):
        self._b = json.dumps({"answers": {"q": {"choice": choice, "confidence": conf}}}).encode()

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


# 1. JSONL evidence crashes json.load
def test_load_evidence_jsonl_multi_row(tmp_path):
    f = tmp_path / "ev.jsonl"
    _write(f, [{"url": "https://example.com/a"}, {"url": "https://example.com/b"}])
    assert len(evidence.load_evidence(f)) == 2


def test_load_evidence_array_empty_and_bad_line(tmp_path):
    f = tmp_path / "a.json"
    f.write_text('[{"url": "https://example.com/a"}]')
    assert len(evidence.load_evidence(f)) == 1
    f.write_text("")
    assert evidence.load_evidence(f) == []
    f.write_text('{"url": "x"}\nnot json\n')
    with pytest.raises(ValueError, match=":2:"):
        evidence.load_evidence(f)


# 2. an outage must not look like `insufficient`
def test_sweep_exits_nonzero_when_all_queries_fail(tmp_path, monkeypatch):
    monkeypatch.setattr(sweep, "run", lambda cmd, timeout=60: (127, "mcporter missing"))
    out = tmp_path / "ev.jsonl"
    monkeypatch.setattr(sys, "argv", ["sweep", "--query", "Jane Doe", "--query", "x",
                                      "--out", str(out), "--pace", "0"])
    with pytest.raises(SystemExit) as e:
        sweep.main()
    assert e.value.code == sweep.EXIT_ENGINE_ERROR
    assert all(r["status"] == "engine_error" for r in evidence.load_evidence(out))


def test_sweep_ok_when_some_succeed(tmp_path, monkeypatch):
    calls = iter([(1, "boom"),
                  (0, "Title: Jane\nURL: https://example.com/j\nText: Jane Doe, engineer")])
    monkeypatch.setattr(sweep, "run", lambda cmd, timeout=60: next(calls))
    monkeypatch.setattr(sys, "argv", ["sweep", "--query", "a", "--query", "b",
                                      "--out", str(tmp_path / "e.jsonl"), "--pace", "0"])
    sweep.main()  # no SystemExit


def test_run_missing_binary_is_handled():
    code, _ = sweep.run(["definitely-not-a-real-binary-xyz"])
    assert code == 127


def test_scan_reports_engine_error_not_insufficient(tmp_path, capsys, monkeypatch):
    f = tmp_path / "ev.jsonl"
    _write(f, [{"query": "q", "status": "engine_error", "error": "x"}] * 3)
    monkeypatch.setattr(jev_verify, "verify_identity_retry",
                        lambda *a, **k: pytest.fail("must not call JEV"))
    assert scan.main(["--name", "Jane Doe", "--evidence-file", str(f)]) == 3
    assert "engine_error" in capsys.readouterr().out


# 3. provenance: query + snippet carried through
def test_snippet_parsed_and_provenance_in_jev_rows():
    rows = sweep.parse_exa_text("Title: Jane Doe - Eng\nURL: https://example.com/jane\n"
                                "Highlights: Jane Doe works at Example Corp\n"
                                "Title: Other\nURL: https://example.com/o")
    assert rows[0]["snippet"] == "Jane Doe works at Example Corp"
    assert "snippet" not in rows[1]
    out = evidence.to_jev_rows([{"query": '"Jane Doe" site:example.com',
                                 "engine": "exa", **rows[0]}])
    assert out[0]["source"].endswith('"Jane Doe" site:example.com')
    assert out[0]["signal"] == "Jane Doe works at Example Corp"


def test_verify_prompt_ranks_signal_first_and_dedupes(monkeypatch):
    sent = {}

    def fake_open(req, timeout=0):
        sent["body"] = json.loads(req.data)
        return _Resp("match", 0.9)

    monkeypatch.setattr(jev_verify.urllib.request, "urlopen", fake_open)
    monkeypatch.setattr(jev_verify, "_load_key", lambda: "test-key")
    ev = [{"url": "https://example.com/a", "source": "s", "signal": ""},
          {"url": "https://example.com/a", "source": "s", "signal": "dup"},
          {"url": "https://example.com/b", "source": "s", "signal": "same employer"}]
    jev_verify.verify_identity("Jane Doe", "+1-555-0100", ev)
    state = sent["body"]["state"]
    assert state.index("same employer") < state.index("example.com/a")
    assert state.count("example.com/a") == 1


def test_verify_retry(monkeypatch):
    n = {"i": 0}

    def flaky(*a, **k):
        n["i"] += 1
        if n["i"] < 3:
            raise OSError("down")
        return "match", 0.9

    monkeypatch.setattr(jev_verify, "verify_identity", flaky)
    monkeypatch.setattr(jev_verify.time, "sleep", lambda s: None)
    assert jev_verify.verify_identity_retry("Jane Doe", "", [])[0] == "match"


def test_low_confidence_match_downgraded(monkeypatch):
    monkeypatch.setattr(jev_verify.urllib.request, "urlopen",
                        lambda r, timeout=0: _Resp("match", 0.4))
    monkeypatch.setattr(jev_verify, "_load_key", lambda: "k")
    assert jev_verify.verify_identity("Jane Doe", "", [])[0] == "insufficient"


# 4. sherlock opt-in
def test_sherlock_off_by_default(tmp_path, monkeypatch):
    f = tmp_path / "ev.jsonl"
    _write(f, [{"query": "q", "url": "https://example.com/a", "title": "t"}])
    monkeypatch.setattr(scan, "sherlock_rows",
                        lambda h: pytest.fail("sherlock ran without --sherlock"))
    monkeypatch.setattr(jev_verify, "verify_identity_retry",
                        lambda *a, **k: ("insufficient", 0.2))
    assert scan.main(["--name", "Jane Doe", "--evidence-file", str(f),
                      "--handle", "janedoe"]) == 0


def test_sherlock_runs_when_opted_in(tmp_path, monkeypatch):
    f = tmp_path / "ev.jsonl"
    _write(f, [{"query": "q", "url": "https://example.com/a", "title": "t"}])
    seen = {}

    def fake(h):
        seen["h"] = h
        return []

    monkeypatch.setattr(scan, "sherlock_rows", fake)
    monkeypatch.setattr(jev_verify, "verify_identity_retry",
                        lambda *a, **k: ("insufficient", 0.2))
    scan.main(["--name", "Jane Doe", "--evidence-file", str(f),
               "--sherlock", "--handle", "janedoe"])
    assert seen["h"] == ["janedoe"]


# 5. planner: no hardcoded country, no wrong phone split
def test_no_hardcoded_region_query():
    qs = [q["query"] for q in dorks.plan(name="Jane Doe")["queries"]]
    assert not any("Israel" in q for q in qs)
    qs = [q["query"] for q in dorks.plan(name="Jane Doe", region="Canada")["queries"]]
    assert '"Jane Doe" Canada' in qs


def test_us_number_not_mangled():
    qs = [q["query"] for q in dorks.plan(phone="+1 555 010 0123")["queries"]]
    assert not any("155-" in q for q in qs)
    qs = [q["query"] for q in dorks.plan(phone="+1 555 010 0123", default_cc="1")["queries"]]
    assert '"555-010-0123"' in qs
    assert not any("155-" in q for q in qs)


def test_972_prefix_still_converted():
    qs = [q["query"] for q in dorks.plan(phone="+972 50 555 0100")["queries"]]
    assert '"0505550100"' in qs


# 6. social handle with query string
@pytest.mark.parametrize("url,handle", [
    ("https://example.com/jane.doe?utm=1", "jane.doe"),
    ("https://example.com/jane.doe/", "jane.doe"),
    ("https://example.com/profile.php?id=12345", "12345"),
    ("https://example.com/in/janedoe#about", "janedoe"),
])
def test_handle_from_url(url, handle):
    assert dorks.handle_from_url(url) == handle
    assert dorks.plan(social=url)["handles"] == [handle]


# 7. one threshold source
def test_validate_report_uses_shared_threshold(tmp_path):
    r = tmp_path / "r.json"
    r.write_text(json.dumps({"seed": {}, "verdict": "match", "confidence": 0.55,
                             "evidence": [{"source": "s", "signal": "x",
                                           "url": "https://example.com"}]}))
    out = subprocess.run([sys.executable, str(SCRIPTS / "validate_report.py"), str(r)],
                         capture_output=True, text=True)
    assert f"< {jev_verify.PROVISIONAL_MAX}" in out.stdout
