# P8 Judge-Call Harness

Reusable, stdlib-only harness for LLM-as-judge calibration. Built for the
P8 judge reliability audit (BRIEF-20260914-sprint-p8-judge-audit); designed
for direct reuse by P0 (governance A/B adjudication) and P2.

Everything runs offline from raw artifacts: the JSONL call log IS the
dataset. No analysis trusts a summary table — every metric in `metrics.py`
consumes raw log records and can be recomputed by the adversarial reviewer.

## Modules

| File | Purpose |
|---|---|
| `judge_client.py` | OpenAI-compatible chat-completions client (urllib only). Config-driven endpoints, per-call temperature/seed/max_tokens, robust timeouts, max 1 retry (every attempt logged verbatim). |
| `parse.py` | Verdict parser for the two rubric schemas + strict format-compliance tracking. Tolerant extraction, strict compliance flag; `<think>` blocks stripped and their presence logged. |
| `randomize.py` | Seeded order assignment, shuffling, condition assignment (`random.Random` with string seeds — deterministic across processes). |
| `metrics.py` | Position flip rate, verbosity effect, self-vs-other delta, anchoring score drift, format compliance rate, pairwise Cohen's kappa, seeded bootstrap CIs. |
| `run_battery.py` | Orchestrator: bias class -> items x judges with per-judge call caps, seeded run order, fixed rubric prompts (pairwise + scored + generation). Resume/dedup (E-P8-1): already-logged jobs are skipped before the cap check, so interrupted/capped runs top up to 100% coverage with no duplicate calls. |
| `envelope.py` | Section-3 JSON envelope builder (artifact_type, created_utc, agent, mission_brief, inputs+sha256, environment, method, data, provenance). |
| `tests/test_harness.py` | 41 offline unit tests against a mock OpenAI-compatible server (http.server). No live calls. (37 shipped Day-1 + 2 reviewer regression tests 2026-09-14 + 2 resume/dedup tests E-P8-1 2026-09-15; the originally shipped file said "36" -- stale count.) |

## Verbatim log schema (one JSON object per line)

```
schema_version, call_id, attempt, retry_of, timestamp_utc,
endpoint, base_url, model_id,
request: {messages (verbatim), temperature, seed, max_tokens, extra_params},
raw_response_text,          <- verbatim assistant output, never summarized
raw_response_json,          <- full server response body
parsed: {ok, format_compliant, verdict, error, schema,
         think_block_present, raw_keys},
finish_reason, token_usage, latency_ms, http_status, error,
harness: {version, code_sha256: {<every harness module>: sha256}},
metadata: {run_tag, bias_class, item_id, condition, order, correct_slot,
           longer_slot, judge, schema, item_seed_source}
```

Rubric verdict formats (shown to every judge, same rubric each):

- pairwise: `{"preference": "A"|"B"|"tie", "confidence": <0-1>, "reasoning_short": "<=40 words"}`
- scored:   `{"score": <0-10>, "confidence": <0-1>, "reasoning_short": "<=40 words"}`

Format compliance is strict (valid key field + numeric in-range confidence +
string reasoning_short). A judge below 95% compliance is a FINDING, never a
silent drop — `FormatTracker` / `format_compliance_rate` report it.

## Reusing in P0 (defect adjudication)

1. Copy `harness/` (self-contained) or add it to `sys.path`.
2. Build an endpoints file:
   `{"endpoints": [{"name": "glm-4-9b", "base_url": "http://127.0.0.1:8091", "model": "glm-4-9b-chat", "timeout_s": 240}, ...]}`
3. Health-check each judge: `JudgeClient(ep, log).health()` — expect `status == 200`.
4. For blind artifact grading, build pairwise messages via
   `run_battery.build_pairwise_messages(question, artifact_A, artifact_B)`
   (arm labels NEVER enter the prompt; blind by construction).
5. Call `client.chat(messages, temperature=0, seed=<task+judge-derived seed>,
   metadata={"item_id": task, "judge": name, "schema": "pairwise"})`.
6. Parse with `parse.parse_pairwise`; compute adjudicator agreement across
   judges with `metrics.cohen_kappa` on the parsed preferences.

## Reusing in P2

- Same client/log for any LLM-as-judge scoring. Use `parse_scored` +
  `build_scored_messages` for absolute 0-10 grading.
- Before publishing judge-derived scores, apply the P8 correction model
  (`data/judge-corrections.json` in this repository) and label uncorrected
  numbers `uncorrected — pending P8`.
- Report kappa against the P8 baseline: `metrics.pairwise_kappa_from_log`.

## Running the battery

```
python run_battery.py --config runner-config.json
# config keys: endpoints_file, log_path, items_file, bias_classes[],
# seed, temperature, max_tokens, max_calls_per_judge, judges[], summary_out
```

Bias classes: `position` (AB/BA both orders), `verbosity` (plain vs
padded-incorrect), `cbw` (confident-but-wrong probe), `anchoring` (scored,
anchored vs unanchored), `selfpref_generation` (judge answers ground-truth
question; grading phase pairs its answer vs a competitor's, unlabeled).

### Resume / dedup (E-P8-1, 2026-09-15)

Re-running the same config is now safe. At startup the runner loads the
already-logged job keys from `log_path` (if present) and skips those jobs
BEFORE the cap check — they consume no budget and are never re-dispatched.

- **Dedup key:** exact string match on the log record's top-level
  `metadata.item_seed_source` field — the verbatim per-call seed source
  `"{seed}|{bias_class}|{judge}|{item_id}|{condition}|{order}"` that the
  runner rebuilds before every dispatch. This makes the match an exact
  identity on (seed, bias class, judge, item, condition, order); retry
  attempts collapse to one key. Lines without `item_seed_source` (e.g.
  precheck calls) never match battery jobs.
- **Cap semantics unchanged:** `max_calls_per_judge` and the day-cap
  accumulator count NEW dispatches only; already-logged jobs neither
  consume budget nor touch `day_calls_used`. Missing log file -> behavior
  identical to a fresh run.
- **Audited coverage** — per judge, every summary must satisfy:
  `n_jobs_by_judge[j] = calls_made[j] + n_already_logged[j] + n_skipped_by_cap[j]`
  (`n_jobs_total` is the class-wide total; `n_already_logged` and
  `n_skipped_already_logged` are the same count under both names). A run
  is complete for a class when `n_skipped_by_cap == 0` and the formula
  leaves nothing un-dispatched.

## Tests

```
cd harness && python -m unittest tests.test_harness -v
```

41 tests, all offline (mock server), including the E-P8-1 resume/dedup
tests (capped-run top-up, log-absent no-op) and the day-cap accumulator
regression test. Determinism proof for the item
generator lives in `../battery/determinism-proof.json`.
