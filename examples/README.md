# examples/

Sanitized example configuration for running one battery leg against your own
judge. Run all commands from the repository root (relative paths in the config
resolve against your working directory).

## Files

- `endpoints.example.json` — harness-native endpoints file. One entry per
  judge: `name` (the judge key used throughout the logs and summaries),
  `base_url` (any OpenAI-compatible server, e.g. llama.cpp's `llama-server`,
  vLLM, Ollama), `model` (the served model id), `timeout_s`. Health check:
  `GET <base_url>/v1/models` must return 200.
- `runner-config.example.json` — one-leg runner config (the `position` class
  only, 36 items × 2 orders = 72 calls per judge). Point `endpoints_file` at
  your endpoints file and set `judges` to your judge's `name`. To run all
  classes, extend `bias_classes` with `"verbosity"`, `"cbw"`, `"anchoring"`,
  `"selfpref_generation"` (the `items_files` map already lists them) and drop
  or raise `max_calls_per_judge` — re-running the same config is safe:
  already-logged calls are skipped, never duplicated.

## Usage

```
python harness/run_battery.py --config examples/runner-config.example.json
python harness/summarize.py --log logs/judge-calls.jsonl --out logs/summary-position.json
python harness/derive-corrections.py derive --log logs/judge-calls.jsonl \
    --out data/judge-corrections.json
```

## Expected output

- `logs/judge-calls.jsonl` — one JSON object per line, the verbatim call log:
  full request messages, raw response text and body, parsed verdict, token
  usage, latency, and per-call metadata (`bias_class`, `item_id`, `condition`,
  `order`, `correct_slot`, judge, schema). This file IS the dataset.
- `logs/summary-position.json` — per-judge accounting: calls made, parse and
  format-compliance rates (<95% compliance is reported as a finding, never
  silently dropped), position flip counts, coverage fields
  (`n_skipped_by_cap == 0` means the leg is complete).
- `data/judge-corrections.json` — per-judge, per-class coefficients with
  Wilson 95% CIs (paired bootstrap for the anchoring class), `n`, `tie_rate`,
  and a `significant` flag. Coefficients whose CI crosses zero ship
  `significant: false` and may not be used to claim a correction.

For a smoke test against a live endpoint first (≤10 calls), see
`harness/run_smoke.py --config <smoke-config.json>`; the smoke config accepts
the same keys as the runner config.
