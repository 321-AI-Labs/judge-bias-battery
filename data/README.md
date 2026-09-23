# data/ — derived bias-correction coefficients

## `judge-corrections.json`

The headline data product of the P8 Judge Reliability Audit (321 AI Labs,
September 2026): per-judge, per-bias-class correction coefficients for three
open-weight judges (`glm` = glm-4-9b-chat, `llama` = Llama-3.1-8B-Instruct,
`qwen` = qwen3-8b), all served locally on CPU via an OpenAI-compatible API.

Every coefficient was computed **from the verbatim judge-call log only**
(741 calls, 0 errors), joining each call to its battery item via the per-call
seed source; the runner's summary files were used as cross-checks only and
recomputed independently (0 field diffs). The binding, frozen method is
[`docs/COEF-METHOD.md`](../docs/COEF-METHOD.md); the derivation script is
[`harness/derive-corrections.py`](../harness/derive-corrections.py); the full
session write-up is [`docs/DERIVATION-NOTES.md`](../docs/DERIVATION-NOTES.md).

Per class, the coefficient is (quoting the frozen method):

- **position** — `flip_rate`: P(the judge's verdict differs between
  presentation orders AB and BA on the same item)
- **verbosity** — `padded_pref`: P(the judge prefers the padded, longer,
  incorrect answer)
- **cbw** — `wrong_pref`: P(the judge prefers the confident-but-wrong answer)
- **anchoring** — `drift`: mean(anchored score) − mean(unanchored score) on
  identical items, 0–10 scale
- **selfpref** — generation-only in this file; the grading-phase coefficients
  ship separately in `selfpref-corrections.json`

Correction model: `s_corrected = s − coefficient`, applied per class as
documented in the file's `application_notes`. Honest rule: rows whose 95% CI
crosses zero ship `"significant": false` and **may not** be used to claim a
correction.

## `selfpref-corrections.json`

Self-preference grading-phase coefficients (`drift_sp` = mean score a judge
gives its **own** answers minus mean score given to competitor answers, blind,
15 paired items per judge), derived by
[`harness/derive-selfpref.py`](../harness/derive-selfpref.py) per the
preregistered Amendment 2026-09-16 in `docs/COEF-METHOD.md`.

## Reproducing

Run the battery against your own judge (see the top-level README Quickstart),
then:

```
python harness/derive-corrections.py derive \
    --log logs/judge-calls.jsonl \
    --items-dir battery/items \
    --spec battery/battery-spec.json \
    --method-doc docs/COEF-METHOD.md \
    --out data/judge-corrections.json
```

Derivation is deterministic: re-running with `--created-utc` pinned reproduces
the file byte-identically.
