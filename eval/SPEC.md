# Evaluation Harness Spec — people-osint

**Status:** draft (2026-10-13) · **Owner:** Omer · **Implementation:** eval/ (in progress)

This spec defines the measurement layer for the people-osint skill: how we
score pipeline quality, how we prove an upgrade helped, and how we submit
results to the external [People Search Bench](https://github.com/LessieAI/people-search-bench)
benchmark. Metric definitions follow the bench's methodology (MIT, credited);
implementation lives in `eval/metrics.py` as the single source of truth —
docs never restate formulas beyond pointers (lesson taken from the bench's
own doc/code drift).

## Track A — Self-eval (before/after regression)

**Question answered:** did a change to the pipeline make results better,
worse, or just different?

### Protocol

1. **Freeze the baseline** (pre-change commit `C₀`):
   - `plan.json` — deterministic, byte-identical for the same seed
   - `evidence.jsonl` — raw sweep output (append-only)
   - `scan_result.json` / final report artifacts
   - store under `~/.hermes/data/osint/<seed>-baseline/`
2. **Apply the change** (commit `C₁`).
3. **Re-run the same seed** with identical CLI flags and pace; store under
   `~/.hermes/data/osint/<seed>-after/`.
4. **Diff the layers** (this is the comparison contract):
   - *Plan layer:* identical by construction (determinism check — if it
     differs, the change touched the planner; flag it)
   - *Evidence layer:* rows, ok-with-URL count, engine errors, domain mix
   - *Candidate layer:* number of distinct candidate clusters found
   - *Verdict layer:* verdict + confidence per candidate, ranking
   - *Report-quality score:* structural completeness (see metrics below)
5. **Live-web caveat, stated up front:** Exa results drift between runs.
   Evidence-layer diffs are indicative, not causal. Verdict-layer and
   report-layer diffs are the primary signal; evidence diffs are context.
   For tighter causality, re-run the baseline at `C₀` twice — the delta
   between two baseline runs is the noise floor, and the after-run must
   beat it.

### Seed set for regression runs

Minimum: 3 seeds of different types (one name-only — collision stress; one
name+phone; one email or social). "Ran Margalit" (name-only, 3 known real
candidates) is the standing regression seed.

## Track B — External benchmark (People Search Bench)

**Question answered:** where does this pipeline land vs Lessie, Exa,
Juicebox and Claude Code on public, reproducible queries?

- Query set: the bench's 119 published queries (4 categories: b2b,
  deterministic, influencer, recruiting) — cached under `eval/data/`.
- Runner: `eval/run_bench.py` — each query prompt goes through the same
  sweep engine (scripts/sweep.py, Exa path), producing
  `person_data` rows. ≤15 results per query. No fabrication, no manual
  curation (bench submission rules).
- Export: submission CSV with exact columns `query_id, agent_name,
  person_data` (`--export-submission`), PR-able to the bench repo.
- Grading: the bench's criteria-grounded verifier (external, Tavily-backed)
  or a local grading run using `eval/metrics.py` once per-criterion grades
  exist. Published per-platform baselines for context:
  Lessie 65.2 · Exa 55.0 · Claude Code 46.0 · Juicebox 45.8 (overall).
  Our engine is Exa-based → Exa 55.0 is the honest apples-to-apples bar.

## Metrics (single source of truth: `eval/metrics.py`)

| Metric | Definition (implemented) | Layer |
|---|---|---|
| Relevance Precision | padded nDCG@10 — ideal DCG assumes 10 perfect results exist | bench / ranking |
| Precision@k | qualified (grade ≥ 0.5) in top-k ÷ k | bench |
| Effective Coverage | TCR × mean(min(qualified/K, 1)) | bench |
| Report-Quality Score | structural completeness: weighted field coverage of the report schema (per-scenario weights), 0–100 | self-eval |
| Consistency | 100 × max(0, 1 − σ/μ) of relevance across categories (bench) or across seed types (self-eval) | both |

Qualified-grade production for Track A uses the skill's own criteria-grounded
verifier (`scripts/criteria_verify.py`): each evidence cluster is scored
against per-field criteria (three-level: met 1.0 / partial 0.5 / not-met 0.0),
aggregated to a relevance grade; clusters are ranked by padded nDCG-style
positional weighting of their grades.

## Acceptance criteria for the current upgrade (C₀ → C₁)

1. Same seed, same plan (determinism unbroken).
2. After-run produces ≥1 ranked candidate with per-candidate verdicts where
   baseline produced a single flat `insufficient`.
3. No regression in failure honesty: engine_error rows still surface; exit
   codes unchanged; all tests green (existing 20 + new eval tests).
4. Report-quality score computable for both runs and reported in the diff.
5. Submission CSV exportable for ≥1 bench category without crashing on
   engine errors.

## Non-goals

- No LLM-as-judge anywhere in scoring (bench's own verifier aside, which is
  external and their choice).
- No live bench run in CI; Track A regression runs are on-demand, not
  scheduled.
- No private data in eval fixtures — bench queries and regression seeds only
  (seeds rule of the skill applies here too).
