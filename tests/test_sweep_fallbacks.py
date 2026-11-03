"""Tavily/Firecrawl fallbacks in sweep.py. Fake mcporter + fake HTTP; no network."""
import io
import json
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import sweep  # noqa: E402

TKEY = "tvly-FAKEKEY-0123456789abcdef"
FKEY = "fc-FAKEKEY-fedcba9876543210"
CREDITS = "web_search_exa error (402): You have exceeded your credits limit."
GOOD = "Title: Exa Page\nURL: https://example.com/exa\nText: hello"
TAV_OK = json.dumps({"results": [{"title": "T", "url": "https://example.org/t",
                                  "content": "tav snippet", "score": 0.9}]})
FC_OK = json.dumps({"success": True, "data": {"web": [
    {"title": "F", "url": "https://example.net/f", "description": "fc snippet"}]}})


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", TKEY)
    monkeypatch.setenv("FIRECRAWL_API_KEY", FKEY)
    monkeypatch.setattr(sweep.time, "sleep", lambda s: None)
    monkeypatch.setattr(sweep, "mcporter_argv", lambda: ["mcporter"])


class Fake:
    """Scripted engines: exa outputs (mcporter stdout), http answers by host."""

    def __init__(self, monkeypatch, exa=(CREDITS,), tavily=((200, TAV_OK),),
                 firecrawl=((200, FC_OK),)):
        self.calls = {"exa": 0, "tavily": 0, "firecrawl": 0}
        self.exa, self.tav, self.fc = exa, tavily, firecrawl
        monkeypatch.setattr(sweep, "run", self.run)
        monkeypatch.setattr(sweep, "http_post_json", self.http)

    def pick(self, seq, name):
        self.calls[name] += 1
        return seq[min(self.calls[name] - 1, len(seq) - 1)]

    def run(self, cmd, timeout=60):
        return 0, self.pick(self.exa, "exa")

    def http(self, url, key, body, timeout=30):
        if "tavily" in url:
            assert key == TKEY
            return self.pick(self.tav, "tavily")
        assert key == FKEY
        return self.pick(self.fc, "firecrawl")


def sweep_main(monkeypatch, tmp_path, queries):
    out = tmp_path / "ev.jsonl"
    argv = ["sweep.py", "--out", str(out)]
    for q in queries:
        argv += ["--query", q]
    monkeypatch.setattr(sys, "argv", argv)
    code = 0
    try:
        sweep.main()
    except SystemExit as e:
        code = e.code
    rows = ([json.loads(l) for l in out.read_text(encoding="utf-8").splitlines()]
            if out.exists() else [])
    return code, rows


def test_exa_402_then_tavily_ok_and_exa_memoized(monkeypatch, tmp_path):
    f = Fake(monkeypatch)
    code, rows = sweep_main(monkeypatch, tmp_path, ["a", "b", "c"])
    assert code == 0
    assert f.calls == {"exa": 1, "tavily": 3, "firecrawl": 0}
    assert [r["engine"] for r in rows] == ["tavily"] * 3
    assert rows[0] == {"query": "a", "engine": "tavily", "status": "ok",
                       "title": "T", "url": "https://example.org/t",
                       "snippet": "tav snippet"}


def test_tavily_fail_then_firecrawl_ok(monkeypatch, tmp_path):
    f = Fake(monkeypatch, exa=(GOOD, CREDITS), tavily=((500, ""),))
    code, rows = sweep_main(monkeypatch, tmp_path, ["a", "b"])
    assert code == 0
    assert [r["engine"] for r in rows] == ["exa", "firecrawl"]
    assert rows[1]["url"] == "https://example.net/f"
    assert rows[1]["snippet"] == "fc snippet"
    assert f.calls["tavily"] == 1


def test_all_fail_exit_3(monkeypatch, tmp_path):
    Fake(monkeypatch, tavily=((500, ""),), firecrawl=((None, "URLError"),))
    code, rows = sweep_main(monkeypatch, tmp_path, ["a", "b"])
    assert code == sweep.EXIT_ENGINE_ERROR
    assert rows and all(r["status"] == "engine_error" for r in rows)
    assert "tavily http 500" in rows[0]["error"]
    assert "firecrawl request failed" in rows[0]["error"]


def test_all_engines_run_out_midsweep_names_each(monkeypatch, tmp_path, capsys):
    Fake(monkeypatch, exa=(GOOD, CREDITS),
         tavily=((200, TAV_OK), (432, "")), firecrawl=((402, ""),))
    code, rows = sweep_main(monkeypatch, tmp_path, ["a", "b", "c", "d"])
    assert code == sweep.EXIT_ENGINE_ERROR
    assert [r["status"] for r in rows] == ["ok", "ok", "engine_error"]
    err = capsys.readouterr().err
    assert "all engines unavailable" in err
    for needle in ("exa: credits exhausted", "tavily: credits exhausted",
                   "firecrawl: credits exhausted"):
        assert needle in err


def test_auth_error_is_memoized(monkeypatch, tmp_path):
    f = Fake(monkeypatch, tavily=((401, ""),))
    code, rows = sweep_main(monkeypatch, tmp_path, ["a", "b", "c"])
    assert code == 0
    assert f.calls["tavily"] == 1 and f.calls["firecrawl"] == 3
    assert [r["engine"] for r in rows] == ["firecrawl"] * 3


def test_429_is_not_memoized(monkeypatch, tmp_path):
    Fake(monkeypatch, tavily=((429, ""), (200, TAV_OK)))
    code, rows = sweep_main(monkeypatch, tmp_path, ["a", "b"])
    assert [r["engine"] for r in rows] == ["firecrawl", "tavily"]


def test_missing_key_skips_engine_with_note(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("TAVILY_API_KEY")
    f = Fake(monkeypatch)
    code, rows = sweep_main(monkeypatch, tmp_path, ["a"])
    assert code == 0 and rows[0]["engine"] == "firecrawl"
    assert f.calls["tavily"] == 0
    err = capsys.readouterr().err
    assert err.count("note: tavily skipped, TAVILY_API_KEY not set") == 1


def test_empty_result_is_not_a_failure(monkeypatch, tmp_path):
    f = Fake(monkeypatch, tavily=((200, json.dumps({"results": []})),))
    code, rows = sweep_main(monkeypatch, tmp_path, ["a"])
    assert code == 0 and f.calls["firecrawl"] == 0
    assert rows == [{"query": "a", "engine": "tavily", "status": "ok",
                     "note": "no results"}]


def test_pace_between_attempts(monkeypatch, tmp_path):
    sleeps = []
    Fake(monkeypatch)
    monkeypatch.setattr(sweep.time, "sleep", sleeps.append)
    monkeypatch.setattr(sys, "argv", ["sweep.py", "--out", str(tmp_path / "e"),
                                      "--query", "a", "--query", "b",
                                      "--pace", "1.5"])
    sweep.main()
    assert sleeps and set(sleeps) == {1.5}


# ---- secrets never leak -------------------------------------------------

@pytest.mark.parametrize("which", ["httperror", "urlerror", "oserror"])
def test_keys_never_in_stdout_stderr_or_evidence(monkeypatch, tmp_path, capsys, which):
    leaky = "boom Authorization: Bearer %s and %s" % (TKEY, FKEY)
    if which == "httperror":
        exc = urllib.error.HTTPError("https://api.tavily.com/search?k=" + TKEY,
                                     500, leaky, {}, io.BytesIO(leaky.encode()))
    elif which == "urlerror":
        exc = urllib.error.URLError(leaky)
    else:
        exc = OSError(leaky)

    def boom(req, timeout=None):
        raise exc

    monkeypatch.setattr(sweep.urllib.request, "urlopen", boom)
    monkeypatch.setattr(sweep, "run", lambda cmd, timeout=60: (0, CREDITS))
    code, rows = sweep_main(monkeypatch, tmp_path, ["a", "b"])
    cap = capsys.readouterr()
    ev = (tmp_path / "ev.jsonl").read_text(encoding="utf-8")
    for blob in (cap.out, cap.err, ev):
        assert TKEY not in blob and FKEY not in blob
    assert code == sweep.EXIT_ENGINE_ERROR


def test_key_only_in_header_not_url_or_body(monkeypatch):
    seen = {}

    class R:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return TAV_OK.encode()

    def fake_open(req, timeout=None):
        seen["req"] = req
        return R()

    monkeypatch.setattr(sweep.urllib.request, "urlopen", fake_open)
    ok, rows = sweep.tavily_search("q")
    r = seen["req"]
    assert ok and TKEY not in r.full_url and TKEY not in r.data.decode()
    assert r.get_header("Authorization") == "Bearer " + TKEY
