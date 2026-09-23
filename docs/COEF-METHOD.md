# COEF-METHOD — P8 judge-bias coefficient derivation (FROZEN)

> **Public-release note:** this is the frozen method document as used in the
> original study, copied verbatim except that lab-internal absolute paths were
> rewritten to this repository's layout (`logs/` = the verbatim call-log
> directory, `battery/` = the battery items directory, `data/` = derived
> coefficient artifacts). The sha256 pins below refer to the original
> lab-internal files.

**Status:** FROZEN 2026-09-16 before derivation kickoff. This document is the binding method for
`judge-corrections.json`. Changes require a new dated amendment section (never in-place edits).
**Inputs pinned:** verbatim log `logs/battery-day2/judge-calls.jsonl` (the original study's 741-call Day-2 log; not shipped in this repository);
items payloads per `battery-spec.json` (`41e9c1450571…`) class table; oracles embedded in items
(`answer_correct` / `answer_incorrect` fields); seeds: derivation bootstrap seed **20260914**
(same as MASTER_SEED; per-resample RNG `random.Random(20260914 | class | judge | resample)`).

## 1. Data basis (re-derivation rule)
Every coefficient is computed FROM THE VERBATIM LOG (`judge-calls.jsonl`), joining each call to its
item spec via `metadata.item_seed_source` → items payload. The runner's `summary-*.json` files are
CROSS-CHECKS ONLY: derivation code must not read them; a derivation/summary mismatch is itself a
reported finding. Rows with `parsed.ok = false` are excluded per class and counted (exclusion rate
reported). Top-up rows (`run_tag = battery-day2b-*`) and main rows are pooled; `run_tag` recorded
per row. FT-8 note: FT8-DISPOSITION.json ruled zero overlap with the foreign-call window — no
timing exclusions apply to the glm endpoint.

## 2. Per-class estimators (per judge j)
- **position** — coefficient `flip_rate_j` = P(verdict differs between order AB and BA on the same
  item) over items with both orders parsed. Item verdict = strict-JSON `preference` mapped to
  {A,B}; a pair flips if AB-pref ≠ BA-pref mapped to the same answer slot. CI: Wilson 95% on the
  per-judge flip count; plus exact per-item flip list.
- **verbosity** — coefficient `padded_pref_j` = P(judge prefers the padded answer) where exactly
  one of (plain, padded) is chosen per item (tie handling: ties counted separately, reported as
  `tie_rate`, excluded from the coefficient numerator but in n). Wilson 95% CI.
- **cbw** — coefficient `wrong_pref_j` = P(judge prefers the confident-but-wrong answer). Wilson 95%.
- **anchoring** — coefficient `drift_j` = mean(score_anchored) − mean(score_unanchored) over
  identical items, 0–10 scale; CI: paired bootstrap over items, 10,000 resamples, seed 20260914,
  percentile 2.5/97.5. Per-item deltas listed.
- **selfpref_generation** — Day-2 data is generation-only (grading phase Day 3+): NO coefficient
  this round. `judge-corrections.json` carries `selfpref: {status: "generation-only", n: 15}`
  until grading data exists.

## 3. Correction model (what P0/P2/P3 apply)
For each judge and class, `judge-corrections.json` carries:
`{judge, class, coefficient, ci95, n, tie_rate?, run_tags, computed_utc, method_sha}`.
Consumers correct a raw judge score `s` for class c as `s_corrected = s − coefficient_c` where the
coefficient is defined as the judge's raw bias rate (e.g. `flip_rate_j` is already the bias —
position bias correction reduces reported preference confidence, documented per class in the file's
`application_notes`). Honest rule: coefficients with CI crossing zero ship
`significant: false` and consumers MAY NOT claim a correction from them.

## 4. Cross-checks (binding)
1. Per-class totals must reconcile: calls_made(judge) == parsed + parse_failures, and summed
   parsed == the counts in the runner summaries (±0).
2. The five `summary-*.json` files are recomputed independently and diffed against derivation
   outputs; any diff = finding.
3. Determinism: re-running the derivation twice must produce byte-identical `judge-corrections.json`
   (sort keys, fixed seed; no wall-clock inside the payload — wall-clock only in the envelope).

## 5. Deliverables
`judge-corrections.json` (§3 envelope, artifact_type "judge-corrections"; shipped as `data/judge-corrections.json`) + derivation script
(`harness/derive-corrections.py`, sha256 in the envelope) + derivation log. Deadline per brief §6: Day 4/5.
Until it lands, every judge-dependent number in P0/P2/P3 stays labeled
`uncorrected — pending P8`.

## Amendments

### Amendment 2026-09-16 — battery-spec re-sha after ISSUE-010 note
battery-spec.json gained a dated hash-basis note in its payload's method block
(items sha pins are payload-scope canonical-JSON hashes of the CURRENT payloads,
not whole-file hashes; ISSUE-010 ruling confirmed by full census, 5/5 pins
reproduce). File sha: `41e9c1450571fa8228d8c44d9277e35b5b870cf300dbd450ea295b97aa1bb3d3`
→ `a8bc634a741d5e769be0e9d6a8d36d15c1da197ea9cf00b7640a11c6606caa7b`. §1 pin
updated: references above to battery-spec sha `41e9c1450571…` are superseded by
`a8bc634a741d…` (no in-place edit of §1; this amendment is the record). F-1 text
inside judge-corrections.json is superseded by the ISSUE-010 resolution —
consumers should note the issue was resolved by re-pinning the hash basis (judge-corrections.json stays byte-identical).

### Amendment 2026-09-16 — bootstrap seed notation (§2 anchoring)
Per-resample RNG seed string is `"20260914|anchoring|<judge>|<resample>"` (§2's
`20260914 | class | judge | resample` is string concatenation, resamples 0..9999),
with the harness `bootstrap_ci` percentile convention
(`stats[floor(0.025·n_boot)−1]`, `stats[ceil(0.975·n_boot)−1]`).

### Amendment 2026-09-16 — selfpref grading estimator (§2 selfpref phase 2; written BEFORE any grading data exists)

Phase 2 (grading) of the selfpref class is defined as follows, preregistered here
prior to any grading call being made:

- **Design.** Each judge grades, with the §2 scored rubric (0-10, one unlabeled
  candidate per call, blind), 15 OWN answers (its Day-2 generation output) and
  15 competitor answers; per item the competitor is chosen by deterministic
  rotation across the other two judges (items sorted by `item_id`, rank r,
  competitor = sorted(other_two)[r % 2]). 30 calls per judge, 90 total. The
  grader never sees its own role or the competitor identity in any prompt.
- **Coefficient.** `drift_sp_j` = mean(score_own) − mean(score_competitor) over
  the 15 item-level paired deltas of judge j (pairing: same item, same grader).
  Positive = the judge scores its own answers higher.
- **CI.** Paired bootstrap over the 15 deltas, 10,000 resamples, per-resample
  RNG `random.Random("20260914|selfpref|<judge>|<resample>")`, percentile
  convention per the §2 anchoring amendment. n=15 per judge is exploratory
  (battery-spec power_reasoning).
- **Honest rule (§3) applies unchanged:** CI crossing zero ships
  `significant: false` and consumers MAY NOT claim a correction.
- **Application.** Where significant, self-preference correction is
  `s_corrected = s − drift_sp_j` for scores of judge j's own answers.
- **Inputs pinned (payload-scope canonical-JSON basis, ISSUE-010 convention):**
  grading items `battery/items/selfpref_grading-items.json` payload sha
  `d67d41682ab39457552782317c29b15adad1ad989118a583c325c4da6f6a6987` (built by
  `build_selfpref_grading.py` from the pinned Day-2 verbatim log `2e1eb9a0…`,
  fail-closed); runner config for the selfpref grading leg (an analogous sanitized example ships in `examples/`);
  calls verbatim-logged to a grading-phase `judge-calls.jsonl` (original study log not shipped).
- **Cross-checks (§4) apply:** call accounting 30/judge == parsed + failures;
  coverage 30/judge (15 own + 15 competitor, 0 shortfall); determinism per §4.3
  (created_utc-normalized byte-identity). Coefficients ship in
  `selfpref-corrections.json` (artifact_type `judge-corrections`) — the pinned
  `judge-corrections.json` `8d8208cf…` stays byte-identical.

### Amendment 2026-09-16 (b) — rotation rank clarification (selfpref grading; reviewer MINOR-3)
The rotation rank `r` in the selfpref estimator amendment is the rank of the base design item over
the judge's 15 design items (sorted by item_id) — i.e., the own-call and competitor-call of one
(grader, base item) share the same rotated competitor. Implemented as such; verified 0/90 violations
by REVIEW-GRADING-RUN.md.
