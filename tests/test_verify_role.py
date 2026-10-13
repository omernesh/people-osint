"""Regression tests for dorks.py variant spellings and verify_role.py —
the Amir Pascal / Irena Portnik lessons as executable specs."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

from dorks import plan, last_name_variants
from verify_role import extract_titles, grade


# ---------- transliteration variants ----------

def test_paskal_variant_present():
    vs = last_name_variants("Paskal")
    assert "Pascal" in vs, vs


def test_variants_capped_and_deterministic():
    a, b = last_name_variants("Greenberg"), last_name_variants("Greenberg")
    assert a == b and len(a) <= 6


def test_plan_includes_variant_dorks():
    p = plan(name="Amir Paskal")
    goals = " | ".join(q["goal"] for q in p["queries"])
    assert "variant spelling (Paskal->Pascal)" in goals
    assert any('"Amir Pascal" site:linkedin.com' == q["query"] for q in p["queries"])


def test_plan_dedupes_variant_queries():
    p = plan(name="Amir Pascal")  # variant would regenerate "Pascal" -> skip
    qs = [q["query"] for q in p["queries"]]
    assert len(qs) == len(set(qs))


# ---------- verify_role: the trick-question scenario ----------

LINKEDIN_CACHE = (
    "Amir Pascal ICC Development Group manager CRM & Integration ... "
    "### Dev group manager - cal (Current) Apr 2017 - Present (9 years and 4 "
    "months) in Givatayim ... ### Crm & integration Dev group manager - Yael "
    "Group Jan 2013 - Dec 2016"
)

COMMIT_POST = (
    "𝗕𝗮𝗻𝗸 𝗼𝗳 𝗝𝗲𝗿𝘂𝘀𝗮𝗹𝗲𝗺 𝘀𝘂𝗰𝗰𝗲𝘀𝘀𝗳𝘂𝗹𝗹𝘆 𝗹𝗮𝘂𝗻𝗰𝗵𝗲𝘀 𝗶𝘁𝘀 𝗔𝗪𝗦 𝗟𝗮𝗻𝗱𝗶𝗻𝗴 𝗭𝗼𝗻𝗲 ... "
    "Irena Portnik, Deputy CEO, CTO Data, Digital & Innovation ... "
    "Amir Pascal Gil Tabakman Gilad Hadar ... 2025"
)

YAHOO_ROW = (
    "| Ms. Irena Portnik | Deputy CEO, EVP, Head of Resources & Technological "
    "Innovation Div. and Member of Management Board | 2.46M | -- | 1961 |"
)


def test_extract_titles_linkedin():
    ts = extract_titles(LINKEDIN_CACHE)
    assert any(t["org"].lower().startswith("cal") and t["current"] for t in ts)


def test_extract_titles_stock_row():
    ts = extract_titles(YAHOO_ROW)
    assert any("Deputy CEO" in t["title"] for t in ts)


def test_grade_stale_vs_confirmed():
    # card says CAL; LinkedIn cache shows CAL current since 2017 but a 2025
    # post names him at Bank of Jerusalem — BoJ outranks the lagging badge
    ev = [{"url": "u1", "snippet": COMMIT_POST.replace("Amir", "Amir")} |
          {"title": "Commit post 2025"}]
    # BoJ title row style
    ev.append({"url": "u2", "snippet": "Deputy CEO at Bank of Jerusalem ... 2025 Present"})
    g = grade("cal", ev)
    assert g["grade"] in ("stale", "confirmed")  # must not be unknown


def test_grade_confirmed_when_recent():
    ev = [{"url": "u", "snippet": "CISO at CAL ... 2024 - Present"}]
    g = grade("cal", ev)
    assert g["grade"] == "confirmed", g


def test_grade_stale_when_only_past():
    ev = [{"url": "u", "snippet": "Manager at Visa CAL 2005 - 2010"}]
    g = grade("visa cal", ev)
    assert g["grade"] == "stale", g


def test_grade_unknown_without_evidence():
    g = grade("cal", [{"url": "u", "snippet": "nothing about roles"}])
    assert g["grade"] == "unknown", g
