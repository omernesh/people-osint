---
name: people-osint-directory
description: Use when enriching member profiles from name+phone seeds via public sources only.
---

# People OSINT — Directory Enrichment

Public-info enrichment for member directories. Turns a messaging-app-exposed
seed (display name + phone number) into a professional profile card using only
information a stranger could find with a search engine.

## Hard rules
- **Seeds ONLY:** name + phone number. Never use private knowledge (memory,
  prior chats, CRM data, contact lists) as search input or evidence.
- **No prose LLM.** Identity decisions go through a typed-choice classifier
  (`scripts/jev_verify.py`) — a choice + calibrated confidence, never free
  text. Everything else is pure script / deterministic.
- **Light tools only.** No servers, no heavy deps. Web search/extract +
  `sherlock` (pipx) are the whole toolchain.
- **Public only.** Every field in the output must be reachable by a stranger
  Googling the name. Link out to sources; never fabricate, never include
  private data (address, family, health...).

## Pipeline flow
1. **Resolve phone** — when only a name is given: locate the member in your
   chat export (sender IDs like `972…@c.us` / `@s.whatsapp.net` / `@lid`).
   This is the only step that touches your private data — everything
   downstream is public-source.
2. **Dork sweep** (pure script) — 6–10 queries per member, batched:
   - `"<name>" site:linkedin.com`, `site:x.com`, `facebook`, `crunchbase`, `github`
   - `"<phone>"` in 3 formats (local, hyphenated, international) — zero hits
     is normal (phones are rarely indexed); that's a finding: clean footprint
   - Non-Latin spelling of the name (e.g. Hebrew) for local sources
   - career cross-refs from first hits: `"<name>" <company>`
   - Extract: handles, current role, companies, bio links (a personal bio
     link in a profile is gold — it often lists ventures directly)
3. **Sherlock pivot** (`pipx install sherlock-project`) — feed canonical
   handles; `--json --timeout 8`. Hits mean the profile EXISTS, not that it's
   your person.
4. **Typed-choice verify** (`scripts/jev_verify.py`) — one choice question
   per candidate: does the evidence match the seed? Criteria keys `c1..cN`
   (Latin only). Confidence ≥0.5 → accept; below → discard, or fall back to
   phone-signal matching.
5. **Render** — fill `templates/profile.json`; your front-end renders the
   card. Keep the evidence array — it's your audit trail and your GDPR story.

## Profile template
See `templates/profile.json`:
identity (seed + resolved name + confidence), socials (per-platform +
sherlock_hits), career, ventures, evidence[{url, signal, source}].

## Pitfalls
- **Name collisions are the killer.** Common names return unrelated
  businesses, people in other cities, deceased relatives. Never single-hit
  match; cross-check 2+ independent signals (phone/company/location/handle).
- **Phone numbers are rarely indexed** — absence of phone hits is normal.
- **The typed classifier is Latin-script only** — non-Latin names ride in the
  state text, criteria keys stay `c1..cN`; non-Latin-only evidence →
  phone-signal fallback.
- **Sherlock false positives:** profile-existence ≠ identity. Squatters exist.
- **Respect removal:** a public directory built from chat members should
  offer an opt-out/removal path — pre-empts GDPR-style complaints (EU).
- **Be a good citizen:** rate-limit your searches; upstream bans are real.
