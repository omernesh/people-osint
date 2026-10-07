---
name: people-osint
description: Use when investigating a person from any seed — name, phone, email, social link, or any combination — using public sources only.
---

# People OSINT — General Investigation

Runs a public-source investigation on a person from **whatever seed you have**:
name, phone number, email, or a social profile link — any subset. Produces a
**structured report** (see `templates/report.json`); the caller decides what
to do with it (directory card, contact check, background research).

## Hard rules
- **Seeds ONLY.** Search input is exactly what the user gave (name / phone /
  email / social). Never feed private knowledge (memory, chat contents, CRM,
  contact lists) into the search or into evidence.
- **No prose LLM.** The identity verdict comes from a typed-choice classifier
  (`scripts/jev_verify.py`) — `match | no_match | insufficient` + calibrated
  confidence, never free text. Query planning and report shaping are pure
  scripts. The report itself is structured JSON, not generated prose.
- **Light tools only.** No servers, no scraping farms. Web search/extract +
  `sherlock` (pipx) are the toolchain.
- **Public only.** Every field must be reachable by a stranger Googling the
  seed. Link out to sources; never fabricate; never include private data
  (address, family, health, account numbers...).
- **No single-hit identity.** Name collisions are the killer — cross-check
  2+ independent signals (phone/company/location/handle) before `match`.

## Pipeline flow
1. **Plan the sweep** — `scripts/dorks.py --name ... --phone ... --email ...
   --social ...` → ordered query plan + candidate handles. Pure script,
   deterministic, same seed = same plan.
2. **Run the sweep** — `scripts/sweep.py plan.json --out evidence.jsonl
   [--pace 2.0] [--max 30]` executes the plan via Exa (mcporter), paced,
   writes one JSONL row per hit, and NEVER masks engine failures (each
   failed call becomes an `{"error": ...}` row). Prefer this over ad-hoc
   shell loops: a `cmd || fallback | head` pipe swallows errors and looks
   like a clean empty result. Collect `{url, signal, source}` rows:
   signal = WHAT matched (name in headline, same company, same location).
   Zero hits on a phone/email is a finding: clean footprint.
3. **Sherlock pivot** (`pipx install sherlock-project`) — feed canonical
   handles. Pitfall: `--json` has returned empty output on this install —
   use text mode `--print-found --timeout 8` and parse the
   `[+] site: url` lines. Hits = profile EXISTS, not identity.
4. **Typed verify** — `scripts/jev_verify.py verify_identity(name, phone,
   evidence)` → verdict + confidence. Evidence rows MUST carry the keys
   `source`, `signal`, `url` (exactly what the script reads — templates and
   reports that write different key names break the call). Confidence < 0.5
   on `match` → treat as `insufficient`; 0.5–0.6 → provisional, flag it in
   the report. Non-Latin names ride in the state text; criteria keys stay
   Latin (`c1..cN`).
5. **Validate** — `scripts/validate_report.py report.json [--strict]`.
   Checks JSON validity, required keys, evidence-row keys, and match-below-
   0.6 warnings. Run it before delivering; hand-written JSON breaks more
   often than it should. Both report shapes are accepted: multi-candidate
   (`candidates[]`, template shape) and deep-dive (verdict/confidence/
   evidence at top level).
6. **Report** — fill `templates/report.json`: seed, verdict, profile,
   footprint (is the phone/email indexed anywhere?), evidence, and
   recommendations. Every non-null field traces to an evidence row. Save to
   a stable path (`~/.hermes/data/osint/<slug>.json`), then deliver:
   `publish-link <path> <slug>` → https://hermes.nesher.co/f/<slug>. Never
   paste local filesystem paths into chat.

## Ambiguous seeds — disambiguate, then re-run enriched
A name-only seed can map to several real people (e.g. "Ran Margalit" → 3
verified candidates). When the sweep returns 2+ plausible distinct people:
1. Report the candidates with per-candidate confidence and ASK the user for
   one disambiguator (company, city, email, how they know the person).
2. Re-run the pipeline with the enriched seed (name + company/city). The
   enriched run is a DEEP DIVE: fetch and extract press mentions, corporate
   registries, patents, and public profile mirrors (Humantic etc.), then
   typed-verify the single identity.

## Founder investigations — corporate registries
For founders/executives, add these to the sweep — they surface team, dates
and co-founders search engines often miss:
- **IVC Data & Insights** (ivc-online.com Google-Card) — Israeli-registered
  entity cards list management, co-founders, prior roles. Example find: the
  ShelfX card listed the full co-founder team including family members
  (CEO's wife as COO) and a co-founder the user knew personally.
- CB Insights people pages, Tracxn, PitchBook profiles, TheCompanyCheck,
  aVenture — US company cards.
- Patents (patents.google.com) — inventor records tie identity to domain;
  citation counts signal significance.
- Co-founder/family connections found here are PUBLIC data — report them
  factually ("registry lists X as co-founder"), never from private memory.

## Seed combinations
- **Name only** — highest collision risk; distinguishing signals (company,
  location, handles) decide. Common names → expect `insufficient`.
- **Phone only** — phones are rarely indexed; absence is normal. Use the
  phone to confirm/deny candidates found by name.
- **Email** — strongest single seed; search it exact, then its local part as
  a handle.
- **Social link** — extract the handle, pivot sherlock, then name-search the
  display name found there.
- **Any combination** — cross-ref queries (`"name" "phone"`) are the most
  reliable `match` basis.

## Pitfalls
- **Name collisions are the killer.** Common names return unrelated
  businesses, other people, deceased relatives. Never single-hit match.
- **Phone numbers are rarely indexed** — absence of phone hits is normal.
- **The typed classifier is Latin-script only** — non-Latin names ride in the
  state text, criteria keys stay `c1..cN`; non-Latin-only evidence →
  phone-signal fallback.
- **Sherlock false positives:** profile-existence ≠ identity. Squatters exist.
- **Breach-data temptation:** stay out of dumped/purchased datasets — public
  search engines only. If a breach hit surfaces organically in search, note
  it under `footprint.breach_hits`, never enumerate exposed secrets.
- **Be a good citizen:** rate-limit searches; upstream bans are real.
