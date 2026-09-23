# REVIEW — judge-corrections.json derivation (P8)

Adversarial reviewer (artifacts-only context) · 2026-09-16 · subject:
`judge-corrections.json` (sha `8d8208cf…b7ef7`) + `harness/derive-corrections.py`
(sha `443a3350…0f722`) + DERIVATION-NOTES.md, re-derived independently from the
verbatim log `logs/battery-day2/judge-calls.jsonl` (741 calls).

## VERDICT: APPROVE-WITH-FIXES

The derivation is correct, deterministic, and faithful to the frozen method
(`COEF-METHOD.md`, sha `18e27702…`, verified unmodified). Every load-bearing
number was independently reproduced from the raw log with reviewer-authored
code (`review-tmp/coef-review/derive_review.py`; zero imports from the
harness):

- All 12 coefficients, 24 CI endpoints, n, tie rates, `significant` flags,
  per-item flip lists, aux rates and anchoring means match to full printed
  precision (claim-check table in `review-tmp/coef-review/FINDINGS-DERIVATION.md`).
- Reconciliation exact: 741 = 216 position + 288 verbosity + 120 cbw +
  72 anchoring + 45 selfpref_generation; 0 errors, 0 retries, 0 parse
  failures in scored classes, 0 join failures, 100% per-judge coverage
  (72/96/40/24/15).
- Determinism proven by this reviewer: 4 fresh-process re-runs of
  `derive-corrections.py derive` are byte-identical to each other and to the
  deliverable after normalizing the single `envelope.created_utc` line
  (delivered with `--crosscheck-file logs/battery-day2/summary-recompute-diff.json`).
- COEF-METHOD honored: verbatim-log-only derivation (summaries recomputed
  separately, `summary-recompute-diff.json` verified, 0 diffs); bootstrap seed
  convention reproduced exactly; zero ties confirmed by full census of 624
  pairwise verdicts plus raw-response spot-checks; selfpref correctly
  generation-only (45 calls, parsed=null, no coefficient); FT8 no-exclusion
  rule confirmed against `FT8-DISPOSITION.json` (0/247 glm calls overlap the
  foreign window).
- ISSUE-010 checklist item 6 CONFIRMED beyond the required sample: (a) all 5
  battery-spec `items_payload_sha256` pins reproduce exactly as payload-scope
  hashes — sha256 of the sort_keys canonical JSON of the CURRENT items files'
  payloads (position `ffb2a875`, verbosity `8ac486f1`, cbw `d0617a5d`,
  anchoring `4a902be9`, selfpref `a381355e`); (b) 624/624 served pairwise
  prompts (position + verbosity + cbw; anchoring containment 72/72)
  byte-match the CURRENT payloads; (c) all 7 runner summaries' file-scope
  items shas match the current files. The pins are content-current: the
  worker's F-1 "staleness" claim is disproven, the owner's units-mismatch
  ruling is correct, and the MAJOR → MINOR/documentation downgrade is
  appropriate (provenance intact, zero coefficient impact).

## Fixes required (documentation-only; owner/worker scope — outside this
reviewer's write mandate)

1. **R-1 (MINOR):** DERIVATION-NOTES.md pins a stale script sha
   (`ffb07712…`); the actual script — as pinned by the artifact's own
   envelope and by `derivation-log.txt` — is `443a3350…`. Correct the one
   line in the notes. The artifact's provenance chain itself is consistent;
   no regeneration is required for this item.
2. **R-2 (MINOR, open action):** Append the dated hash-basis note to
   `bias-battery/battery-spec.json` (payload-scope pins, canonical
   serialization) as resolved in ISSUE-010 — confirmation it was waiting on
   is hereby given. Note: appending changes battery-spec's file sha
   (`41e9c145…`), which COEF-METHOD §1 pins; record the new sha via a dated
   amendment section in COEF-METHOD in the same pass (no in-place edits of
   frozen text).
3. **R-3 (NOTE, recommended):** `judge-corrections.json` still embeds the
   superseded F-1 text ("stale pins", MAJOR). Either regenerate
   deterministically with the corrected finding text or point consumers to
   OPEN-ISSUES.md; non-blocking.

Non-regression evidence: none of the fixes touch derivation arithmetic; the
reference for any future regeneration is this reviewer's independent
re-derivation (identical to the shipped artifact) and the byte-stable replay
(determinism re-proven 2026-09-16 in `review-tmp/coef-review/coef-run-*.json`).

Substantive numbers stand as shipped: llama flips 50% of position pairs, glm
25%, qwen 17% (all significant); padded-answer preference significant for all
three judges (25%/42%/15%); cbw wrong-preference significant only for llama
(7.5%); anchoring drift non-significant for all judges at n=12. glm/qwen cbw
and all anchoring rows ship `significant: false` — consumers MAY NOT claim
corrections from them (§3 honest rule, verified applied).
