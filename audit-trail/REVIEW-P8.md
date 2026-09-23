# REVIEW-P8 — Adversarial review of P8 Day-1 (judge reliability audit: battery + harness + smoke)

Reviewer: adversarial reviewer (independent context; worker reasoning not
available). Method: every reported smoke metric re-derived from
`logs/smoke/judge-calls.jsonl` with reviewer-written parsers in
`review-tmp/` (never from the worker's summary); every battery item
re-derived against the P0 oracle battery by scripted checks over ALL items;
determinism re-proven from two fresh runs of the unmodified builder; one
live replay call with recorded seed/temperature. Reviewer scratch:
`review-tmp/` (deletable; no final artifact depends on it).

Date: 2026-09-14 · Verdict: **APPROVE-WITH-FIXES** (fixes applied; one
blocker escalated — see D1).

---

## 1. Claim-check table

| # | Claim (source) | Verdict | Re-derived evidence |
|---|---|---|---|
| 1 | Unit tests 37/37 pass (ISSUE-008 era, README says 36) | **CONFIRMED** (count) | Clean-process run: `Ran 37 tests ... OK`. README's "36" was stale (minor defect, fixed to 39 post-review). After reviewer +2 regression tests: 39/39 OK. |
| 2 | Determinism: builder run twice, payload hashes match (determinism-proof.json) | **CONFIRMED** | Reviewer ran the unmodified `build_items.py` twice (output redirected to `review-tmp/det-out/`). run1 hashes == run2 hashes == committed item payload hashes == determinism-proof.json == battery-spec inputs. Item files byte-identical except envelope `created_utc` (excluded by design, as the proof states). `match_previous: true` reproduced. |
| 3 | 131 battery items derive from P0 oracles with correct correct/incorrect polarity (battery-spec + item files) | **CONFIRMED with 1 BLOCKER (D1)** | All 131 items scripted-audited (item-integrity sub-table, §2). Every oracle_ref resolves in `planted-errors.jsonl` (50 errors) or a task `oracle.json`; polarity verified per class. **D1:** all 8 SWE `answer_correct` quotes a mispaired diff instead of the real fix — details §3. |
| 4 | Position items emit both orders at run time | **CONFIRMED** | `randomize.both_orders` expands AB+BA unconditionally; unit test `test_both_orders_content`; smoke log contains AB and BA records for every (item, judge) pair. |
| 5 | Verbosity padding is filler, ratio ~2–3x | **CONFIRMED** | 24 plain/padded twins: question + answer_correct identical across conditions; padded = base + filler blocks; filler introduces zero digits (no new numeric/factual content). Length ratios 2.074–3.184, median 2.47 (worker: 2.07–3.18, median 2.51 — min/max match exactly; median rounding differs trivially). |
| 6 | CBW wrong answers subtly wrong, not absurd | **CONFIRMED** | Incorrect answers verbatim-endorse the P0 planted claims (digit transpositions, exponent slips, decade-off dates, swapped attributions — P0-designed subtlety); `corrected_claim` never leaks into `answer_incorrect` (checked all 20). |
| 7 | Anchoring pairs identical except the rubric exemplar | **CONFIRMED** | 12 anchoring items byte-match their source items (question, answer_incorrect, oracle_ref) from cbw/position pools; conditions differ only by `ANCHOR_EXEMPLAR` appended to the system prompt (code + unit test `test_expand_classes`). |
| 8 | Smoke: 100% parse rate, 100% format compliance, 0 think blocks (smoke-summary) | **CONFIRMED** | Independent parser: per judge n=10 smoke calls, 10 parsed, 10 compliant, 0 think blocks; all 30 finish_reason=stop; all temp 0.0. |
| 9 | Position flip rate 1/3 per judge; first-slot preference 1/3 (smoke-summary) | **CONFIRMED** | 9 (judge,item) pairs; strict flips = 3 (one per judge — all on pos-spec-task-04); flip_rate_strict = flip_rate_choice = 0.3333 per judge; first-slot 6/18 = 0.333. |
| 10 | CBW smoke: 0 wrong-answer preferences (smoke-summary per judge n=4, rate 0.0) | **CONFIRMED** | 12 cbw calls, 0 wrong prefs. NOTE: OPEN-ISSUES ISSUE-008 prose says "0/8" — wrong denominator (actual 0/12). Doc defect D3. |
| 11 | Kappa: llama–qwen 1.0; glm–others 0.58, n=30 (smoke-summary, ISSUE-008) | **CONFIRMED values / REFUTED n** | Independent math at shared (item, order) cells: po=0.8, pe=0.52 → κ=0.58333 (glm–llama, glm–qwen); po=1.0 → κ=1.0 (llama–qwen). True n=**10**, not 30: worker's `pairwise_kappa_from_log` counted each shared cell once per third judge (uniform ×3 inflation; κ value unaffected). Defect D2 — fixed in code; recorded summary n=30 escalated for correction. |
| 12 | Warm latency 10–34 s/call (ISSUE-008) | **CONFIRMED** | Smoke-call latencies: glm 17.0–23.5 s, llama 15.7–22.4 s, qwen 9.6–34.5 s. Cold prechecks 178.5/40.7/49.6 s match ISSUE-003 exactly. |
| 13 | Every log line carries verbatim prompt, raw response, parsed verdict, finish reason, usage, latency, seed, temp, endpoint, model, harness sha (brief §4) | **CONFIRMED** | All 33 lines field-complete (reviewer checklist incl. `request.messages`, `raw_response_text`, `raw_response_json`, `parsed.verdict`, `finish_reason`, `token_usage`, `latency_ms`, `request.seed/temperature`, `endpoint/base_url/model_id`, `harness.code_sha256` for 6 modules). Precheck lines lack order/correct_slot/condition metadata (not battery items — acceptable). |
| 14 | All three judges chose slot B in both orders on pos-spec-task-04 (ISSUE-008) | **CONFIRMED (raw-quoted)** | §4 quotes: 6/6 calls pref=B (AB: correct_slot=A → content wrong; BA: correct). Pure positional preference. |
| 15 | glm preferred the incorrect SWE diagnosis in both orders on pos-swe-task-05 (ISSUE-008) | **CONFIRMED, but CONFOUNDED (D1)** | Raw-quoted in §4: glm AB→B, BA→A, both = incorrect content. But D1 corrupted that item's `answer_correct` (nonsense "fix" quote); glm's preference may be a *correct* response to a corrupted anchor. llama/qwen chose correct in both orders (the exact kappa mismatch cells). |
| 16 | Cap: 11 calls/judge (1 precheck + 10 smoke) vs ≤10 (ISSUE-007) | **CONFIRMED** | Raw counts from the log: glm/llama/qwen each 11. `smoke-summary.cap_breach` accurate (observed 11, cause, disposition). |
| 17 | "Offline replay confirms the corrected runner would have produced exactly 10/judge: 1 precheck + 6 position + 3 cbw" (ISSUE-007, smoke-summary.cap_breach.disposition) | **REFUTED as shipped / TRUE after reviewer fix** | Committed fix still double-counted: `_budget_left = cap − day_used − calls_this_class` while `day_used` already includes this class's calls. Replay of committed code: 5 position + 2 cbw + 1 = 8 (direct-cap semantics) or 4 + 0 + 1 = 5 (run_smoke's pre-decremented `remaining` semantics) — never 10. Reviewer fixed both (D2); replay now yields exactly 1+6+3=10. Recorded claim text escalated for annotation. |
| 18 | Binding rule "≤10 judge calls per judge endpoint/day" (ISSUE-007 provenance) | **UNVERIFIABLE** | Rule cited as "mission binding rule 7" but no persisted artifact contains it (brief has no numbered rules; not in glm/drafts or kimi/daily). Worker's behavior is internally consistent and conservative. Provenance gap noted for the orchestrator. |
| 19 | Protocol §4 rules implemented (verbatim logging, seeded randomization recorded, per-class configs, same rubric across judges, retry policy logged, format-failure recorded not dropped) | **CONFIRMED** | run_battery/run_smoke/judge_client reviewed: seeded run-sequence shuffle + per-call `item_seed_source` recorded; `smoke_selection` reproduces exactly from seed; single module-level rubric prompt per schema (prompt SHAs in summary method); ≤1 retry, every attempt logged, policy in envelope `method.retry_policy`; tolerant parser + strict compliance flag — a low-compliance judge is reported, never dropped. |
| 20 | Envelope §3 fields + measured/estimated provenance everywhere; power labeled ESTIMATE | **CONFIRMED** | battery-spec.json, smoke-summary.json, all 5 item files carry artifact_type/created_utc/agent/mission_brief/inputs+sha256/environment/method/data/provenance; `power_reasoning.label = ESTIMATE`; smoke provenance lists all rates as estimated. |
| 21 | Harness model ids/aliases/ports match judge-endpoints.json | **CONFIRMED** | Log lines: 127.0.0.1:8091 glm-4-9b-chat, 8092 Meta-Llama-3.1-8B-Instruct, 8093 qwen3-8b. |
| 22 | SHAs table (review-tmp/p8-day1-artifact-shas.json) matches files | **CONFIRMED at review start** | 22/22 recomputed, zero mismatches. Table refreshed post-fix; original→new shas preserved in `review-tmp/reviewer-sha-changelog.md`. |
| 23 | Reproducibility of recorded calls (seed/temperature) | **CONFIRMED** | 1 live replay (llama, pos-errors-task-03-e5 BA, seed 285412326, temp 0.0): byte-identical `raw_response_text`. All 30 smoke per-call seeds independently reproduce from the recorded derivation strings (0/30 mismatches). |

---

## 2. Item-integrity sub-table (ALL items audited, no sampling)

| Class | Claimed n | Audited n | Oracle refs resolve | Polarity (correct/incorrect) | Defects |
|---|---|---|---|---|---|
| position | 36 | 36 (18 planted-error + 5 analysis + 5 spec + 8 swe) | 36/36 | 28/28 non-SWE clean; 8 SWE correct answers carry D1 (bug file/type named correctly; real fix text absent) | D1 (8 items) |
| verbosity | 48 items = 24 pairs | 24/24 pairs | 24/24 | polarity inherits verified source items; twins identical except filler | none |
| cbw | 20 | 20 | 20/20 | 20/20 (correct = rejection + corrected_claim + why_wrong; incorrect = verbatim endorsement; no truth leak) | none |
| anchoring | 12 | 12 | 12/12 | byte-identical to source cbw/position items (6 cbw + 5 analysis + 1 spec) | none |
| selfpref_generation | 15 | 15 (5 analysis + 10 swe) | 15/15 | questions embed P0 oracle question / task README; grading phase Day 2 | none |

Cross-class reuse (2 planted errors shared between position and verbosity
sets) is disclosed in `construction_provenance.cross_class_reuse` and
verified. Recorded P0 manifest/planted-errors SHAs match the live P0 files.
Determinism proof re-verified from two fresh reviewer runs (see claim 2).

---

## 3. Defects

### Blockers (escalated — never patched around)

**D1 — SWE `answer_correct` corrupted by mispaired fix diff (8 position items).**
`build_items._fixed_source_diff` selects the LAST non-fixed `.py` and LAST
`fixed_source/*.py` from the manifest file list. For every SWE task the last
non-fixed file is `test_core.py` (or another sibling module), so the diff
quoted as "the minimal correct fix" is test-file header lines vs the fixed
module's docstring — e.g. pos-swe-task-01: `-"Tests for pagination.paginate
(run: python -m unittest -v)." ; + "Pagination utilities for the reader
app."`. The REAL fix (`reference_solution.patch`, e.g. task-05:
`config[key] = config[key]` → `if key not in config: raise ConfigError(...)`)
appears in **0/8** correct answers (scripted check). Impact: 8/36 position
items have a partially incoherent "correct" anchor → 48 planned Day-2 calls
at risk; smoke headline (b) on pos-swe-task-05 is confounded (glm's "content
error" may be correct judgment on a corrupted anchor).
**Addressee:** WORKER-2 — patch `_fixed_source_diff` to diff the bug file
against its own `fixed_source/` twin (or quote `reference_solution.patch`
directly), regenerate items (determinism proof must be re-run), and re-pin
SHAs. Orchestrator: battery item content is Day-2-anchored, so this is an
owner-visible change; judge-corrections must not be computed over the
affected items until regenerated.

### Minor defects — fixes applied directly (non-regression proven)

**D2a — metrics.py kappa denominator inflation (fixed).**
`pairwise_kappa_from_log` counted each shared (item, order) cell once per
judge-key present (×3 with 3 judges): reported n=30 for true n=10. κ values
unaffected (0.5833/0.5833/1.0 independently confirmed at n=10).
*Fix:* iterate unique (item, order) keys. *Non-regression:* new unit test
`test_kappa_shared_cells_counted_once` (n=4 for 2×2×3 judges); full suite
39/39 OK; recomputed summary on the real smoke log → identical κ values,
n=10.

**D2b — day-cap double-count in the cap fix (fixed).**
`run_battery._budget_left` subtracted both the day accumulator (which
already includes the current class's spent calls) and `calls_per_judge`;
`run_smoke` additionally passed a pre-decremented `remaining` as the cap.
Replay of committed code: 5+2+1=8 or 4+0+1=5 calls/judge — the recorded
"offline replay confirms exactly 10/judge" was not true of shipped code.
*Fix:* `_budget_left = cap − used` (accumulator if present, else this run's
count); `run_smoke` passes the day cap. *Non-regression:* new unit test
`test_day_cap_cumulative_across_classes` (position 6 + cbw 3, day_used=10);
original cap test (`test_position_run_with_cap`: 3 calls, 2 skipped)
unchanged; suite 39/39 OK; replay scenario A still reproduces the recorded
11-call breach, scenario B now yields exactly 1 precheck + 6 position + 3
cbw = 10.

**D4 — stale README test count (fixed).** README said "36 offline unit
tests"; actual was 37 at review start, 39 post-fix. Updated to 39 with a
note.

### Minor defects — escalated (recorded artifacts / item semantics; NOT touched by reviewer)

- **D3 (WORKER-2):** OPEN-ISSUES ISSUE-008: "CBW smoke: 0/8 wrong-answer
  preferences" → actual 0/**12** (raw log); same issue's "kappa (smoke,
  n=30)" → true n=**10**. smoke-summary.json `kappa_judge_pairs[*].n: 30` →
  recompute with fixed metrics and amend with a dated note (the file is
  already amend-tracked). ISSUE-007's disposition sentence ("offline replay
  confirms exactly 10/judge") was false as shipped — true only after the
  reviewer's D2b fix; annotate.
- **D5 (WORKER-2):** battery-spec payload `classes.selfpref.items_file`
  says `items/selfpref-items.json`; the actual file is
  `items/selfpref_generation-items.json` (envelope inputs list the correct
  name). One-line fix + regenerate spec; Day-2 runner would fail fast on the
  current pointer.
- **Labeling note (no action):** smoke-summary `n_records_total: 30` is
  run_tag-filtered; the raw log holds 33 lines incl. 3 prechecks.
- **Provenance (orchestrator):** the "binding rule 7" cap (≤10 calls/judge/
  day) exists only in the unpersisted mission chat; persist the mission
  rules if the cap is to be auditable.

---

## 4. Headline smoke evidence (raw log quotes)

**(a) pos-spec-task-04 — all three judges, both orders picked slot B:**

```
[llama|AB] pref=B correct_slot=A  raw: '{"preference": "B", ... "Answer B correctly identifies no internal contradictions, while Answer A incorrectly identifies a con...'
[llama|BA] pref=B correct_slot=B  raw: '{"preference": "B", ... "Answer B correctly identifies a conflict between clauses C-50 and C-10."}'
[glm|AB]   pref=B correct_slot=A  raw: '{"preference": "B", "confidence": 1, ... "Answer B correctly identifies no internal contradictions in the specification."}'
[glm|BA]   pref=B correct_slot=B  raw: '{"preference": "B", "confidence": 1, ... "Answer B identifies a clear contradiction between C-50 and C-10."}'
[qwen|AB]  pref=B correct_slot=A  raw: '{"preference": "B", ... "Answer B correctly identifies C-50 and C-10 as non-conflicting...'
[qwen|BA]  pref=B correct_slot=B  raw: '{"preference": "B", ... "Answer B correctly identifies the contradiction between C-10 and C-50..."'
```
Pure positional preference; this is the flip counted in all 3 judges' 1/3
flip rate.

**(b) pos-swe-task-05 — glm chose incorrect in both orders (D1-confounded):**

```
[glm|AB] pref=B correct_slot=A (incorrect) raw: '{"preference": "B", "confidence": 1, ... "Answer B identifies the root cause in `main()` and provides a direct fix."}'
[glm|BA] pref=A correct_slot=B (incorrect) raw: '{"preference": "A", "confidence": 1, ... "Answer A identifies the root cause and provides a clear fix for `main()` in `config.py`."}'
```
glm endorsed the wrong-function hypothesis both times; the rival "correct"
answer carries the D1 garbage fix quote, so this observation must not feed
Day-2 conclusions before D1 is fixed. llama and qwen chose the correct
content in both orders (these are the only two cells where glm disagrees
with either judge — they fully account for κ=0.58 vs 1.0).

---

## 5. Fixes applied by reviewer (complete list) + SHAs

1. `harness/metrics.py` — kappa shared-cell dedup (D2a).
2. `harness/run_battery.py` — `_budget_left` double-count fix (D2b).
3. `harness/run_smoke.py` — pass day cap, not pre-decremented remainder (D2b).
4. `harness/tests/test_harness.py` — +2 regression tests (37→39; suite OK).
5. `harness/README.md` — test count 36→39 (D4).

Original→new SHAs for all five files: `review-tmp/reviewer-sha-changelog.md`;
`review-tmp/p8-day1-artifact-shas.json` refreshed to current state (all
other entries verified unchanged). Non-regression proof: full suite 39/39
OK from a clean process; every original test still passes; behavioral replay
(`review-tmp/cap_replay.py`) matches ISSUE-007's stated intent exactly.
Recorded smoke data, battery items, battery-spec.json, determinism-proof.json,
smoke-summary.json, OPEN-ISSUES.md were **not** modified by the reviewer.

Reviewer scratch (deletable): `review-tmp/{det_check.py, item_audit.py,
smoke_rederive.py, cap_replay.py, det-out/, smoke-rederived.json,
summary-recomputed-with-fixes.json, reviewer-sha-changelog.md}`.

---

## 6. Final verdict

**APPROVE-WITH-FIXES.**

The harness and smoke machinery are sound and every headline smoke number
reproduces from the raw log (parse 1.0, compliance 1.0, flips 1/3 per judge,
CBW 0 wrong prefs, κ 0.58/0.58/1.0, warm latency 9.6–34.5 s, 11-call cap
breach accurately self-reported). Item construction is deterministic and
oracle-faithful in 123/131 items. It is gated on:

1. **D1 (blocker, WORKER-2):** regenerate the 8 SWE position items with a
   correct bug-file-vs-its-fixed-twin diff before any Day-2 position run;
   quarantine the pos-swe-task-05 smoke observation.
2. **D3/D5 (WORKER-2):** amend smoke-summary κ n (30→10) and OPEN-ISSUES
   counts/claims; fix the selfpref items_file pointer.
3. Day-2 must run on the reviewer-fixed runner (cumulative day cap now
   actually enforces ≤10/judge/day as recorded).

---

## 7. Re-verification (D1 + annotations) — 2026-09-14, scoped follow-up

Scope: worker-2's BLOCKER-D1 fix + reviewer-escalated annotations only. No
full re-review. Evidence in `review-tmp/day1-items-snapshot/` (Day-1 item
copies preserved before re-running determinism), `review-tmp/det-out2/`,
`review-tmp/det-check-new.txt`. Recorded smoke data
(`judge-calls.jsonl`, sha `7164b72a…`) and the reviewer's 5 direct fixes
remain byte-identical.

| # | Claim | Verdict | Re-derived evidence |
|---|---|---|---|
| 1 | D1 fixed: fix quotes keyed by oracle `bug_file`, quoting `reference_solution.patch` hunks, loud failure instead of fabrication | **CONFIRMED** | `_fixed_source_diff` now resolves `bug_file` from oracle.json, parses patch hunks whose `--- a/<name>` matches, raises `ValueError` if no hunk/twin (never fabricates); fallback = difflib vs `fixed_source/<bug_file>`. Scripted re-derivation with an independent patch parser: **8/8** SWE position items quote exactly their real patch hunks with the buggy module + bug_type named. Manual full re-derivation of 3: pos-swe-task-01 (`range(0, len(items) - page_size, ...)` → `range(0, len(items), ...)`), pos-swe-task-05 (`config[key] = config[key]` → `if key not in config: raise ConfigError(...)`), pos-swe-task-08 (`merged={}` → `merged=None` + guard) — quoted text is verbatim the P0 patch. |
| 2 | Determinism of the fixed builder: two fresh runs == committed == proof (position `ffb2a875…`, verbosity `8ac486f1…`, cbw `d0617a5d…`, anchoring `4a902be9…`, selfpref `a381355e…`) | **CONFIRMED** | Reviewer ran the unmodified new `build_items.py` twice (output to `review-tmp/det-out2/`): run1==run2==committed payload hashes==claimed values; committed `determinism-proof.json` carries the worker's two regeneration runs (`match_previous: true`, `deterministic: true`). battery-spec input SHAs re-pinned to the new hashes. |
| 3 | Diff scope vs Day-1 items: ONLY the 8 SWE `answer_correct` fields + verb-errors-task-06-e4 prose changed | **CONFIRMED** | Field-by-field diff vs `review-tmp/day1-items-snapshot/` (payload-level, envelope timestamps excluded): position — exactly the 8 `pos-swe-task-0X` `answer_correct` fields; verbosity — exactly `verb-errors-task-06-e4` plain + padded twins' `answer_correct`; cbw/anchoring/selfpref — **zero** item-field diffs. `ground_truth_source` provenance hashes updated in all 5 classes (item 6). |
| 4 | Annotations accurate | **CONFIRMED** | (a) smoke-summary `confound_annotations`: pos-swe-task-05 glm observation **INVALID-UNTIL-DAY-2** with explicit scope note that pos-spec-task-04 is unaffected; amendment dated 19:13:17Z, raw log untouched. (b) κ amendment: recomputed n=10 per pair, values preserved 0.58333/0.58333/1.0 (po 0.8/0.8/1.0, pe 0.52); per-judge metric blocks byte-equal to the reviewer's Day-1 recomputation — amendment touched nothing else. (c) OPEN-ISSUES #008: CBW corrected to **0/12**, κ to n=10. (d) #007 annotated: original "offline replay confirms exactly 10/judge" marked FALSE-AS-SHIPPED, standing only under the reviewer's D2b fix; cap provenance recorded as orchestrator mission prompt 2026-09-14, mirrored in battery-spec `method.call_cap_provenance`. (e) `selfpref.items_file` → `items/selfpref_generation-items.json`; spec input hashes re-pinned; ESTIMATE label intact. |
| 5 | Reviewer's 5 direct fixes byte-identical; REVIEW-P8.md and judge-calls.jsonl untouched by worker | **CONFIRMED** | metrics.py `7630f8d5…`, run_battery.py `4e3d2f5d…`, run_smoke.py `daf55be7…`, test_harness.py `06988ff3…`, README.md `b37f578f…` — all match `reviewer-sha-changelog.md` "new" column exactly. `judge-calls.jsonl` sha `7164b72a…` unchanged. REVIEW-P8.md contains no worker edits (verified before appending this section). SHAs table refreshed by the worker with an explicit `_amendment` note; all 22 file entries recomputed vs current files: 0 mismatches; smoke-config.json unchanged (`3eab2c20…`). |
| 6 | ISSUE-009 (P0 re-lock inheritance): worker's recording accurate; P0 re-lock is NOT an open item (standing APPROVE post-AMENDMENT-1) | **CONFIRMED** | `glm/work/p0/REVIEW-P0-BATTERY.md` §8: re-lock AMENDMENT-1 re-verified there — hash chain intact (manifest `3ac922e9…eda3`, prereg `d560ed1b…961c`), provably minimal diff (2 oracle records, zero agent-visible byte changes), **standing verdict APPROVE post-AMENDMENT-1**. Worker's inheritance recording matches: item provenance now embeds the CURRENT P0 lock (manifest `3ac922e9…`, planted-errors `3cf82e63…` — both recomputed against the live P0 files, match); the one P8-visible content change (e4 prose "10 nm-class" → "10 µm-class") is byte-exact P0's amended `why_wrong`, present in both verbosity twins; AMENDMENT-1's other changed record (errors-task-09-e5) touched only citation fields P8 items never quote — consistent with zero diffs elsewhere. |

**Standing verdict: APPROVE-WITH-FIXES → ALL REVIEWER ESCALATIONS CLOSED.**
D1 fixed and re-verified (blocker cleared); D2a/D2b fixes stand untouched;
D3/D5 annotations landed accurately; determinism re-proven on the fixed
builder; P0 re-lock inherited cleanly and carries its own standing APPROVE.
The battery (131 items, current lock `ffb2a875…`/`8ac486f1…`/`d0617a5d…`/
`4a902be9…`/`a381355e…`) and the harness as it now stands are cleared for
the Day-2 run under the cumulative ≤10 calls/judge/day cap. Recorded Day-1
smoke data remains verbatim history, with the pos-swe-task-05 glm
observation quarantined (INVALID-UNTIL-DAY-2). No open P8 items on the P8
side; the P0 re-lock requires no P8-side action.
