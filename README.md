# people-osint

A [Hermes Agent](https://hermes-agent.nousresearch.com/docs) skill (works as a plain toolkit too): run a **public-source investigation on a person** from whatever seed you have — name, phone number, email, social profile link, or any combination — and get back a **structured report** (JSON), not generated prose.

Built for: contact checks, background research, directory enrichment, speaker/founder vetting, "who is this person" questions. Everything in the report is findable by a stranger with a search engine.

## What it does

1. **Plans the sweep** — `scripts/dorks.py` turns the seed into a deterministic query plan (site-scoped dorks, phone in 3 formats, email exact search, cross-refs) + candidate handles. Same seed = same plan.
2. **Runs the sweep** — `scripts/sweep.py` executes the plan via Exa (mcporter), paced, appending `{query, engine, title, url}` rows to a JSONL evidence file; engine failures are recorded, never silently swallowed.
3. **Pivots handles** — [sherlock-project](https://github.com/sherlock-project/sherlock) maps candidate handles across ~400 sites (existence, not identity). Use text mode `--print-found`; `--json` has been flaky.
4. **Typed verdict** — `scripts/jev_verify.py` returns `match | no_match | insufficient` + calibrated confidence via a typed-choice classifier. Evidence rows carry `source`/`signal`/`url` keys. **No prose LLM anywhere** — decisions are structured choices, the report is JSON.
5. **Validates & reports** — `scripts/validate_report.py` checks JSON validity, evidence keys and confidence bounds before delivery; `templates/report.json` is the schema: seed, verdict, profile (socials/career/ventures/location/publications), digital footprint (is the phone/email indexed? breach hits?), evidence array, recommendations.

The evidence array is the audit trail: every non-null field traces to a URL. That's also your GDPR story — everything shown is public and traceable.

## Design rules (the point of this skill)

- **Seeds only.** Search input is exactly what the user gave. Private knowledge never enters the search.
- **Public only.** Anything a stranger couldn't Google doesn't go in the report.
- **No prose LLM.** Typed-choice verdicts + deterministic scripts. Structured output, not paragraphs.
- **Light toolchain.** Web search + sherlock via pipx. No servers, no databases, no scraping farms.
- **Never single-hit match.** Cross-check 2+ independent signals (phone/company/location/handle) before calling it a match.

## Install

### As a Hermes skill

```bash
git clone https://github.com/omernesh/people-osint ~/.hermes/skills/osint/people-osint
pipx install sherlock-project          # handle pivot
export TYPESAFE_API_KEY=...            # or put it in ~/.hermes/.env
```

Hermes picks it up on next session start; trigger: *"investigate a person from name/phone/email/social"*.

### As a standalone toolkit

```bash
git clone https://github.com/omernesh/people-osint
cd people-osint
pipx install sherlock-project
```

```bash
# 1. deterministic query plan
python3 scripts/dorks.py --name "Jane Doe" --phone "+15551234567" \
    --email "jane@example.com" --social "https://x.com/janedoe"

# 2. run the queries with your search tool, collect evidence rows

# 3. typed verdict
python3 - <<'EOF'
import sys; sys.path.insert(0, "scripts")
from jev_verify import verify_identity
verdict, conf = verify_identity(
    "Jane Doe", "+15551234567",
    evidence=[{"url": "https://linkedin.com/in/janedoe",
               "signal": "headline matches; same company as phone cross-ref",
               "source": "dork"}])
print(verdict, conf)   # -> match 0.87
EOF
```

### Dependencies

- Python 3.10+ (stdlib only for the scripts)
- [sherlock-project](https://github.com/sherlock-project/sherlock) via pipx for the handle pivot
- A [TypeSafe System One](https://typesafe.ai) API key for the typed verifier (`TYPESAFE_API_KEY`) — optional; without it you decide the verdict manually from the evidence rows
- Any web search API / agent search tooling for the sweep

## Layout

```
SKILL.md              # the workflow (agent-facing)
scripts/dorks.py      # deterministic query planner (stdlib only)
scripts/jev_verify.py # typed-choice identity verifier (stdlib only)
templates/report.json # structured report schema (dummy data)
```

## Ethics & legal

This skill only aggregates what search engines already index about a person. It does not breach accounts, buy data-broker dumps, or use private information. Still — investigating real people carries responsibility:

- Rate-limit your sweeps; don't hammer search engines.
- Don't publish phone numbers/emails themselves — they're seeds, not content.
- Check your jurisdiction (GDPR et al.) before publishing profiles of EU persons; offer removal paths where applicable.
- Stay out of breach dumps and purchased datasets — public search only.

## License

MIT
