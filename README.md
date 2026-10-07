# people-osint-directory

A [Hermes Agent](https://hermes-agent.nousresearch.com/docs) skill (works as a plain toolkit too): turn a **name + phone number** seed from a chat group into a public-source professional profile card — LinkedIn-style directory entries for community members, built strictly from information a stranger could find with a search engine.

## What it does

For each member seed (display name + phone):

1. **Dork sweep** — 6–10 search-engine queries: name × site:linkedin/x/facebook/crunchbase/github, phone in 3 formats, non-Latin name spelling, career cross-refs. Extracts handles, roles, companies, bio links.
2. **Sherlock pivot** — feed canonical handles to [sherlock-project](https://github.com/sherlock-project/sherlock) to map cross-platform profile existence.
3. **Typed-choice verify** — every candidate identity goes through a deterministic *choice* classifier (`scripts/jev_verify.py`) that returns `match | no_match | insufficient` + calibrated confidence. **No prose LLM generation anywhere** — the output is a structured verdict, not generated text.
4. **Profile JSON** — fill `templates/profile.json`; render with whatever front-end you have.

Every profile carries an `evidence[]` array — URLs + signals per claim. That's the audit trail, and it's also your GDPR story: everything shown is public and traceable, and the skill mandates an opt-out path on any published directory.

## Design rules (the point of this skill)

- **Seeds only.** Search input is name + phone. Never feed private knowledge (chat contents, CRM, contact lists) into the search.
- **Public only.** Anything a stranger couldn't Google doesn't go in the profile.
- **No prose LLM.** Decisions are typed choices + confidence. Deterministic scripts do the rest.
- **Light toolchain.** Web search + sherlock via pipx. No servers, no databases, no scraping farms.
- **Never single-hit match.** Cross-check 2+ independent signals (phone/company/location/handle) before accepting an identity.

## Install

### As a Hermes skill

```bash
git clone https://github.com/omernesh/people-osint-directory ~/.hermes/skills/osint/people-osint-directory
pipx install sherlock-project          # handle pivot
export TYPESAFE_API_KEY=...            # or put it in ~/.hermes/.env
```

Hermes picks it up on next session start; trigger: *"enriching member profiles from name+phone seeds"*.

### As a standalone toolkit

```bash
git clone https://github.com/omernesh/people-osint-directory
cd people-osint-directory
pipx install sherlock-project
python3 scripts/jev_verify.py --help   # not yet interactive — import it:
```

```python
import sys; sys.path.insert(0, "scripts")
from jev_verify import verify_identity
verdict, conf = verify_identity(
    "Yael Ben-David", "+972500000000",
    evidence=[{"url": "https://linkedin.com/in/...", "signal": "headline match", "source": "dork"}])
# → ("match", 0.87)
```

### Dependencies

- Python 3.10+ (stdlib only for the verifier)
- [sherlock-project](https://github.com/sherlock-project/sherlock) via pipx for the handle pivot
- A [TypeSafe System One](https://typesafe.ai) API key for the typed verifier (`TYPESAFE_API_KEY`) — optional; without it you fall back to manual cross-checking
- Any web search API / agent search tooling for the dork sweep

## Layout

```
SKILL.md              # the workflow (agent-facing)
scripts/jev_verify.py # typed-choice identity verifier (stdlib only)
templates/profile.json# output schema with dummy data
```

## Ethics & legal

This skill only aggregates what search engines already index about a person. It does not breach accounts, buy data-broker dumps, or use private information. Still — publishing a directory of real people carries responsibility:

- Offer a removal/opt-out path on any public directory page.
- Rate-limit your sweeps; don't hammer search engines.
- Don't publish phone numbers themselves — they're the seed, not the content.
- Check your jurisdiction (GDPR et al.) before publishing profiles of EU persons.

## License

MIT
