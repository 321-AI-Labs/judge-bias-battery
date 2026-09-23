# REVIEW-STEWARD — P8 (data steward validation, §3 publication data contract)

- steward: DATA STEWARD (GLM) · date (UTC): 2026-09-14 · mission brief: BRIEF-20260914-sprint-p8-judge-audit.md (§4 verbatim-log rule), envelope contract per BRIEF-20260912-subagent-governance.md §3
- Validated against PRIMARY SOURCES on disk only (hashlib recomputation; model files `stat`-ed; strict JSON with NaN/Infinity rejection). Scratch: `glm/work/steward-tmp/`. No artifact under review was modified.
- Steward policy (applied uniformly across p0/p7/p8): (1) artifact_type enum extensions must be declared in provenance/notes — undeclared → metadata-only REJECT; (2) `provenance.measured_vs_estimated` required as the literal first-class field (a `measured[]`/`estimated[]` pair alone does not satisfy it); (3) full §3 envelope required on experiment artifacts.
- P8 artifact pattern (envelope + payload, hashes at payload scope) is judged CONSISTENT: battery-spec.json references items by `items_payload_sha256` = sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False)) per build_items.py, and every item file is also envelope-bearing. All five claims re-computed and matched under that declared scope (whole-file hashes would differ — payload scope is the correct reading, confirmed from build_items.py `payload_hash`).

## Per-artifact verdicts

### 1. glm/work/p8/judge-endpoints.json — REJECT (1 metadata violation; all content checks green)

- **Violation** — field path: `provenance` — observed keys: `regime`, `measured_vs_estimated`, `sample_N`. `artifact_type: "judge-endpoints"` is an enum extension and is NOT declared in provenance/notes. Required remedy: add `provenance.artifact_type_extension_note`.
- Content verified:
  - Model paths exist on disk with EXACT byte sizes as claimed: `A:\models\judges\glm-4-9b-chat-Q4_K_M.gguf` = 6,250,926,848 B (== bytes_expected == bytes_downloaded); `A:\models\judges\Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf` = 4,920,739,168 B; qwen ollama blob `sha256-a3de86cd…` = 5,225,374,496 B (== bytes_expected; no bytes_downloaded claimed). Server `A:\engines\llama-b10930-bin-win-cuda-13.3-x64\llama-server.exe` exists (9,216 B — launcher-stub-sized; endpoint liveness is independently evidenced by 33 successful logged calls, http 200).
  - `data.ports` {glm 8091, llama 8092, qwen 8093} == per-model `port` fields == ports observed in judge-calls.jsonl base_urls (and P7's live record hit 8093 for qwen3-8b). ✓
  - Full §3-style environment (os_build/driver/ram_speed_mts/engine_sha/cpu/gpu); `measured_vs_estimated` present; `created_utc` 2026-09-14T17:15:00Z ISO/past; agent non-empty; mission_brief real. ✓

### 2. glm/work/p8/bias-battery/battery-spec.json — REJECT (2 metadata violations; all content checks green)

- **Violation (a)** — field path: `envelope.provenance` — `artifact_type: "bias-battery-spec"` enum extension undeclared (observed keys: `estimated`, `measured`). Remedy: add declaration note.
- **Violation (b)** — field path: `envelope.provenance.measured_vs_estimated` — MISSING (semantics carried by `estimated[]`/`measured[]` arrays instead). Required remedy: add the literal field summarizing those lists.
- Content verified: `envelope.method.call_cap_provenance` PRESENT ✓; power sizing labeled — `payload.power_reasoning.label = "ESTIMATE"` plus `estimated: ["power reasoning", "budget math"]` ✓; counts match tasking: position 36, verbosity 24 pairs (48 entries = 24 distinct `base_item` × {plain, padded}; 48 calls), cbw 20, anchoring 12, selfpref 15 ✓; all 5 item payload hashes + `script_sha256` (build_items.py b8813b66…) recomputed and matched ✓; judges/endpoints match judge-endpoints.json ✓; `created_utc` 19:10:05Z ISO/past ✓.

### 3. glm/work/p8/bias-battery/items/*.json (5 files) — REJECT ×5 (same 2 metadata violations each; all content checks green)

Applies to each of: `anchoring-items.json` (12 items), `cbw-items.json` (20), `position-items.json` (36), `selfpref_generation-items.json` (15), `verbosity-items.json` (48 = 24 pairs).

- **Violation (a)** — field path per file: `envelope.provenance` — `artifact_type: "bias-battery-items-<class>"` enum extension undeclared. Remedy: declaration note per file (or one convention note per file, identical form).
- **Violation (b)** — field path per file: `envelope.provenance.measured_vs_estimated` — MISSING (observed: `estimated[]`, `measured[]`). Remedy: add the literal field.
- Content verified per file:
  - Each file is envelope-bearing with a COMPLETE §3 envelope otherwise (`created_utc` 2026-09-14T19:10:05Z ISO/past; agent; mission_brief real; `inputs` = current P0 lock — battery-manifest `3ac922e9…eda3` and planted-errors `3cf82e63…c402e`, BOTH re-verified against disk; environment; method with seed 20260914 + script SHA; `data.n_items` matching payload).
  - `oracle_ref` present on EVERY item (0 missing across all 131 items). All 72 group-1 refs resolve into P0 `planted-errors.jsonl` with the record's `surface_claim` embedded verbatim in the item question; all analysis/spec refs match P0 verify-results.json (oracle values incl. analysis-task-05 {count:55, form_submit}; planted/regime clause ids incl. spec-task-01 C-43 vs C-14). 0 dangling refs.
  - Pattern consistency with battery-spec: payload-hash-referenced AND envelope-bearing — consistent across all five files and the spec. ✓

### 4. glm/work/p8/bias-battery/determinism-proof.json — REJECT (2 metadata violations; all content checks green)

- **Violation (a)** — `envelope.provenance`: `artifact_type: "determinism-proof"` enum extension undeclared. **Violation (b)** — `envelope.provenance.measured_vs_estimated` MISSING (`measured[]`/`estimated[]` present). Same remedy as above.
- Content verified: `method.rule` documents the payload-scope hash (envelope timestamps excluded); both recorded runs carry identical hash sets (`match_previous: true`), and all 10 class hashes equal my independent recomputation from the on-disk payloads. Rule 3 (regenerate + hash-compare + record both) satisfied.

### 5. glm/work/p8/logs/smoke/judge-calls.jsonl — ACCEPT

- 33 lines (3 precheck + 30 smoke = 10 per judge), each strict JSON with `schema_version: 1`. 0 parse failures, no NaN/Infinity.
- BRIEF §4 verbatim fields — all present on EVERY record: prompt = `request.messages` (verbatim, incl. rubric system prompt); raw response = `raw_response_text` + `raw_response_json` (verbatim API body); parsed verdict = `parsed{ok, format_compliant, verdict{preference, confidence, reasoning_short}, schema}`; seed = `request.seed` (per-call derived; base seed 20260914 per spec — precheck uses seed 1, disclosed); temp = `request.temperature` = 0.0 on all 33; endpoint = `endpoint` + `base_url`; model = `model_id`; latency = `latency_ms`; usage = `token_usage`. Also `call_id`, `attempt`/`retry_of` (all null — no retries), `finish_reason`, `http_status`, `timestamp_utc` (ISO/past), `metadata{run_tag, bias_class, item_id, judge, schema}`, and per-record `harness.code_sha256`.
- Cross-checks: ports/models == judge-endpoints.json for all records; smoke accounting exactly 3 position items × 3 judges × 2 orders + 2 cbw items × 3 judges × 2 orders = 30, item sets == `smoke_selection` in smoke-summary.json; all 33 `parsed.ok = true`.
- Observation (not a violation): per-record `harness.code_sha256` for judge_client.py / metrics.py / run_battery.py differ from current disk — expected and correct provenance: those files changed post-smoke under REVIEW-P8 D2a/D2b fixes (documented in OPEN-ISSUES ISSUE-007/009). The log faithfully records the code that ran; the log IS the dataset and was NOT rewritten.

### 6. glm/work/p8/logs/smoke/smoke-summary.json — REJECT (2 metadata violations; all content checks green)

- **Violation (a)** — field path: `envelope.provenance` — `artifact_type: "smoke-summary"` enum extension undeclared. **Violation (b)** — `envelope.provenance.measured_vs_estimated` MISSING (`measured[]`/`estimated[]` present). Remedy: add both.
- Content verified:
  - `inputs` sha claims: `judge_call_log` 7164b72a… == disk bytes ✓; `endpoints_file` 4d09a3e2… == disk bytes ✓; `items_position` 0cb20785… / `items_cbw` 7aaa873e… do NOT match current item files — CORRECT historical provenance: the smoke consumed the pre-D1 item files (regenerated post-review per ISSUE-009/D1; run not re-run because of the day cap). The substitution is disclosed in `confound_annotations` (amended_utc 19:13:17Z) and ISSUE-009. Note for Day-2: the pre-D1 item file bytes are no longer retained on disk; continuity is established at payload level (cbw payload hash d0617a5d… unchanged; D1 touched only the 8 position SWE fix quotes + 1 verbosity twin).
  - **κ n=10 amendment PRESENT** ✓ — `notes` records AMENDED 2026-09-14T19:13:17Z (D2a: shared-cell ×3 inflation corrected, n=30 → n=10, κ values 0.5833/0.5833/1.0 recomputed from the verbatim log); `kappa_judge_pairs` all n=10. Smoke-rate honesty: "smoke results are NOT powered estimates" in `data.day1_smoke`-equivalent + estimated[] flag. ✓
  - **confound_annotations PRESENT** ✓ — pos-swe-task-05 glm observation labeled INVALID-UNTIL-DAY-2 (BLOCKER D1), scope limited, pos-spec-task-04 finding unaffected. ✓
  - cap_breach disclosed (11 vs 10 per judge, ISSUE-007) with cause and disposition. ✓

### 7. glm/work/p8/logs/smoke/smoke-config.json — REJECT (missing envelope)

- **Violation** — §3 envelope absent entirely. Observed top-level keys: `endpoints_file`, `log_path`, `items_files`, `seed`, `temperature`, `max_tokens`, `run_tag`, `summary_out`, `max_day_calls_per_judge`, `day_calls_already_used`. Missing required fields: `$.artifact_type`, `$.created_utc`, `$.agent`, `$.mission_brief`, `$.inputs`, `$.environment`, `$.method`, `$.data`, `$.provenance` (incl. `measured_vs_estimated`). Tasking explicitly requires an envelope on smoke-config.json.
- Remedy: wrap the existing flat config under a `data` (or `payload`) key with the §3 envelope around it — declare artifact_type (e.g. `"smoke-config"` with the standard extension note); values themselves are consistent with the spec and log (seed 20260914, temperature 0.0, cap 10).

## Sampled-hash / stat results (primary-source recomputation)

| claim | source | recomputed | verdict |
|---|---|---|---|
| judge-calls.jsonl sha `7164b72a…a870` | smoke-summary inputs | match | PASS |
| judge-endpoints.json sha `4d09a3e2…1651` | smoke-summary inputs | match | PASS |
| 5 item payload hashes | battery-spec envelope.inputs + payload.classes.*.items_payload_sha256 + determinism-proof | recomputed (payload scope) equal | PASS 5/5 |
| build_items.py sha `b8813b66…eb20` | battery-spec + determinism-proof + item envelopes | match | PASS |
| 3 model files + server binary | judge-endpoints.json | stat: exist, exact byte sizes | PASS 4/4 |
| ports 8091/8092/8093 | judge-endpoints data block vs per-judge entries vs log base_urls | consistent | PASS 33/33 records |
| run-time harness code SHAs (6 files) | judge-calls.jsonl records | envelope.py/parse.py/randomize.py == disk; judge_client.py/metrics.py/run_battery.py ≠ disk (post-review fixes, documented) | NOTED |

NaN/Infinity scan: clean on all 10 P8 JSON artifacts (strict parse enforced). All `created_utc` values ISO-8601 and in the past (latest amendment 19:13:17Z < current 19:4xZ).

## Summary table

| artifact | verdict | violation (exact field path) |
|---|---|---|
| judge-endpoints.json | REJECT | `provenance` lacks artifact_type-extension declaration for "judge-endpoints" |
| bias-battery/battery-spec.json | REJECT | `envelope.provenance` extension undeclared; `envelope.provenance.measured_vs_estimated` missing |
| bias-battery/items/anchoring-items.json | REJECT | `envelope.provenance` extension undeclared; `.measured_vs_estimated` missing |
| bias-battery/items/cbw-items.json | REJECT | same two |
| bias-battery/items/position-items.json | REJECT | same two |
| bias-battery/items/selfpref_generation-items.json | REJECT | same two |
| bias-battery/items/verbosity-items.json | REJECT | same two |
| bias-battery/determinism-proof.json | REJECT | `envelope.provenance` extension undeclared; `.measured_vs_estimated` missing |
| logs/smoke/judge-calls.jsonl | ACCEPT | — |
| logs/smoke/smoke-summary.json | REJECT | `envelope.provenance` extension undeclared; `.measured_vs_estimated` missing |
| logs/smoke/smoke-config.json | REJECT | entire §3 envelope missing (`$.artifact_type` … `$.provenance`) |

TOTAL: 1 accepted / 10 rejected across 11 artifacts (all rejections metadata-only; zero hash, count, oracle-ref, accounting, or verbatim-log failures)

---

## Re-validation (2026-09-14T19:5xZ, scoped to original rejections; prior ACCEPTs stand)

- **R6 battery-spec.json + items/anchoring-items.json + items/cbw-items.json + items/position-items.json + items/selfpref_generation-items.json + items/verbosity-items.json + bias-battery/determinism-proof.json + logs/smoke/smoke-summary.json — ACCEPT (8/8).** Each now carries `envelope.provenance.artifact_type_extension_note` (naming its artifact_type, citing this report) AND a literal `provenance.measured_vs_estimated` string, with the original `measured[]`/`estimated[]` arrays preserved. Payload hashes recomputed and UNCHANGED, still matching battery-spec claims: position `ffb2a875…`, verbosity `8ac486f1…`, cbw `d0617a5d…`, anchoring `4a902be9…`, selfpref_generation `a381355e…` (5/5). Counts/oracle_refs unaffected.
- **R7 logs/smoke/smoke-config.json — ACCEPT.** Now `{envelope, payload}` with all 9 envelope fields (`artifact_type` "smoke-config", `created_utc` 2026-09-14T19:46:46Z ISO/past, `agent`, `mission_brief`, `inputs`, `environment`, `method`, `data`, `provenance`); extension declared; literal `measured_vs_estimated` present. Original flat config preserved verbatim under `payload` (field-identical to the file validated at first pass: seed 20260914, temp 0.0, max_tokens 256, cap 10, day counters 1/1/1). `envelope.inputs[0]` pins the consumed flat-form sha256 `3eab2c2052248bb29d6c76175036287ddaea8c9f0e4cf9066efe78424460a669` == claim (start `3eab2c20…`); note: original flat bytes replaced by the enveloped file, so the pin is a disclosed worker claim — content identity independently verified field-by-field, and run_smoke.py documents acceptance of both forms.
- **R8 bias-battery/superseded/position-items.pre-D1.json + superseded/README.md — ACCEPT (new artifact, resolves the steward's Day-2 retention note).** File present; payload hash recomputed = `437f84e44d657d36…` == claim (`437f84e4…`); README accurately marks it as the verbatim pre-BLOCKER-D1 copy, historical, "do not judge from these". This closes the loop on smoke-summary's historical `items_position` input sha (`0cb20785…`): the D1-era payload is now retained on disk.
- **judge-endpoints.json (orchestrator-fixed) — ACCEPT (original rejection 6 cleared).** `provenance.artifact_type_extension` now present (declares "judge-endpoints" as §3 enum extension) and `measured_vs_estimated` updated (still accurate: adds "model byte sizes verified on disk" — which this steward independently re-verified at first pass). All other envelope content unchanged and still valid.
- **judge-calls.jsonl unchanged:** raw sha256 still `7164b72a…a870` — the verbatim dataset was not touched by remediation. ✓

**Re-validation TOTAL: 0 open rejections — all 10 original rejections resolved (R6×8 artifacts, R7, R8 new-artifact accept) + orchestrator artifact fixed. Final state: 12 of 12 P8 artifacts accepted.**
