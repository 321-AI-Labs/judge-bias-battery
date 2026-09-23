#!/usr/bin/env python3
"""Build the selfpref GRADING items (phase 2) from the Day-2 generation data.

Protocol (battery-spec.json class "selfpref", power_reasoning "selfpref n=15"):
each judge grades 15 own answers (scored 0-10) + 15 competitor answers
(scored 0-10), one competitor per item, rotated across the other two judges;
15 paired self-vs-other deltas per judge; blind by construction (one candidate
per prompt, role never shown; the grader's OWN wording is the stimulus under
measurement).

Inputs (fail-closed -- both shas verified before any write):
  1. logs/judge-calls.jsonl (verbatim generation log from the selfpref
     GENERATION phase; whole-file pin below; override with --log)
  2. battery/items/selfpref_generation-items.json
     (questions; payload-scope canonical-JSON pin per the ISSUE-010 basis)

Rotation rule (deterministic): items sorted by item_id, rank r (0-based);
grader J's competitor = sorted(other_two)[r % 2]. Per item each answer serves
as competitor to exactly one grader.

Output: battery/items/selfpref_grading-items.json
({"envelope", "payload": {"items": [...]}}; run_battery reads
payload["items"]). 90 items: 3 judges x 15 items x {own, competitor}.

NOTE: the shipped items/selfpref_grading-items.json was built from the
original study's generation log, which is not part of this repository.
Re-running this script requires that log and must reproduce the pinned
whole-file sha below; it is kept for provenance and reproducibility.

Determinism: fixed PAYLOAD_CREATED_UTC constant, no wall-clock reads, no RNG
(rotation is positional), sort_keys JSON. Running twice must be byte-identical.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # repo root
LOG = ROOT / "logs" / "judge-calls.jsonl"
GEN_ITEMS = ROOT / "battery" / "items" / "selfpref_generation-items.json"
OUT = ROOT / "battery" / "items" / "selfpref_grading-items.json"
JUDGES = ("glm", "llama", "qwen")

LOG_PIN = "2e1eb9a04ded4185ad3beeb3f1893a5b8b744417866f54a20bcfbb31fbb93174"
GEN_PAYLOAD_PIN = "a381355ec13801503e50be2976cb5164ce8cc8a4d0558c2b1389d795336dc954"
PAYLOAD_CREATED_UTC = "2026-09-16T17:30:00Z"  # fixed constant (determinism)
SEED = 20260914


def sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def fail(msg: str) -> None:
    sys.exit(f"FAIL-CLOSED: {msg}")


def main() -> None:
    log_sha = sha256_file(LOG)
    if log_sha != LOG_PIN:
        fail(f"generation log sha {log_sha} != pinned {LOG_PIN}")
    gen_raw = GEN_ITEMS.read_bytes()
    gen_doc = json.loads(gen_raw)
    payload_canon = hashlib.sha256(
        json.dumps(gen_doc["payload"], sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    if payload_canon != GEN_PAYLOAD_PIN:
        fail(f"generation items payload sha {payload_canon} != pinned {GEN_PAYLOAD_PIN}")

    # --- extract the 45 generation answers from the verbatim log
    answers: dict[tuple[str, str], dict] = {}
    for line in LOG.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        md = rec.get("metadata") or {}
        if md.get("bias_class") != "selfpref_generation":
            continue
        judge, iid = md.get("judge"), md.get("item_id")
        if rec.get("error"):
            fail(f"generation row {iid}/{judge} carries error {rec['error']!r}")
        if (judge, iid) in answers:
            fail(f"duplicate clean generation row for {iid}/{judge}")
        answers[(judge, iid)] = {
            "call_id": rec.get("call_id"),
            "answer": rec.get("raw_response_text"),
            "finish_reason": rec.get("finish_reason"),
        }
    if len(answers) != 45:
        fail(f"expected 45 clean generation rows, found {len(answers)}")

    # --- questions from the pinned generation items payload
    gen_items = gen_doc["payload"]["items"]
    if len(gen_items) != 15:
        fail(f"expected 15 generation items, found {len(gen_items)}")

    # --- build 90 grading items (rotation is positional, no RNG)
    ordered = sorted(gen_items, key=lambda it: it["item_id"])
    out_items = []
    for r, gi in enumerate(ordered):
        iid, question, group = gi["item_id"], gi["question"], gi.get("group")
        base = iid.replace("spgen-", "", 1)
        for j, grader in enumerate(JUDGES):
            others = sorted(x for x in JUDGES if x != grader)
            competitor = others[r % 2]
            own = answers[(grader, iid)]
            comp = answers[(competitor, iid)]
            out_items.append({
                "item_id": f"spgrade-{base}-{grader}-own",
                "group": group,
                "base_item": iid,
                "oracle_ref": gi.get("oracle_ref"),
                "question": question,
                "grader_judge": grader,
                "competitor": competitor,
                "graded_role": "own",
                "answer": own["answer"],
                "answer_source_call_id": own["call_id"],
            })
            out_items.append({
                "item_id": f"spgrade-{base}-{grader}-vs-{competitor}",
                "group": group,
                "base_item": iid,
                "oracle_ref": gi.get("oracle_ref"),
                "question": question,
                "grader_judge": grader,
                "competitor": competitor,
                "graded_role": "competitor",
                "answer": comp["answer"],
                "answer_source_call_id": comp["call_id"],
            })
    if len(out_items) != 90:
        fail(f"expected 90 grading items, built {len(out_items)}")
    ids = [it["item_id"] for it in out_items]
    if len(set(ids)) != 90:
        fail("duplicate item_ids in built payload")

    envelope = {
        "artifact_type": "bias-battery-items-selfpref_grading",
        "created_utc": PAYLOAD_CREATED_UTC,
        "agent": "GLM (Day-4 session, P8 selfpref grading phase)",
        "mission_brief": "KIMI-PROMPT-20260916-day4 (PAUSE-STATE UPDATE 4 next-step 1)",
        "inputs": {
            "repos": [],
            "scripts": [{"path": "battery/build_selfpref_grading.py",
                          "sha256": sha256_file(Path(__file__))}],
            "source_log": [{"path": str(LOG),
                             "sha256": log_sha,
                             "note": "fail-closed whole-file pin; verbatim generation answers"}],
            "source_items": [{"path": "battery/items/selfpref_generation-items.json",
                               "sha256_file": sha256_file(GEN_ITEMS),
                               "sha256_payload_canonical": payload_canon,
                               "note": "payload-scope canonical-JSON pin per ISSUE-010 basis"}],
        },
        "environment": {"os_build": "Windows-11-10.0.26200-SP0", "python": "3.12.10",
                          "cpu": "AMD64", "stdlib_only": True},
        "method": {
            "design": "per judge: 15 own answers + 15 competitor answers, scored 0-10, "
                      "one candidate per prompt, blind (role never shown); 15 paired "
                      "self-vs-other deltas per judge",
            "rotation_rule": "items sorted by item_id, rank r; grader J's competitor = "
                             "sorted(other_two)[r % 2]; per item each answer serves as "
                             "competitor to exactly one grader",
            "join_rule": "log rows joined on metadata (judge, item_id); answers are the "
                          "verbatim raw_response_text of the Day-2 generation calls",
            "grader_routing": "items carry grader_judge; run_battery dispatches each item "
                              "to exactly that judge (no cross-product)",
        },
        "provenance": {
            "regime": "measurement (judge self-preference, scored grading)",
            "measured_vs_estimated": "measured: questions and answers are verbatim pinned "
                                      "sources; no estimated values in the payload",
            "sample_N": len(out_items),
        },
    }
    payload = {
        "construction_provenance": {
            "questions": "verbatim from selfpref_generation-items.json payload (pinned)",
            "answers": "verbatim raw_response_text from the pinned Day-2 generation log",
            "rotation": envelope["method"]["rotation_rule"],
            "blinding": "scored grading of one unlabeled candidate per call; the payload's "
                        "graded_role/grader_judge/competitor fields are metadata for the "
                        "derivation only and never enter any prompt",
        },
        "ground_truth_source": gen_doc["payload"].get("ground_truth_source"),
        "payload_created_utc": PAYLOAD_CREATED_UTC,
        "seed": SEED,
        "n_items": len(out_items),
        "items": out_items,
    }
    doc = {"envelope": envelope, "payload": payload}
    OUT.write_text(json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    print(f"wrote {OUT}")
    print("items:", len(out_items))
    print("file sha256:", sha256_file(OUT))
    print("payload-canonical sha256:",
          hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False)
                         .encode("utf-8")).hexdigest())


if __name__ == "__main__":
    main()
