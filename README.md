# Judge Bias Battery

**LLM-as-judge panels have measurable, correctable biases.** This repository
ships a 5-class bias battery (position, verbosity, confident-but-wrong,
anchoring, self-preference), a stdlib-only Python harness that runs it against
any OpenAI-compatible judge endpoint with verbatim logging, and the correction
model we derived by running the battery against three open-weight judges —
glm-4-9b-chat, Llama-3.1-8B-Instruct, and qwen3-8b (CPU-served, temperature 0).
Run the battery against *your* judge, get bias coefficients out, and correct
judge-derived scores before you trust them.

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

Correction model: `s_corrected = s − coefficient` per bias class, with an
honest rule — coefficients whose 95% CI crosses zero ship
`"significant": false` and **may not** be used to claim a correction.
<!-- source: COEF-METHOD.md §3 -->

## Quickstart

Requirements: Python 3.10+ (standard library only — no dependencies) and one
or more judges served behind an OpenAI-compatible `/v1/chat/completions`
endpoint (llama.cpp, vLLM, Ollama, …).

1. **Clone and (optionally) run the offline test suite** (44 tests, mock
   server, no live calls):

   ```
   git clone <repo-url> && cd judge-bias-battery
   python -m unittest discover -s harness/tests
   ```

2. **Point at your judge.** Copy `examples/endpoints.example.json` and fill in
   your endpoint's `base_url` and served `model` id.

3. **Run one battery leg** (the `position` class, 72 calls):

   ```
   python harness/run_battery.py --config examples/runner-config.example.json
   ```

   Re-running the same config is safe: already-logged calls are skipped
   before the cap check, so interrupted runs top up without duplicates.

4. **Summarize and derive coefficients:**

   ```
   python harness/summarize.py --log logs/judge-calls.jsonl --out logs/summary.json
   python harness/derive-corrections.py derive --log logs/judge-calls.jsonl --out my-corrections.json
   ```

   Derivation is deterministic: pin `--created-utc` and re-runs are
   byte-identical. See `examples/README.md` for the expected outputs and
   `data/README.md` for how to read the coefficients.

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

## Citation

Paper and study page: [Measuring and Correcting Systematic Bias in
LLM-as-Judge Panels (321 AI Labs TR#8)](https://321ai.xyz/studies/llm-judge-bias-battery/)
— [PDF](https://321ai.xyz/studies/llm-judge-bias-battery/TR8-judge-bias-audit.pdf)

Zenodo DOI: **TBD** (placeholder — will be minted on release). See
`CITATION.cff`.

```bibtex
@software{casey2026judgebiasbattery,
  author = {Casey, Kiyoshi},
  organization = {321 AI Labs},
  title = {Judge Bias Battery: Measuring and Correcting Systematic Bias in LLM-as-Judge Panels},
  version = {1.0.0},
  year = {2026},
  note = {Zenodo DOI TBD}
}
```

## License

MIT — see `LICENSE`. The battery items in `battery/items/` are the published
scientific instrument; please do not edit them in derivative benchmarks
without versioning the change, so coefficients stay comparable.
