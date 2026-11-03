"""Exa error handling in sweep.py. Fake mcporter output only; no network."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import sweep  # noqa: E402

CREDITS = ("web_search_exa error (402): You have exceeded your credits limit. "
           "Please top up to keep using Exa at dashboard.exa.ai")
GOOD = "Title: Example Page\nURL: https://example.com/a\nText: hello"


def _fake(monkeypatch, outputs):
    calls = []

    def fake_run(cmd, timeout=60):
        calls.append(cmd)
        return 0, outputs[min(len(calls) - 1, len(outputs) - 1)]

    monkeypatch.setattr(sweep, "run", fake_run)
    monkeypatch.setattr(sweep, "mcporter_argv", lambda: ["mcporter"])
    monkeypatch.setattr(sweep.time, "sleep", lambda s: None)
    return calls


def test_402_is_named_credits_exhausted(monkeypatch):
    _fake(monkeypatch, [CREDITS])
    ok, rows = sweep.exa_search("q")
    assert not ok
    assert rows[0]["error"].startswith("credits exhausted: web_search_exa error (402)")
    assert "no Title/URL" not in rows[0]["error"]


def test_other_code_is_real_error_with_line(monkeypatch):
    _fake(monkeypatch, ["web_search_exa error (500): upstream boom"])
    ok, rows = sweep.exa_search("q")
    assert not ok
    assert rows[0]["error"] == "exa error 500: web_search_exa error (500): upstream boom"


def test_credits_text_without_402_code(monkeypatch):
    _fake(monkeypatch, ["web_search_exa error (429): exceeded your credits limit"])
    ok, rows = sweep.exa_search("q")
    assert rows[0]["error"].startswith("credits exhausted")


def test_sweep_stops_at_first_credits_error_and_exits_3(monkeypatch, tmp_path, capsys):
    calls = _fake(monkeypatch, [GOOD, CREDITS, GOOD, GOOD])
    out = tmp_path / "ev.jsonl"
    monkeypatch.setattr(sys, "argv", ["sweep.py", "--out", str(out),
                                      "--query", "a", "--query", "b",
                                      "--query", "c", "--query", "d"])
    with pytest.raises(SystemExit) as e:
        sweep.main()
    assert e.value.code == sweep.EXIT_ENGINE_ERROR
    assert len(calls) == 2  # c, d never ran
    rows = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines()]
    assert [r["status"] for r in rows] == ["ok", "engine_error"]
    assert rows[1]["error"].startswith("credits exhausted")
    assert "engine_error: credits exhausted" in capsys.readouterr().err
