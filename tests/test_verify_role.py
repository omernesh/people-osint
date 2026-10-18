"""Regression tests for dorks.py variant spellings and verify_role.py —
the stale-contact-card / transliteration lessons as executable specs.
All names, orgs and places here are synthetic."""
import sys, pathlib
from datetime import date

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

from dorks import plan, last_name_variants
from verify_role import extract_titles, grade, org_matches

THIS_YEAR = date.today().year


# ---------- transliteration variants ----------

def test_k_to_c_variant_present():
    vs = last_name_variants("Kaplan")
    assert "Caplan" in vs, vs


def test_variants_capped_and_deterministic():
    a, b = last_name_variants("Greenberg"), last_name_variants("Greenberg")
    assert a == b and len(a) <= 6
    assert "Greenburg" in a


def test_variants_preserve_capitalisation():
    for v in last_name_variants("Kaplan") + last_name_variants("Cohen"):
        assert v[0].isupper(), v


def test_variants_keep_ch_digraph():
    # "ch" is one sound — "Schwartz" -> "Skhwartz" is not a real spelling
    assert not any("kh" in v.lower() for v in last_name_variants("Schwartz"))


def test_plan_includes_variant_dorks():
    p = plan(name="Dana Kaplan")
    goals = " | ".join(q["goal"] for q in p["queries"])
    assert "variant spelling (Kaplan->Caplan)" in goals
    assert any('"Dana Caplan" site:linkedin.com' == q["query"] for q in p["queries"])


def test_plan_dedupes_variant_queries():
    p = plan(name="Dana Caplan")
    qs = [q["query"] for q in p["queries"]]
    assert len(qs) == len(set(qs))


# ---------- verify_role: the trick-question scenario ----------

LINKEDIN_CACHE = (
    "Dana Kaplan Example Development Group manager CRM & Integration ... "
    f"### Dev group manager - acmecard (Current) Apr {THIS_YEAR - 1} - Present "
    "(1 year and 4 months) in Springfield ... ### Crm & integration Dev group "
    "manager - Beta Group Jan 2013 - Dec 2016"
)

STYLED_POST = (
    "𝗘𝘅𝗮𝗺𝗽𝗹𝗲 𝗕𝗮𝗻𝗸 𝘀𝘂𝗰𝗰𝗲𝘀𝘀𝗳𝘂𝗹𝗹𝘆 𝗹𝗮𝘂𝗻𝗰𝗵𝗲𝘀 ... "
    f"Deputy CEO at 𝗘𝘅𝗮𝗺𝗽𝗹𝗲 𝗕𝗮𝗻𝗸 {THIS_YEAR} Present"
)

STOCK_ROW = (
    "| Ms. Jane Doe | Deputy CEO, EVP, Head of Resources & Technological "
    "Innovation Div. and Member of Management Board | 2.46M | -- | 1961 |"
)


def test_extract_titles_linkedin():
    ts = extract_titles(LINKEDIN_CACHE)
    cur = [t for t in ts if t["org"].lower().startswith("acmecard")]
    assert cur and cur[0]["current"] and cur[0]["year"] == THIS_YEAR - 1
    past = [t for t in ts if t["org"].lower().startswith("beta group")]
    assert past and not past[0]["current"] and past[0]["year"] == 2013


def test_extract_titles_stock_row():
    ts = extract_titles(STOCK_ROW)
    assert any("Deputy CEO" in t["title"] for t in ts)


def test_extract_titles_styled_unicode_is_folded():
    ts = extract_titles(STYLED_POST)
    assert any(t["org"].startswith("Example Bank") and t["current"] for t in ts), ts


def test_extract_titles_middot_header():
    text = f"### Staff Engineer · Acme Corp · Full-time\nJan {THIS_YEAR - 1} - Present"
    ts = extract_titles(text)
    assert any(t["org"] == "Acme Corp" and t["current"] for t in ts), ts


def test_neighbour_entry_dates_not_borrowed():
    # the first entry has no dates; it must NOT inherit the next one's
    text = f"Advisor at Acme Corp | Engineer at Beta Labs {THIS_YEAR} - Present"
    ts = {t["org"]: t for t in extract_titles(text)}
    assert ts["Acme Corp"]["current"] is False
    assert ts["Beta Labs"]["current"] is True


def test_grade_card_org_superseded_by_newer_org():
    # card says acmecard; a fresh post names the person at Example Bank
    ev = [{"url": "u1", "title": "post", "snippet": STYLED_POST}]
    g = grade("acmecard", ev)
    assert g["grade"] == "stale", g
    assert "DIFFERENT" in g["reason"]


def test_grade_confirmed_when_recent():
    ev = [{"url": "u", "snippet": f"CISO at Acme Card ... {THIS_YEAR - 1} - Present"}]
    g = grade("acme card", ev)
    assert g["grade"] == "confirmed", g


def test_grade_stale_when_only_past():
    ev = [{"url": "u", "snippet": "Manager at Visa Acme 2005 - 2010"}]
    g = grade("visa acme", ev)
    assert g["grade"] == "stale", g


def test_grade_unknown_without_evidence():
    g = grade("acme", [{"url": "u", "snippet": "nothing about roles"}])
    assert g["grade"] == "unknown", g


def test_short_claim_does_not_substring_match():
    # claimed "cal" must not match "Musical Group" / "Local Bank"
    assert not org_matches("cal", "Musical Group")
    assert not org_matches("cal", "Local Bank")
    assert org_matches("acme", "ACME Credit Cards")
    ev = [{"url": "u", "snippet": f"Director at Musical Group {THIS_YEAR} - Present"}]
    assert grade("cal", ev)["grade"] != "confirmed"


def test_stock_row_without_org_does_not_confirm_any_claim():
    # pipe rows have no org field — an empty org used to match EVERY claim
    row = f"| Ms. Jane Doe | Chief Financial Officer | -- | {THIS_YEAR} |"
    g = grade("Some Unrelated Org", [{"url": "u", "snippet": row}])
    assert g["grade"] != "confirmed", g


def test_stock_row_confirms_when_org_named_in_title():
    row = f"| Ms. Jane Doe | Chief Financial Officer, Acme Holdings | -- | {THIS_YEAR} |"
    g = grade("Acme Holdings", [{"url": "u", "snippet": row}])
    assert g["grade"] == "confirmed", g
