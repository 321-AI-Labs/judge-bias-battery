#!/usr/bin/env python3
"""Derive selfpref grading coefficients (drift_sp per judge) from the verbatim
day-3 grading log, per COEF-METHOD.md Amendment 2026-09-16 (selfpref estimator).

Inputs:
  - the verbatim grading log (passed as argv[1]; created by run_battery with
    runner-config-day3-selfpref-grading.json)
  - the grading items payload (fail-closed payload-scope canonical pin)

Every coefficient is computed FROM THE VERBATIM LOG joined to items via
metadata.item_seed_source; runner summaries are cross-checks only. Retry-
superseded attempts (a call whose attempt-2 carries retry_of = attempt-1
call_id) are dropped before intake and counted, mirroring §1 and
derive-corrections.py; remaining rows with parsed.ok=false (parse failures
and final error rows) are EXCLUDED AND COUNTED per COEF-METHOD §1 (real data
2026-09-16: 1 llama task-confusion parse failure drops one pairing, so that
judge derives on n=14 — the exclusion is reported in cross_checks and
per_judge_detail; alignment of the earlier strict 15-pair gate to the frozen
method's exclusion rule, disclosed in OPEN-ISSUES ISSUE-011; estimator math,
seeds, and CI convention untouched).
Determinism: sort_keys, fixed bootstrap seed, no wall-clock inside the
payload (per-row computed_utc pinned to the log's final call timestamp;
wall-clock only in envelope.created_utc).

Output: data/selfpref-corrections.json (§3 envelope).
Run twice; created_utc-normalized hashes must match (proof recorded in the
derivation log).
"""
from __future__ import annotations

import hashlib
import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # repo root
ITEMS = ROOT / "battery" / "items" / "selfpref_grading-items.json"
OUT = ROOT / "data" / "selfpref-corrections.json"
JUDGES = ("glm", "llama", "qwen")
SEED = 20260914
RESAMPLES = 10000
ITEMS_PAYLOAD_PIN = "d67d41682ab39457552782317c29b15adad1ad989118a583c325c4da6f6a6987"


def fail(msg: str) -> None:
    sys.exit(f"FAIL-CLOSED: {msg}")


def bootstrap_ci(deltas, judge: str):
    n = len(deltas)
    means = []
    for b in range(RESAMPLES):
        rng = random.Random(f"{SEED}|selfpref|{judge}|{b}")
        s = 0.0
        for _ in range(n):
            s += deltas[rng.randrange(n)]
        means.append(s / n)
    means.sort()
    lo = means[max(0, int(0.025 * RESAMPLES) - 1)]
    hi = means[max(0, int(0.975 * RESAMPLES) - 1)]
    return [lo, hi]


def main() -> None:
    if len(sys.argv) not in (2, 3):
        fail("usage: derive-selfpref.py <grading-log.jsonl> [output.json]")
    log_path = Path(sys.argv[1])
    out_path = Path(sys.argv[2]) if len(sys.argv) == 3 else OUT
    if not log_path.is_file():
        fail(f"log not found: {log_path}")

    raw_items = ITEMS.read_bytes()
    doc = json.loads(raw_items)
    payload = doc["payload"]
    canon = hashlib.sha256(json.dumps(payload, sort_keys=True,
                                       ensure_ascii=False).encode("utf-8")).hexdigest()
    if canon != ITEMS_PAYLOAD_PIN:
        fail(f"items payload sha {canon} != pinned {ITEMS_PAYLOAD_PIN}")
    items_by_id = {it["item_id"]: it for it in payload["items"]}

    # --- read the verbatim log; collect parsed scores per (judge, item)
    rows = []
    parse_failures = {j: 0 for j in JUDGES}   # final rows, parsed present but not ok
    error_rows = {j: 0 for j in JUDGES}       # final rows carrying rec.error
    run_tags = {j: set() for j in JUDGES}
    n_superseded = 0
    last_ts = None
    all_recs = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            fail("torn log line (must be whole-file consistent)")
        md = rec.get("metadata") or {}
        if md.get("bias_class") != "selfpref_grading":
            continue
        ts = rec.get("timestamp_utc")
        if ts and (last_ts is None or ts > last_ts):
            last_ts = ts
        all_recs.append(rec)
    # drop retry-superseded attempts (§1; same rule as derive-corrections.py
    # and metrics._final_calls): a row whose call_id is referenced by another
    # row's retry_of was superseded by its own successful/error retry.
    superseded_ids = {r.get("retry_of") for r in all_recs if r.get("retry_of")}
    for rec in all_recs:
        if rec.get("call_id") in superseded_ids:
            n_superseded += 1
            continue
        md = rec.get("metadata") or {}
        judge = md.get("judge")
        if rec.get("error"):
            error_rows[judge] = error_rows.get(judge, 0) + 1
            continue
        item_id = md.get("item_id")
        it = items_by_id.get(item_id)
        if it is None or it["grader_judge"] != judge:
            fail(f"log row does not join to items: {item_id} / {judge}")
        if md.get("run_tag"):
            run_tags.setdefault(judge, set()).add(md["run_tag"])
        parsed = rec.get("parsed") or {}
        if not parsed.get("ok"):
            parse_failures[judge] = parse_failures.get(judge, 0) + 1
            continue
        score = (parsed.get("verdict") or {}).get("score")
        if score is None:
            fail(f"parsed row without score: {item_id}")
        rows.append({"item_id": item_id, "judge": judge, "score": float(score),
                     "graded_role": md.get("graded_role"),
                     "competitor": md.get("competitor"),
                     "base_item": it["base_item"],
                     "call_id": rec.get("call_id"),
                     "item_seed_source": md.get("item_seed_source")})

    corrections, per_judge_detail = [], {}
    for judge in JUDGES:
        jr = [r for r in rows if r["judge"] == judge]
        own = {r["base_item"]: r["score"] for r in jr if r["graded_role"] == "own"}
        other = {r["base_item"]: r["score"] for r in jr if r["graded_role"] == "competitor"}
        pairs = []
        for base in sorted(set(own) & set(other)):
            pairs.append({"base_item": base, "score_own": own[base],
                          "score_competitor": other[base],
                          "delta": round(own[base] - other[base], 6),
                          "competitor_judge": next(
                              r["competitor"] for r in jr
                              if r["graded_role"] == "competitor" and r["base_item"] == base)})
        # COEF-METHOD §1: unparsed rows are excluded and counted, not fatal —
        # a parse failure on one side of a pairing drops that base_item
        # (design n=15 per judge; the shortfall is reported, not papered over).
        if len(pairs) < 14:
            fail(f"judge {judge}: only {len(pairs)} paired deltas "
                 f"(design n=15; more than one exclusion refuses certification)")
        excluded_bases = sorted((set(own) | set(other)) - (set(own) & set(other)))
        deltas = [p["delta"] for p in pairs]
        mean_delta = sum(deltas) / len(deltas)
        lo, hi = bootstrap_ci(deltas, judge)
        significant = not (lo <= 0.0 <= hi)
        corrections.append({
            "judge": judge, "class": "selfpref",
            "coefficient_name": "drift_sp", "coefficient": round(mean_delta, 6),
            "ci95": [round(lo, 6), round(hi, 6)], "n": len(pairs),
            "significant": significant,
            "run_tags": sorted(run_tags.get(judge, set())),
            "computed_utc": last_ts,
            "method_sha": hashlib.sha256(
                (ROOT / "p8/COEF-METHOD.md").read_bytes()).hexdigest(),
        })
        per_judge_detail[judge] = {
            "pairs": pairs,
            "n_excluded_pairings": 15 - len(pairs),
            "excluded_base_items": excluded_bases,
            "mean_score_own": round(sum(own.values()) / len(own), 6),
            "mean_score_competitor": round(sum(other.values()) / len(other), 6),
        }

    # --- call accounting / coverage cross-checks (COEF-METHOD §4)
    expected_calls = {j: 30 for j in JUDGES}
    made = {j: sum(1 for r in rows if r["judge"] == j) for j in JUDGES}
    failed = {j: parse_failures.get(j, 0) + error_rows.get(j, 0)
              for j in JUDGES}
    coverage = {}
    for j in JUDGES:
        n_own = sum(1 for r in rows if r["judge"] == j and r["graded_role"] == "own")
        n_comp = made[j] - n_own
        coverage[j] = {"own": n_own, "competitor": n_comp,
                       "parse_failures": failed[j],
                       "paired_deltas": None,  # filled after the pairing pass
                       "ok": made[j] + failed[j] == expected_calls[j]
                             and n_own + n_comp == made[j]}
    for corr in corrections:
        coverage[corr["judge"]]["paired_deltas"] = corr["n"]

    envelope = {
        "artifact_type": "judge-corrections",
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "agent": "GLM (Day-4 session, P8 selfpref grading derivation)",
        "mission_brief": "KIMI-PROMPT-20260916-day4 (PAUSE-STATE UPDATE 4 next-step 1)",
        "inputs": {
            "repos": [],
            "scripts": [{"path": "harness/derive-selfpref.py",
                          "sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}],
            "items_payload_sha256": canon,
            "verbatim_log": str(log_path),
            "method": "docs/COEF-METHOD.md (Amendment 2026-09-16, selfpref estimator)",
        },
        "environment": {"os_build": "Windows-11-10.0.26200-SP0", "python": "3.12.10",
                          "cpu": "AMD64", "stdlib_only": True},
        "method": {
            "statistic": "drift_sp = mean(score_own) - mean(score_competitor), 15 paired "
                         "deltas per judge; paired bootstrap 10k, seed "
                         "20260914|selfpref|<judge>|<resample>, percentile per anchoring "
                         "amendment convention",
            "exclusion_rule": "rows with parsed.ok=false excluded and counted",
            "honest_rule": "CI crossing zero -> significant=false; MAY NOT be applied",
        },
        "data": {
            "corrections": corrections,
            "cross_checks": {
                "call_accounting": {
                    "expected_calls_per_judge": expected_calls,
                    "parsed_calls_per_judge": made,
                    "parse_failures_by_judge": parse_failures,
                    "error_rows_by_judge": error_rows,
                    "parse_failures_total": sum(failed.values()),
                    "n_retry_superseded_attempts": n_superseded,
                    "rule": "per judge: parsed_calls + parse_failures + "
                            "error_rows == expected (retry-superseded attempts "
                            "excluded before intake); any shortfall also fails "
                            "coverage",
                    "verdict": "PASS" if all(
                        made[j] + failed[j] == expected_calls[j] for j in JUDGES
                    ) else "FAIL",
                },
                "coverage": {"per_judge": coverage,
                              "verdict": "PASS" if all(
                                  c["ok"] for c in coverage.values()) else "FAIL"},
                "items_payload_sha256": canon,
            },
            "application_notes": {
                "selfpref": "Where significant, self-preference correction is "
                            "s_corrected = s - drift_sp_j, applied ONLY to scores "
                            "judge j assigned to its OWN answers (graded_role=own); "
                            "never to scores of other judges' answers. CI crossing "
                            "zero ships significant=false and MAY NOT be applied "
                            "(COEF-METHOD §3 honest rule). n=15 per judge is "
                            "exploratory (battery-spec power_reasoning).",
            },
            "per_judge_detail": per_judge_detail,
        },
        "provenance": {
            "regime": "measurement (judge self-preference, scored grading, blind)",
            "measured_vs_estimated": "measured: all scores from the verbatim log; "
                                      "no estimated values",
            "sample_N": 15,
        },
    }
    out_path.write_text(json.dumps(envelope, indent=1, sort_keys=True) + "\n",
                        encoding="utf-8")
    print(f"wrote {out_path}")
    for c in corrections:
        print(f'{c["judge"]:6} drift_sp={c["coefficient"]:+.4f} '
              f'ci95=[{c["ci95"][0]:+.4f}, {c["ci95"][1]:+.4f}] n={c["n"]} '
              f'sig={c["significant"]}')
    print("cross-checks:", envelope["data"]["cross_checks"]["call_accounting"]["verdict"],
          envelope["data"]["cross_checks"]["coverage"]["verdict"])
    print("sha256:", hashlib.sha256(out_path.read_bytes()).hexdigest())


if __name__ == "__main__":
    main()
