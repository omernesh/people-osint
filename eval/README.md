# eval/ — people-search-bench harness

Runs the [people-search-bench](https://github.com/LessieAI/people-search-bench)
(MIT) query set through the people-osint sweep pipeline and produces a bench
submission CSV. The scoring math (padded nDCG etc.) lives in
`eval/metrics.py`; the runner is `eval/run_bench.py`.

## Layout

```
eval/metrics.py    pure scoring math (padded nDCG, precision@k, coverage, aggregate)
eval/run_bench.py  CLI: download/load bench queries -> sweep.py -> submission CSV
eval/data/         cache for the 4 bench query JSONL files (b2b, deterministic,
                   influencer, recruiting; 119 queries total)
eval/runs/<ts>/    per-run outputs: evidence.jsonl + submission.csv
```

## Usage

```bash
# full bench (119 queries; downloads query files once into eval/data/)
python3 eval/run_bench.py

# subsets — deterministic: filter, sort by query_id, then cap
python3 eval/run_bench.py --category recruiting --limit 5
python3 eval/run_bench.py --queries rec_0001,b2b_0003

# extra copy of the submission CSV somewhere specific
python3 eval/run_bench.py --export-submission /tmp/submission.csv
```

Query selection is **sorted by `query_id`**, so `--limit 5` and reruns are
reproducible. Engine calls are paced ≥ 1.5 s apart (`--pace`, default 2.0).
Every prompt goes through `scripts/sweep.py` — the same Exa/mcporter path
investigations use, never a reimplementation — and one failing query never
kills the run: failures land in evidence as `status: engine_error` rows and
the harness logs and continues (exit code 3 only if *every* query failed,
mirroring sweep.py).

## Submission rules (bench-side)

The submission CSV has the exact columns `query_id,agent_name,person_data`
(one row per returned person). Two hard rules from the bench:

- **≤ 15 results per query** — more rows are truncated at 15 per query.
- **No fabrication** — every `person_data` row traces to an `ok` evidence
  row with a URL; nothing is invented or manually curated to look better.
  The bench measures real pipeline performance.

`person_data` = person name (best-effort heuristic: the title's head before
the first dash/pipe/comma when it has 2+ space-separated tokens) + ` | ` +
URL + first 200 chars of the snippet. When the title isn't name-ish the
raw title is used as the lead — the row is still emitted, never dropped.

## Scoring

The CSV is graded by the bench pipeline (criteria extraction → web
verification → per-person 0..1 relevance grades). Once graded results
exist, feed them to `eval/metrics.py`:

- **Relevance precision** — padded nDCG@10: the ideal DCG assumes 10
  perfect results, so 3 perfect results out of 3 score below 10/10.
- **Effective coverage** — task-completion rate × mean coverage, where
  coverage = min(qualified / requested_k, 1), qualified = grade ≥ 0.5.
- **Consistency** — `100 * max(0, 1 - pstdev/mean)` across per-category
  relevance scores.
- Failed / unanswered queries **zero-pad** into every aggregate.

```python
import sys; sys.path.insert(0, "eval")
from metrics import aggregate, consistency_score

# per-query rows: {"query_id": ..., "grades": [0.7, 0.2, ...], "requested_k": 10}
scores = aggregate(rows, total_queries=119)
print(scores["overall"])                       # 0..1
print(consistency_score([0.61, 0.55, 0.48]))   # 0..100
```

## Tests

```bash
python3 -m pytest tests/ -q        # includes tests/test_eval.py (no network)
```
