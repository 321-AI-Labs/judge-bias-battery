# Judge Bias Battery

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Tests: 44 passing](https://img.shields.io/badge/tests-44%20passing-brightgreen.svg)](#quickstart)
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.22949194.svg)](https://doi.org/10.5281/zenodo.22949194)

**Your LLM judge has measurable biases. This battery measures them, and the
correction model fixes what is significant — nothing else.**

A 5-class bias battery (position, verbosity, confident-but-wrong, anchoring,
self-preference), a stdlib-only Python harness that runs it against any
OpenAI-compatible judge endpoint (llama.cpp, vLLM, Ollama), and the correction
model derived from running the battery against three open-weight judges.
Point it at *your* judge this afternoon, get bias coefficients out, and know
which of your eval scores to stop trusting.

## Run it in 5 minutes

Requirements: Python 3.10+ (standard library only — no dependencies) and one
judge served behind an OpenAI-compatible `/v1/chat/completions` endpoint.

```bash
git clone https://github.com/321-AI-Labs/judge-bias-battery.git && cd judge-bias-battery

# optional: 44 offline tests against a mock server, zero tokens spent
python -m unittest discover -s harness/tests

# point at your judge, then run the position leg (72 calls)
cp examples/endpoints.example.json endpoints.json   # fill in base_url + model
python harness/run_battery.py --config examples/runner-config.example.json

# derive your judge's bias coefficients
python harness/summarize.py --log logs/judge-calls.jsonl --out logs/summary.json
python harness/derive-corrections.py derive --log logs/judge-calls.jsonl --out my-corrections.json
```

## What you get out

One coefficient per bias class, with its 95% CI and a significance flag —
a real record from this repo's audit:

```json
{
  "class": "position",
  "judge": "glm",
  "coefficient": 0.25,
  "coefficient_name": "flip_rate",
  "ci95": [0.1375, 0.4107],
  "n": 36,
  "significant": true,
  "method_sha": "18e27702…"
}
```

Every call is logged verbatim (full request, raw response, parsed verdict,
latency). The log file IS the dataset; the coefficients are reproducible from
it, and derivation is deterministic — pin `--created-utc` and re-runs are
byte-identical.

## Why this exists

If you use LLM-as-judge scores anywhere — eval pipelines, reward models,
leaderboards, routing — those scores carry the judge's biases in silence.
Swap the order of two answers and the verdict can flip. Pad a wrong answer to
three times the length and it starts winning. And a judge grading its own
model's outputs is not grading on the same curve as everyone else's. None of
this shows up in the score itself. The only way to know whether it applies to
*your* judge, on *your* tasks, is to measure it — which is what an afternoon
with this battery does.

## Headline findings (P8 Judge Reliability Audit, 321 AI Labs, Sept 2026)

Measured on a 741-call battery (0 errors, 0 parse exclusions in scored
classes): <!-- source: judge-corrections.json (envelope.inputs: "741 calls, 0 errors") -->

| Judge | Position flip_rate | Verbosity padded_pref | CBW wrong_pref | Anchoring drift |
|---|---|---|---|---|
| glm-4-9b-chat | **0.25** [0.14, 0.41] | **0.25** [0.15, 0.39] | 0.00 (n.s.) | +0.42 (n.s.) |
| Llama-3.1-8B | **0.50** [0.34, 0.66] | **0.42** [0.29, 0.56] | **0.075** [0.03, 0.20] | −1.17 (n.s.) |
| qwen3-8b | **0.17** [0.08, 0.32] | **0.15** [0.07, 0.27] | 0.00 (n.s.) | −0.17 (n.s.) |

<!-- source: judge-corrections.json, data.corrections (coefficient, ci95, significant fields) -->

- **Position bias**: Llama-3.1-8B flipped its verdict between AB/BA
  presentation orders on **half** of its position pairs (0.50); glm and qwen
  flipped 25% and 17% respectively — all three significantly above zero.
  <!-- source: judge-corrections.json; DERIVATION-NOTES.md ("Substantive read") -->
- **Verbosity bias**: all three judges preferred the padded (longer,
  incorrect) answer significantly above chance. <!-- source: judge-corrections.json -->
- **Confident-but-wrong**: only Llama showed a detectable wrong-answer
  preference (0.075); glm and qwen were at exactly 0. <!-- source: judge-corrections.json -->
- **Anchoring**: no significant drift for any judge at the exploratory n=12.
  <!-- source: judge-corrections.json -->
- **Self-preference** (44 paired blind grading rounds, preregistered
  estimator): glm scored its own answers **+3.53** points higher than
  competitors' [1.53, 5.67], qwen **+4.53** [2.20, 6.87], and Llama **−5.71**
  [−8.14, −3.14] (scores its own answers lower) — all significant on a 0–10
  scale. <!-- source: selfpref-corrections.json, data.corrections -->

## The correction model, with an honesty gate

`s_corrected = s − coefficient` per bias class — with a hard rule:
coefficients whose 95% CI crosses zero ship `"significant": false` and **may
not** be used to claim a correction. The code enforces it. A correction
derived from noise would manufacture precision that was never measured, so
the gate matters more than the correction.
<!-- source: COEF-METHOD.md §3 -->

Scope limits, stated flat: the audited judges are 8–9B open-weight models, so
magnitudes do not transfer to frontier judges; and anchoring is unresolved at
n=12, not absent. Re-run the battery against the judge you actually use.

## Repository layout

```
harness/            stdlib-only judge-call harness
  run_battery.py      orchestrator: bias class × items × judges, verbatim JSONL log
  run_smoke.py        ≤10-call smoke test against a live endpoint
  summarize.py        per-judge summary from a raw log (cross-check artifact)
  derive-corrections.py  bias coefficients from the verbatim log (derive / cross-check)
  derive-selfpref.py  self-preference grading coefficients (preregistered estimator)
  judge_client.py     OpenAI-compatible client; parse.py, metrics.py, randomize.py, envelope.py
  tests/              44 offline unit tests against a mock server
battery/            the scientific instrument (items copied verbatim from the study)
  battery-spec.json   class table and sizing rationale
  items/              the five item payloads + selfpref grading items (+ erratum note)
  build_items.py      item construction from an oracle battery (provenance;
                      oracle source not shipped)
  build_selfpref_grading.py  grading-phase item builder (provenance;
                      original generation log not shipped)
  determinism-proof.json   byte-identity proof for the item generator
data/               the measured correction model
  judge-corrections.json      headline coefficients (position/verbosity/cbw/anchoring)
  selfpref-corrections.json   self-preference grading coefficients
docs/
  COEF-METHOD.md      the frozen, binding derivation method (+ preregistered amendments)
  DERIVATION-NOTES.md full derivation session write-up and reconciliation results
examples/           sanitized endpoints + runner config and expected outputs
```

Re-running any config is safe: already-logged calls are skipped before the
cap check, so interrupted runs top up without duplicates. To run all bias
classes, extend `bias_classes` in the runner config — see
`examples/README.md`.

## Citation

Paper and study page: [Measuring and Correcting Systematic Bias in
LLM-as-Judge Panels (321 AI Labs TR#8)](https://321ai.xyz/studies/llm-judge-bias-battery/)
— [PDF](https://321ai.xyz/studies/llm-judge-bias-battery/TR8-judge-bias-audit.pdf)

Zenodo DOI: [10.5281/zenodo.22949194](https://doi.org/10.5281/zenodo.22949194)
(concept DOI — always resolves to the latest release). See
`CITATION.cff`.

```bibtex
@software{casey2026judgebiasbattery,
  author = {Casey, Kiyoshi},
  organization = {321 AI Labs},
  title = {Judge Bias Battery: Measuring and Correcting Systematic Bias in LLM-as-Judge Panels},
  version = {1.0.0},
  year = {2026},
  doi = {10.5281/zenodo.22949194}
}
```

## License

MIT — see `LICENSE`. The battery items in `battery/items/` are the published
scientific instrument; please do not edit them in derivative benchmarks
without versioning the change, so coefficients stay comparable.
