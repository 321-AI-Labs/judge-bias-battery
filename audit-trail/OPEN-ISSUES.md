# P8 OPEN ISSUES — Day 1 (WORKER-2)

Status log per mission escalation rules. Newest last.

---

## ISSUE-001 — P0 battery-manifest.json not yet present (dependency wait)

- **Severity:** WATCH (escalates to BLOCKER if it does not land within the
  polled 30-min window and blocks battery construction + smoke)
- **Detail:** Mission dependency `glm/work/p0/battery/battery-manifest.json`
  is being built concurrently by the P0 worker. At Day-1 start the directory
  `glm/work/p0/battery/` did not exist; polling with 20 s backoff started
  2026-09-14 ~17:24 UTC (30-min window). No protocol deviation — harness and
  infra work proceed in parallel.
- **Action:** continue polling; build harness + endpoints integration while
  waiting.

## ISSUE-002 — Endpoints came up staggered; llama-3.1-8B slow to serve

- **Severity:** RESOLVED (was WATCH)
- **Detail:** At first health probe: qwen (8093) and glm (8091) healthy,
  llama (8092) not answering. All three confirmed healthy by 17:26 UTC
  (GET /v1/models == 200). No BLOCKER required; smoke runs on all 3 judges.
- **Action:** none.

## ISSUE-003 — Judge latency on CPU is high (glm up to ~178 s/call cold)

- **Severity:** NOTE (affects Day-2/3 budget, not Day-1 deliverables)
- **Detail:** Precheck call latencies: glm 178.5 s (cold start), llama 40.7 s,
  qwen 49.6 s. Sequential full-battery execution would be slow; harness
  supports parallel per-judge threads (used by run_smoke.py), and callers
  should size Day-2 runs accordingly (see battery-spec.json budget section).
- **Action:** Day-2 runner should keep parallel-per-judge execution and
  max_tokens tight (verdicts are one JSON line).

## ISSUE-004 — RESOLVED: P0 battery-manifest.json landed at 18:13 UTC

- **Severity:** RESOLVED (was ISSUE-001 WATCH)
- **Detail:** Poller picked up `glm/work/p0/battery/battery-manifest.json` at
  18:13:25 UTC (build had been in progress since ~17:40; total wait ~50 min
  from first poll — slightly beyond the ~30-min window, but the dependency
  was demonstrably in flight, so no escalation was needed and no protocol
  deviation occurred). Battery: 30 tasks, all 4 groups present, SHA-locked.

## ISSUE-005 — P0 oracle field names differ from manifest summary wording

- **Severity:** RESOLVED (adapter fix)
- **Detail:** Manifest `oracle_summary` phrases spec oracles as a
  "contradicting clause ids (C-14, C-43)" pair, but the actual oracle.json
  schema is `planted_clause` + `regime_clauses[]`. Also analysis-task-05's
  `value` is a compound object ({count, event_type}), not a scalar. The P8
  item builder (`bias-battery/build_items.py`) reads the real schema.
  No P0 change requested.

## ISSUE-006 — Verbosity padding overshoot on short answers (fixed pre-run)

- **Severity:** RESOLVED
- **Detail:** First padding implementation let whole filler blocks overshoot
  (max 3.81x on the shortest answer). Changed to block-count computation;
  after fix the plain->padded length ratio is 2.07x-3.18x (median 2.51x),
  matching the ~2-3x spec. Payload hash changed; determinism proof was
  regenerated from two fresh runs (match_previous=true).

## ISSUE-007 — Smoke exceeded the per-judge day cap by 1 call (11 vs 10)

- **Severity:** MINOR PROTOCOL DEVIATION — recorded, data retained
- **Detail:** Mission binding rule 7 caps judge calls at <=10 per judge
  endpoint today. Actual: 11 per judge (1 precheck + 10 smoke). Cause: the
  pre-fix runner enforced the cap per bias-class invocation instead of
  cumulatively per day, and the cbw class expands to 2 items x BOTH orders
  = 4 calls (not 2 as budgeted). Observed: glm 11, llama 11, qwen 11.
- **Disposition:** NO calls deleted — the verbatim log is the dataset, and
  dropping records to "fit the cap" would be worse than the overage. The
  runner is fixed (run_battery.run_class now takes a cumulative
  `day_calls_used` accumulator; run_smoke seeds it with precheck counts).
- **ANNOTATED 2026-09-14 (post-review, REVIEW-P8.md claim 17 / D2b):** the
  original disposition sentence "offline replay confirms the corrected
  runner would have produced exactly 10/judge" was FALSE AS SHIPPED — the
  shipped cap fix double-counted (reviewer's replay of the original code
  yields 8 or 5 calls/judge depending on semantics, never 10). The
  reviewer's D2b fix (preserved in run_battery.py / run_smoke.py) makes the
  replay yield exactly 1 precheck + 6 position + 3 cbw = 10. The "exactly
  10" claim stands only under the fixed code.
- **Cap provenance (REVIEW-P8.md claim 18):** binding rule 7 (<=10 smoke
  calls/judge/day) is set by the orchestrator mission prompt, 2026-09-14;
  it does not appear in any persisted pre-review artifact. Also recorded in
  battery-spec.json envelope `method.call_cap_provenance`.

## ISSUE-008 — Smoke observations for Day 2 (not defects)

- All three judges: 100% parse rate, 100% strict format compliance, zero
  `<think>` blocks (qwen served with `--reasoning off`; parser tolerates
  them regardless).
- Position bias signal already visible at n=3: on the long spec-drift item
  (pos-spec-task-04) ALL THREE judges picked slot B in both orders — pure
  positional preference. Day-2 full run will size these properly.
- **ANNOTATED 2026-09-14 (post-review):** "On pos-swe-task-05, glm chose the
  incorrect answer in both orders (content error, not position)" — this
  observation is **CONFOUNDED by BLOCKER D1** (the item's answer_correct
  quoted a mispaired, nonsense "fix" instead of the real reference fix;
  REVIEW-P8.md §3/claim 15). Labeled **INVALID-UNTIL-DAY-2**, not deleted;
  also flagged in smoke-summary.json `confound_annotations`. The
  pos-spec-task-04 positional finding is unaffected (that item had no D1
  defect).
- CBW smoke: **0/12** wrong-answer preferences across judges (3 judges x 2
  items x 2 orders = 12 calls; the earlier "0/8" had the wrong denominator
  — reviewer D3).
- Inter-judge kappa (smoke): llama-qwen **1.0** at **n=10** shared
  (item, order) cells; glm-vs-others **0.583** at n=10 (the earlier "n=30"
  was a shared-cell x3 counting inflation — reviewer D2a; kappa values
  unchanged). smoke-summary.json recomputed accordingly.
- Warm latencies 10-34 s/call (vs 40-178 s cold) — full battery budget in
  battery-spec.json stands.

## ISSUE-009 — BLOCKER D1 fixed: SWE fix quotes were mispaired; P0 battery re-locked post-review

- **Severity:** RESOLVED (was BLOCKER, REVIEW-P8.md §3 D1)
- **Detail:** `build_items._fixed_source_diff` picked the LAST *.py in the
  manifest (usually test_core.py), so all 8 SWE position items quoted a
  nonsense "minimal correct fix" and the real P0 reference fix appeared in
  0/8. Fixed: the diff is now keyed by oracle.json `bug_file`, quoting the
  `reference_solution.patch` hunks for that file (fallback: fixed_source
  twin diff; raises loudly if neither yields changes). All item files were
  regenerated (same seeds); scripted verification: 8/8 items fully quote
  their real patch hunks, junk absent, buggy module named.
- **Determinism re-proof:** two fresh runs, identical payload hashes,
  `match_previous=true` recorded in determinism-proof.json. battery-spec
  input hashes re-pinned. Smoke NOT re-run (day-cap; recorded logs stay
  verbatim as history).
- **P0 re-lock discovered during regeneration:** between the adversarial
  review and this fix, P0 re-locked its battery (manifest sha
  441a4c2f... -> 3ac922e9..., planted-errors 92b3ed55... -> 3cf82e63...).
  Content impact on P8 items: ONLY (a) the 8 D1 SWE fix quotes and (b) one
  verbosity item (verb-errors-task-06-e4) whose answer_correct inherits a
  P0 oracle prose correction ("10 nm-class" -> "10 um-class" for the Intel
  4004). Scripted diff vs the reviewer's Day-1 copies: 8 position diffs +
  that 1 verbosity twin; cbw/anchoring/selfpref content byte-identical.
  Item provenance hashes now embed the CURRENT P0 lock. Flagged for the
  orchestrator in case the P0 lock needs to be re-verified upstream.

## E-P8-1 — Runner resume/dedup added after Day-2 coverage gap (2026-09-15)

- **Severity:** RESOLVED (harness capability gap; no protocol deviation)
- **Motivation (measured, Day-2 battery):** the Day-2 battery ran as 5
  per-class invocations with per-class caps, and the runner takes one
  items_file per invocation. verbosity (288 jobs) and cbw (120 jobs) are
  2 jobs per item, so caps sized to one job per item covered exactly half:
  **verbosity 144/288 jobs and cbw 60/120 jobs were skipped by cap**. The
  skipped subset is systematic (the seeded run order's tail), not random —
  a top-up with the same seed/run_tag would redo the same first jobs, and
  one with a different run_tag would randomly overlap. Exact 100% coverage
  requires the runner to skip already-logged jobs.
- **Change (run_battery.py):** new `load_logged_job_keys()` parses the
  existing JSONL log at startup (missing file -> empty set -> behavior
  unchanged; torn final line tolerated) and extracts each record's
  top-level `metadata.item_seed_source` — the verbatim per-call seed
  source `"{seed}|{bias_class}|{judge}|{item_id}|{condition}|{order}"` the
  runner logs for every battery call (verified present in 537/537 Day-2
  battery log lines). `run_class` takes an optional `logged_job_keys` set
  and skips jobs whose key is in it BEFORE the cap check: they consume no
  budget, don't touch `day_calls_used`, and are counted in the new summary
  field `n_skipped_already_logged` (alias `n_already_logged`). `main()`
  loads the key set once and passes it to every class invocation, so
  re-running the same config tops a capped/interrupted run up to 100%
  coverage with zero duplicate calls. Cap semantics (incl. the
  2026-09-14 reviewer day-cap fix) unchanged.
- **Audited coverage:** summaries now carry `n_jobs_total`, per-judge
  `n_jobs_by_judge` / `n_already_logged_by_judge` /
  `n_skipped_by_cap_by_judge`, satisfying per judge:
  `n_jobs_by_judge[j] = calls_made[j] + n_already_logged[j] + n_skipped_by_cap[j]`.
- **Known limitation:** a job whose ONLY log lines are failed attempts
  (e.g. server down mid-run) still counts as "already logged" and will not
  be re-dispatched on resume; the verbatim log keeps the error record. A
  future flag could restrict the key set to error-free calls if top-up of
  errored jobs is ever needed.
- **Tests:** +2 offline unit tests (mock server): capped-run top-up
  completes with zero duplicate keys and satisfies the coverage identity;
  log-absent behavior byte-identical to dedup disabled. Day-cap
  accumulator regression test (2026-09-14) unchanged and passing. Suite:
  41 tests, all green.

## ISSUE-010 — battery-spec.json pins stale (pre-D1) items payload shas (2026-09-15)

- **Severity:** MAJOR (provenance/documentation) — no impact on coefficients
- **Detail:** During the judge-corrections derivation it was found that
  `bias-battery/battery-spec.json` (sha `41e9c145…`, the file COEF-METHOD pins)
  still carries the PRE-regeneration items hashes in BOTH its envelope `inputs`
  and `payload.classes[*].items_payload_sha256` (e.g. verbosity `8ac486f1…`,
  position `ffb2a875…`). The Day-2 run consumed the regenerated post-ISSUE-009
  files: all seven runner summaries record the current shas (e.g. verbosity
  `1a71b18c…`), and the derivation's independent check found 624/624 pairwise
  prompts byte-match the current payloads. ISSUE-009's claim "battery-spec input
  hashes re-pinned" therefore did not land in battery-spec.json itself.
- **Impact:** none on `judge-corrections.json` — derivation joins the CURRENT
  payloads, which provably match the served prompts; full evidence in
  `DERIVATION-NOTES.md` and `logs/battery-day2/summary-recompute-diff.json`.
- **Action (owner):** re-pin the five items shas in battery-spec.json. Not done
  by the derivation worker (outside this task's write scope: battery-spec is a
  Day-1 artifact, and COEF-METHOD's frozen-method rule forbids silent edits to
  pinned inputs).
- **Ratification requested (same session):** to satisfy COEF-METHOD §4.3
  determinism ("no wall-clock inside the payload") while keeping §3's
  `computed_utc` row field, the derivation pins per-row `computed_utc` to the
  verbatim log's final call timestamp (2026-09-15T17:58:54Z) — data-derived and
  byte-stable; wall-clock appears only in `envelope.created_utc`. Owner may
  ratify or amend the doc; no in-place edit was made by this worker.

## ISSUE-010 RESOLUTION (2026-09-16, P8 owner ruling)
Derivation F-1 claimed battery-spec items_payload_sha256 pins were stale pre-D1 values. Forensic check: the pins are PAYLOAD-scope hashes (Worker-2's D-1 derivation basis; e.g. position ffb2a875, verbosity 8ac486f1) while the derivation's F-1 comparison used FILE-scope shas (position 8a1a3393, verbosity 1a71b18c) — a units mismatch, not staleness. Ground truth: the derivation itself verified 624/624 served prompts byte-match the CURRENT payloads, so the pins are content-current. **No re-pin needed.** Action: battery-spec payload gains a dated note documenting the hash basis (payload-scope, canonical serialization per derive-corrections.py) — appended by the derivation reviewer pass; F-1 severity reclassified MAJOR → MINOR/documentation. Await reviewer confirmation of the payload-content claim (its checklist item 6).

### ISSUE-010 fixes executed (2026-09-16, derivation worker — review APPROVE-WITH-FIXES)
Payload-content claim confirmed by reviewer full census (all 5 pins reproduce as
payload-scope canonical-JSON hashes of CURRENT payloads). Applied:
- **R-2:** dated hash-basis note inserted into battery-spec.json
  `payload.method.issue_010_resolution_2026-09-16` (pure textual insertion,
  CRLF/byte layout otherwise untouched; JSON re-validated). New battery-spec sha
  `a8bc634a741d5e769be0e9d6a8d36d15c1da197ea9cf00b7640a11c6606caa7b`
  (supersedes `41e9c145…`).
- **R-2/R-3/R-4:** COEF-METHOD.md Amendments section now carries "Amendment
  2026-09-16 — battery-spec re-sha after ISSUE-010 note" (§1 pin supersession +
  the R-3 supersession pointer: F-1 text inside judge-corrections.json is
  superseded — consumers read OPEN-ISSUES.md) and "Amendment 2026-09-16 —
  bootstrap seed notation (§2 anchoring)". Amended COEF-METHOD.md sha
  `f5ddb8de4c7013d754cd07f134e93d8a867dc2771d66fb289c1526be80d95de6`.
- **R-1:** DERIVATION-NOTES.md script sha corrected to
  `443a3350a1bc010eb166c537335f22e5102ba9067f859082231b879e53c0f722` (the
  artifact-envelope value), with a dated F-1 supersession annotation.
- Constraints verified: judge-corrections.json byte-identical at
  `8d8208cf1ca48c79c6fd618d4ab6e179563d3d3f0960d2c9ce20e0c76deb7ef7`; verbatim
  log sha unchanged `2e1eb9a0…`; REVIEW-*.md untouched.

## ISSUE-011 — selfpref grading phase opened (2026-09-16, Day-4)

- **Status:** prep complete, gated on the P2 day-2 scoring run finishing
  (window gate per the Day-4 mission: P2 done + FT-14 disposition + p2 quiet
  15 min + judges healthy; never start/stop/reconfigure judge servers).
- **Prep artifacts (offline, no judge calls):** grading items
  `items/selfpref_grading-items.json` (`ab865e75…` / payload `d67d4168…`,
  determinism ×2, built fail-closed from the pinned Day-2 log); runner class
  `selfpref_grading` in `run_battery.py` (`d2a1edcd…`; 3 changes + 2 metadata
  fields, backward-compatible); config
  `harness/runner-config-day3-selfpref-grading.json` (`cf22be6c…`; 90 calls,
  30/judge); derivation `harness/derive-selfpref.py` (`b3b6bde9…` after
  review MINOR-2 fix); COEF-METHOD dated amendment (estimator preregistered
  pre-data; doc `f5ddb8de…` → `60debf1e…`). Harness suite 44/44 green ×2.
- **Review:** adversarial reviewer APPROVE-WITH-FIXES
  (`review-grading/REVIEW-GRADING-PREP.md`): items byte-verified 90/90 vs the
  pinned log; runner diff minimal/blinding-clean; planted-drift recovery
  exact. MINOR-1 (false rotation-bijection prose) closed by erratum
  `items/selfpref_grading-items-ERRATUM.md` — payload pinned, not regenerated.
  MINOR-3 (preflight) = live-phase checklist: 3/3 healths; post-run
  `n_jobs_total == 90`, `calls_made` 30/30/30.
- **Authorized live command (from `harness/`):**
  `python run_battery.py --config runner-config-day3-selfpref-grading.json`
  — verbatim log `logs/battery-day3-selfpref-grading/judge-calls.jsonl`.
  Derivation afterwards ×2 (created_utc-normalized identity), coefficients to
  `selfpref-corrections.json`.
- **Disclosure (ISSUE-011 addendum):** the grading items payload carries
  `payload_created_utc = 2026-09-16T17:30:00Z` as a FIXED CONSTRUCTION
  CONSTANT (determinism requirement — no wall-clock reads in payloads, same
  pattern as build_items.py's Day-1 payloads); the file was actually created
  ~17:20Z, so the constant postdates creation by ~10 min. It is a
  construction parameter, not a wall-clock claim; recorded here to preempt
  the D3/D6 timestamp-ordering question.

### ISSUE-011 addendum (2026-09-16 ~19:35 EDT): GRADING RUN EXECUTED — gate opened, coefficients shipped
- **Gate re-check 19:01 EDT: PASS ×4.** (a) ALL-ARMS-DONE 6/6 (19:55:06Z); (b) the P2 side ran its own
  rejudge-driver post-arms (19:58:09Z): 6 §5 resume passes rc=0 + 3 BDI re-scores, `REJUDGE-ALL-DONE`
  21:49:10Z — the FT-14-bearing day2-p2-base resume retried the 16 llama parse failures (4 recovered,
  n_valid 104→108; 12 remain, per FT-14's anticipated reduced-n disposition); (c) p2 quiet ≥15 min
  (last write 18:45:39 EDT); (d) judges 200×3.
- **Live run 19:06-19:11 EDT** (`run_battery.py --config runner-config-day3-selfpref-grading.json`):
  90 calls, exactly 30/30/30, 0 errors, 89 parsed + 1 parse failure (llama own-row on
  spgrade-analysis-task-03: task-confusion family — answered the analysis task instead of grading;
  parser correct). Verbatim log `logs/battery-day3-selfpref-grading/judge-calls.jsonl` `b34776e9…`;
  summary `e0025c37…`.
- **Derivation** (`derive-selfpref.py`, aligned to COEF-METHOD §1 exclusion rule post-data — disclosed;
  estimator math/seeds/CI untouched): `selfpref-corrections.json` `0fcd7e85…` (created_utc-normalized
  invariant `b2b62af0…`, two runs). Coefficients (ALL significant): glm drift_sp +3.533333
  [+1.5333, +5.6667] n=15 · llama **−5.714286** [−8.1429, −3.1429] n=14 (1 disclosed exclusion) ·
  qwen +4.533333 [+2.2000, +6.8667] n=15. Llama shows strong NEGATIVE self-preference (own mean 3.79
  vs competitor 9.53). Application per artifact application_notes: own-scores-only, honest rule.
- **COEF-METHOD** amended (b) (rotation rank clarification, reviewer MINOR-3): now `ee9f7b2a…`.
  Derive script live sha `c7bd5215…` (supersession tombstone
  `harness/superseded/derive-selfpref.b3b6bde9.TOMBSTONE.md` — archive-before-edit lesson recorded as
  a failed take).
- **Review `review-grading/REVIEW-GRADING-RUN.md`: APPROVE-WITH-FIXES, zero numeric defects** —
  independent re-derivation matches 6dp/4dp; blindness 90/90 byte-exact; seeds 90/90 reproduce;
  determinism normalized-pinned reproduced; deliverable untouched by review. MINOR-1 (missing
  disclosure records) = this addendum + daily run block + register refresh; MINOR-2 (per-judge
  fail-close wording) noted for next script touch; MINOR-3 = the amendment (b) above.

## FT-14 — RESOLVED (2026-09-19, owner ruling R-20260919-1)

Owner ruling of record (`glm/drafts/OWNER-RULINGS-20260919.md`, verbatim): **"FT-14 accepted as
P2-run"** (Choice A). The 12 remaining llama parse-failure rows on `day2-p2-base`
borderline-compliance items are NOT chased; `n_valid 108/120` stands for the affected cells,
disclosed in the corrected tables and TR0 §4.6; no values imputed, no rejudge pass, no re-stamp
of the corrected tables (pins unchanged: p2 `a082a9c6…` / p3 `e121791a…` / demo `42792deb…`).
Root cause of record: llama-3.1-8b task-confusion on imperative borderline items
(`glm/work/p2/failed-takes.md`) — parse failures, not score-dependent. Effect: the Day-5 lock
condition (P8 corrections landed before P0/P2 headlines finalize) is satisfied; P0/P2 headline
numbers may lock at the 2026-09-20 freeze. P2 review chain completed same day: steward hash-only
re-check 7/7 PASS → gatekeeper PASS (LAST) → owner publication ruling signed; p2 STATUS flipped
to `publish_ready: true`. Recorded here by Kimi (orchestrator) per the ruling's apply-by.
