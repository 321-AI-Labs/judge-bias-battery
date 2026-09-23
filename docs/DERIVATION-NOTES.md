# P8 DERIVATION NOTES — judge-corrections.json (2026-09-15)

> **Public-release note:** lab-internal absolute paths below were rewritten to
> this repository's layout. The sha256 pins refer to the original lab-internal
> files; the shipped copies in this repo are byte-identical to the pinned
> artifacts except where the file itself records otherwise.

Worker: P8 judge-reliability audit (coefficient re-derivation).
Binding method: `docs/COEF-METHOD.md` (FROZEN 2026-09-16),
sha256 `18e27702ad728e703f8b010da24cad9c5fdf23d3a79f4adac80689a4059c41b2` — followed
exactly; no amendments made, no silent deviations (two documented interpretations
below, both flagged in OPEN-ISSUES.md). Post-derivation note (2026-09-16): the
doc has since gained dated Amendments (ISSUE-010 resolution + bootstrap seed
notation, per derivation review R-2/R-4); the sha above is the pre-amendment
doc as used for the derivation.

## Deliverables

- `data/judge-corrections.json` — sha256
  `8d8208cf1ca48c79c6fd618d4ab6e179563d3d3f0960d2c9ce20e0c76deb7ef7`
- `harness/derive-corrections.py` — sha256
  `443a3350a1bc010eb166c537335f22e5102ba9067f859082231b879e53c0f722` (stdlib only;
  corrected 2026-09-16 per derivation review R-1 — this note previously pinned the
  pre-posix-fix sha `ffb07712…`; the artifact envelope's embedded sha was always
  the correct one)
- derivation-log.txt (original study log directory, not shipped) — full stdout of the session
- summary-recompute-diff.json (original study log directory, not shipped) — §4.2 diff artifact,
  sha256 `fc0bef2be9a1a41d755093182516765a4d4dbaa526536cb79c7f66c4b0bc40cd`

## Coefficient table (verbatim-log re-derivation; Wilson 95% except anchoring)

| judge | class | coefficient | value | ci95 | n | sig |
|---|---|---|---|---|---|---|
| glm | position | flip_rate | 0.2500 (9/36) | [0.1375, 0.4107] | 36 | yes |
| glm | verbosity | padded_pref | 0.2500 (12/48) | [0.1492, 0.3878] | 48 | yes |
| glm | cbw | wrong_pref | 0.0000 (0/40) | [0.0000, 0.0876] | 40 | no |
| glm | anchoring | drift | +0.4167 | [0.0000, 0.9167] | 12 | no |
| llama | position | flip_rate | 0.5000 (18/36) | [0.3447, 0.6553] | 36 | yes |
| llama | verbosity | padded_pref | 0.4167 (20/48) | [0.2885, 0.5572] | 48 | yes |
| llama | cbw | wrong_pref | 0.0750 (3/40) | [0.0258, 0.1986] | 40 | yes |
| llama | anchoring | drift | −1.1667 | [−3.5000, 0.5000] | 12 | no |
| qwen | position | flip_rate | 0.1667 (6/36) | [0.0787, 0.3189] | 36 | yes |
| qwen | verbosity | padded_pref | 0.1458 (7/48) | [0.0725, 0.2717] | 48 | yes |
| qwen | cbw | wrong_pref | 0.0000 (0/40) | [0.0000, 0.0876] | 40 | no |
| qwen | anchoring | drift | −0.1667 | [−0.6667, 0.2500] | 12 | no |
| — | selfpref | (none) | status `generation-only`, n=15/judge (45 generation calls) | | | |

Anchoring CI: paired bootstrap, 10,000 resamples, per-resample RNG
`random.Random("20260914|anchoring|<judge>|<resample>")`, percentile 2.5/97.5
(harness `bootstrap_ci` index convention). Ties: zero TIE verdicts in any scored
class, so tie_rate = 0.0 everywhere it applies; the tie policy (excluded from
numerator, kept in n; position tie-pairs excluded from n and counted) is
implemented and documented but not exercised by this data.

Substantive read: llama flips preference across presentation orders on HALF its
position pairs (0.50) — strong position bias; glm 0.25 and qwen 0.17 are also
significantly above zero. All three judges prefer the padded (longer, incorrect)
answer significantly above chance. llama is the only judge with a detectable
confident-but-wrong preference (0.075; glm/qwen at exactly 0). Anchoring shows no
significant drift for any judge at the exploratory n=12 (llama's −1.17 mean is
carried by two items with deltas −8/−10; per-item deltas are in the artifact).

## Reconciliation results

- §4.1 call accounting (per judge × class):
  `calls == parsed + parse_failures + generation_only_unparsed + join_failures` —
  **PASS for all 15 cells**. Parse exclusions (`parsed.ok=false`): **0 in every
  scored class**; the 45 selfpref rows are generation-only (no verdict to parse,
  `parsed=null`), matching the shipped summary's `n_parsed: 0`.
- §4.1 pooled parsed vs shipped summaries (±0): position 216/216, verbosity
  288/288 (144 main + 144 top-up), cbw 120/120 (60+60), anchoring 72/72,
  selfpref 0/0 — **all match**.
- §4.2 independent recompute of all SEVEN `summary-*.json` from raw + payloads:
  **MATCH, 0 field diffs** (calls_made per judge, n_errors, n_parsed, n_items,
  n_specs, n_jobs, n_skipped_by_cap, and the top-up dedup fields
  n_already_logged / n_skipped_already_logged / per-judge splits). Also verified:
  each summary's recorded `runner_config` / `endpoints_file` / `items_file` sha
  matches the file on disk. The `health` block is runtime server-probe detail and
  is not recomputable from the log — recorded as out of diff scope.
  (Transparency: my first recompute flagged `n_skipped_by_cap` on the two top-up
  summaries; that was a bug in MY formula — top-up runs count cap-skips and
  already-logged skips in separate counters per E-P8-1. Fixed; the shipped
  summaries are internally consistent.)
- Coverage self-check (task step 5): per judge — position 72/72, verbosity 96/96
  (incl. top-up), cbw 40/40 (incl. top-up), anchoring 24/24, selfpref 15/15
  generation calls — **100%, no shortfall**; join failures 0.
- Extra join-integrity checks (beyond the frozen minimum, all PASS):
  item_seed_source components == metadata for 741/741 rows; correct_slot/longer_slot
  order rules 0 violations; served prompt answers byte-match the items payloads
  624/624 pairwise rows (anchoring: candidate text containment verified); verdicts
  re-extracted from `raw_response_text` with an independent strict parser — 0
  disagreements with the logged `parsed` verdicts; 0 retry-superseded rows.

## Determinism proof (§4.3)

Three runs, identical script + inputs; only `envelope.created_utc` is wall-clock:

- run 1 (deliverable) sha256 `8d8208cf…b7ef7`; run 2 (fresh wall-clock) sha256
  `16bd0282…069800`; run 3 (wall-clock pinned to run 1) sha256 identical to run 1 —
  `cmp` byte-identical.
- run 1 vs run 2 diff = exactly one line (`created_utc`).
- created_utc-normalized sha256 of all three runs:
  `2d42a99959bd28cdc1fc7a49885f5a0c670f4a69ee81a1a556b98123dde0452b`.

Documented interpretation (§3 ↔ §4.3 tension): §3's row field `computed_utc` would
be wall-clock, but §4.3 forbids wall-clock inside the payload. Resolved WITHOUT
touching the frozen doc: per-row `computed_utc` is pinned to the verbatim log's
final call timestamp (2026-09-15T17:58:54Z, data-derived and deterministic);
wall-clock appears only in `envelope.created_utc`. Flagged in OPEN-ISSUES.md for
the owner to ratify or amend.

## Findings

**F-1 (MAJOR, provenance; no coefficient impact) — battery-spec.json items sha
pins: hash-basis units mismatch.** Its envelope `inputs` and
`payload.classes[*].items_payload_sha256` carry hashes that do NOT match the
whole files (e.g. verbosity pin `8ac486f1…` vs whole-file `1a71b18c…`), while the
Day-2 run consumed the current files (all seven runner summaries record the
current whole-file shas). ISSUE-009's "battery-spec input hashes re-pinned" had
not documented this basis. Evidence the derivation joins the right payloads:
624/624 served pairwise prompts byte-match the current payloads.

> **RESOLUTION 2026-09-16 (derivation review; ISSUE-010 closed):** the F-1
> "stale pins" reading above is superseded. Full census confirmed the pins are
> PAYLOAD-SCOPE canonical-JSON hashes of the CURRENT payloads — all 5 reproduce
> as `sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False))`. The pins
> were never stale; the units (payload-scope vs whole-file) were undocumented.
> Resolved by a dated hash-basis note in battery-spec.json `payload.method`
> (file sha `41e9c145…` → see COEF-METHOD Amendment 2026-09-16 for the new sha)
> and a COEF-METHOD amendment. The F-1 text inside judge-corrections.json is
> superseded — consumers read OPEN-ISSUES.md / this file (R-3; the artifact
> itself stays byte-identical).

Recorded in `OPEN-ISSUES.md` (ISSUE-010, 2026-09-15).

## Honest-usage reminders (§3)

Coefficients ship `significant: false` where the 95% CI touches zero (glm/qwen
cbw, all three anchoring rows): consumers MAY NOT claim a correction from those.
Every number above is measured from the verbatim log; nothing is taken from the
runner summaries and nothing is estimated.
