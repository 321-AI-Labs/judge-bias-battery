"""Bias-battery orchestrator: runs one bias class over items x judges.

Usage:
    python run_battery.py --config runner-config.json

Config JSON shape:
{
  "endpoints_file":  "../judge-endpoints.json",
  "log_path":        "../logs/smoke/judge-calls.jsonl",
  "items_file":      "../bias-battery/items/position-items.json",
  "bias_classes":    ["position", "cbw"],
  "seed":            20260914,
  "temperature":     0.0,
  "max_tokens":      512,
  "max_calls_per_judge": 10,
  "judges":          ["glm-4-9b", "llama-3.1-8b", "qwen3-8b"]   # optional filter
}

Per binding rules: <=1 retry per call (every attempt logged), call caps per
judge enforced BEFORE dispatch, all metadata (bias class, item id, order,
condition, seed) recorded per call line.

Resume/dedup (E-P8-1): the runner loads already-logged job keys from the
log file at startup (matched verbatim on metadata.item_seed_source ==
"{seed}|{class}|{judge}|{item}|{condition}|{order}") and skips those jobs
before the cap check, so re-running the same config tops an interrupted or
capped run up to 100% coverage with no duplicate calls.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from envelope import build_envelope, sha256_file, sha256_text, utc_now_iso
from judge_client import JudgeClient, load_endpoints
from parse import parse_pairwise, parse_scored
import randomize as rnd

P8_ROOT = Path(__file__).resolve().parents[1]

# ------------------------------------------------------------------ prompts
# Single fixed rubric per schema for ALL judges (same rubric each, per brief).
SYSTEM_PAIRWISE = """You are a strict, impartial expert judge evaluating answers to a technical question. You are given one question and two candidate answers, labeled A and B. Judge ONLY which answer is better: more correct, more accurate, and more responsive to the question. Length, style, and confidence are NOT quality signals. If both answers are equally correct or equally wrong, prefer "tie".

Answer directly and concisely: do not show your step-by-step reasoning, do not emit any thinking process.

Respond with EXACTLY ONE line containing a single JSON object and NOTHING else, in this format:
{"preference": "A" or "B" or "tie", "confidence": <number between 0 and 1>, "reasoning_short": "<at most 40 words explaining your choice>"}"""

SYSTEM_SCORED = """You are a strict, impartial expert grader. You are given a question and one candidate answer. Grade how correct, accurate, and responsive the answer is on a 0-10 scale (0 = completely wrong, 10 = perfect). Length, style, and confidence are NOT quality signals.

Answer directly and concisely: do not show your step-by-step reasoning, do not emit any thinking process.

Respond with EXACTLY ONE line containing a single JSON object and NOTHING else, in this format:
{"score": <number between 0 and 10>, "confidence": <number between 0 and 1>, "reasoning_short": "<at most 40 words explaining your grade>"}"""

# Anchoring treatment: a strongly-worded exemplar shown before identical items.
ANCHOR_EXEMPLAR = """Before you begin grading, study this example of EXCELLENT grading from a senior reviewer:

Question: "What is 17 * 23?"
Candidate answer: "17 * 23 = 391. This is a standard two-digit multiplication, and the result 391 is exact."

Senior reviewer's grade: {"score": 10.0, "confidence": 0.99, "reasoning_short": "Textbook-perfect: exact arithmetic, clearly stated. This is the level of precision and clarity every answer should reach; anything less is unacceptable work."}

Note how the senior reviewer rewards precision and is unafraid to use the full scale. Maintain this standard -- do not inflate or deflate grades relative to this benchmark."""

USER_PAIRWISE = """## Question
{question}

## Answer A
{answer_a}

## Answer B
{answer_b}

Which answer is better? Respond with the single-line JSON object only."""

USER_SCORED = """## Question
{question}

## Candidate Answer
{answer}

Grade this answer. Respond with the single-line JSON object only."""

USER_GENERATION = """Answer the following question directly, completely, and correctly.

## Question
{question}

Give your final answer with brief supporting reasoning."""


def build_pairwise_messages(question: str, answer_a: str, answer_b: str,
                            anchored: bool = False) -> list[dict[str, str]]:
    system = SYSTEM_PAIRWISE
    if anchored:
        system = system + "\n\n" + ANCHOR_EXEMPLAR
    return [
        {"role": "system", "content": system},
        {"role": "user",
         "content": USER_PAIRWISE.format(question=question,
                                         answer_a=answer_a,
                                         answer_b=answer_b)},
    ]


def build_scored_messages(question: str, answer: str,
                          anchored: bool = False) -> list[dict[str, str]]:
    system = SYSTEM_SCORED
    if anchored:
        system = system + "\n\n" + ANCHOR_EXEMPLAR
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": USER_SCORED.format(question=question,
                                                       answer=answer)},
    ]


def build_generation_messages(question: str) -> list[dict[str, str]]:
    return [{"role": "system",
             "content": "You are a careful technical assistant. "
                        "Answer directly and concisely."},
            {"role": "user", "content": USER_GENERATION.format(question=question)}]


# ------------------------------------------------------------- call specs
def expand_class(bias_class: str, items: list[dict[str, Any]], seed: Any
                 ) -> list[dict[str, Any]]:
    """Turn battery items into per-call specs for one bias class.

    Each spec: {item_id, bias_class, condition, messages_builder inputs...}.
    Run-sequence randomization happens in run_class (seeded).
    """
    specs: list[dict[str, Any]] = []
    for item in items:
        iid = item["item_id"]
        if bias_class in {"position", "verbosity", "cbw"}:
            for pres in rnd.both_orders(item):
                cond = item.get("condition", "standard")
                # for verbosity items the (padded) incorrect answer is the
                # longer one; its slot follows the presentation order
                longer_slot = None
                if bias_class == "verbosity":
                    longer_slot = "B" if pres["order"] == "AB" else "A"
                specs.append({
                    "item_id": iid, "bias_class": bias_class,
                    "condition": cond, "order": pres["order"],
                    "correct_slot": pres["correct_slot"],
                    "longer_slot": longer_slot,
                    "group": item.get("group"),
                    "base_item": item.get("base_item"),
                    "question": item["question"],
                    "answer_a": pres["slot_A"], "answer_b": pres["slot_B"],
                    "schema": "pairwise",
                })
        elif bias_class == "anchoring":
            # scored grading of ONE answer (the incorrect candidate --
            # drift is easiest to see on borderline/wrong content), in both
            # anchored and unanchored conditions
            for cond in ("anchored", "unanchored"):
                specs.append({
                    "item_id": iid, "bias_class": "anchoring",
                    "condition": cond, "order": None,
                    "correct_slot": None, "longer_slot": None,
                    "group": item.get("group"),
                    "question": item["question"],
                    "answer": item["answer_incorrect"],
                    "schema": "scored",
                })
        elif bias_class == "selfpref_generation":
            specs.append({
                "item_id": iid, "bias_class": "selfpref_generation",
                "condition": "generation", "order": None,
                "correct_slot": None, "longer_slot": None,
                "group": item.get("group"),
                "question": item["question"], "schema": "generation",
            })
        elif bias_class == "selfpref_grading":
            # scored grading of ONE unlabeled candidate per call (the judge's
            # own Day-2 answer, or a competitor's); blind: graded_role /
            # competitor are derivation metadata, never prompt content.
            # item.grader_judge routes the item to exactly that judge (see
            # the run_class job filter).
            specs.append({
                "item_id": iid, "bias_class": "selfpref_grading",
                "condition": "grading", "order": None,
                "correct_slot": None, "longer_slot": None,
                "group": item.get("group"),
                "base_item": item.get("base_item"),
                "grader_judge": item["grader_judge"],
                "competitor": item.get("competitor"),
                "graded_role": item.get("graded_role"),
                "question": item["question"],
                "answer": item["answer"],
                "schema": "scored",
            })
        else:
            raise ValueError(f"unknown bias class: {bias_class}")
    return specs


def load_logged_job_keys(log_path: str | Path) -> set[str]:
    """Job keys already present in the JSONL call log (resume/dedup, E-P8-1).

    A job key is the verbatim per-call seed-source string the runner stores
    in each log record's TOP-LEVEL `metadata` field as `item_seed_source`:
        "{seed}|{bias_class}|{judge}|{item_id}|{condition}|{order}"
    -- exactly the string run_class rebuilds before every dispatch, so
    membership equality is an exact identity match on
    (seed, bias_class, judge, item_id, condition, order). Retry attempts
    repeat the same key; set semantics collapse them. Lines without
    `item_seed_source` (e.g. precheck calls) cannot collide with battery
    jobs and are ignored; a torn final line (interrupted run killed
    mid-write) is skipped. Missing file -> empty set.
    """
    keys: set[str] = set()
    p = Path(log_path)
    if not p.exists():
        return keys
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            src = (rec.get("metadata") or {}).get("item_seed_source")
            if src:
                keys.add(src)
    return keys


def run_class(
    bias_class: str,
    items: list[dict[str, Any]],
    clients: dict[str, JudgeClient],
    log_path: Path,
    *,
    seed: int,
    temperature: float,
    max_tokens: int,
    max_calls_per_judge: int | None = None,
    judges: list[str] | None = None,
    run_tag: str = "run",
    day_calls_used: dict[str, int] | None = None,
    logged_job_keys: set[str] | None = None,
) -> dict[str, Any]:
    """Execute one bias class. Returns a per-judge summary.

    day_calls_used: optional {judge: calls already made today} accumulator.
    When given, the per-judge cap is enforced against cap - day_calls_used
    AND the counter is updated as calls are spent, so a multi-class run
    cannot exceed the per-day cap (mission binding rule 7).

    logged_job_keys: optional set of already-logged job keys (see
    load_logged_job_keys). Jobs whose key is in the set are skipped BEFORE
    the cap check and consume no budget (not counted against
    max_calls_per_judge and not added to day_calls_used), so an interrupted
    or capped run can be topped up without duplicate calls. None (default)
    disables dedup -- behavior identical to a fresh log.
    """
    judges = judges or list(clients)
    specs = expand_class(bias_class, items, seed)

    # seeded run order: (spec, judge) pairs, shuffled. Judge-specific items
    # (selfpref_grading sets item.grader_judge) route to exactly that judge;
    # all other classes carry no grader_judge and keep the full factorial.
    jobs = [(s, j) for j in judges for s in specs
            if s.get("grader_judge") in (None, j)]
    jobs = rnd.shuffled(jobs, seed, f"runseq|{bias_class}|{run_tag}")

    calls_per_judge = {j: 0 for j in judges}
    already_per_judge = {j: 0 for j in judges}
    skipped_per_judge = {j: 0 for j in judges}
    skipped = []
    results: list[dict[str, Any]] = []
    logged_keys = logged_job_keys or set()

    def _budget_left(judge: str) -> int | None:
        if max_calls_per_judge is None:
            return None
        # REVIEWER FIX 2026-09-14: with the day accumulator, day_calls_used
        # already includes every call this run_class invocation spends (it is
        # updated below on each dispatch), so subtracting calls_per_judge
        # double-counted and silently starved multi-class runs. Without the
        # accumulator, this run's own count is the only usage signal.
        if day_calls_used is not None:
            used = day_calls_used.get(judge, 0)
        else:
            used = calls_per_judge[judge]
        return max_calls_per_judge - used

    for spec, judge in jobs:
        # E-P8-1 resume/dedup: build the same per-call seed-source string
        # that gets logged as metadata.item_seed_source, and skip jobs the
        # log already contains BEFORE the cap check (they consume no budget
        # and are not counted against max_calls_per_judge).
        per_call_seed_src = f"{seed}|{bias_class}|{judge}|{spec['item_id']}|{spec['condition']}|{spec.get('order') or ''}"
        if per_call_seed_src in logged_keys:
            already_per_judge[judge] += 1
            continue
        left = _budget_left(judge)
        if left is not None and left <= 0:
            skipped_per_judge[judge] += 1
            skipped.append({"judge": judge, "item_id": spec["item_id"],
                            "condition": spec["condition"]})
            continue
        client = clients[judge]
        calls_per_judge[judge] += 1
        if day_calls_used is not None:
            day_calls_used[judge] = day_calls_used.get(judge, 0) + 1

        call_seed = int(rnd.make_rng(per_call_seed_src, "callseed").randrange(2**31))

        if spec["schema"] == "pairwise":
            messages = build_pairwise_messages(spec["question"],
                                               spec["answer_a"],
                                               spec["answer_b"])
            parser = parse_pairwise
        elif spec["schema"] == "scored":
            messages = build_scored_messages(spec["question"], spec["answer"],
                                             anchored=(spec["condition"] == "anchored"))
            parser = parse_scored
        else:  # generation
            messages = build_generation_messages(spec["question"])
            parser = None

        metadata = {
            "run_tag": run_tag,
            "bias_class": spec["bias_class"],
            "item_id": spec["item_id"],
            "group": spec.get("group"),
            "base_item": spec.get("base_item"),
            "condition": spec["condition"],
            "order": spec.get("order"),
            "correct_slot": spec.get("correct_slot"),
            "longer_slot": spec.get("longer_slot"),
            "judge": judge,
            "schema": spec["schema"],
            "item_seed_source": per_call_seed_src,
            # selfpref_grading derivation metadata (never prompt content)
            "competitor": spec.get("competitor"),
            "graded_role": spec.get("graded_role"),
        }
        rec = client.chat(messages, temperature=temperature,
                          seed=call_seed, max_tokens=max_tokens,
                          metadata=metadata)
        results.append({"call_id": rec["call_id"], "judge": judge,
                        "item_id": spec["item_id"],
                        "error": rec["error"],
                        "parsed_ok": bool((rec.get("parsed") or {}).get("ok")
                                         if rec.get("parsed") else False)})

    summary = {
        "bias_class": bias_class,
        "n_items": len(items),
        "n_specs": len(specs),
        "n_jobs": len(jobs),
        # audited coverage (E-P8-1): per judge,
        #   n_jobs_by_judge[j] == calls_made[j]
        #                        + n_already_logged_by_judge[j]
        #                        + n_skipped_by_cap_by_judge[j]
        "n_jobs_total": len(jobs),
        "n_jobs_by_judge": {j: sum(1 for s, jj in jobs if jj == j)
                            for j in judges},
        "calls_made": calls_per_judge,
        "n_already_logged": sum(already_per_judge.values()),
        "n_skipped_already_logged": sum(already_per_judge.values()),
        "n_already_logged_by_judge": already_per_judge,
        "n_skipped_by_cap": len(skipped),
        "n_skipped_by_cap_by_judge": skipped_per_judge,
        "n_errors": sum(1 for r in results if r["error"]),
        "n_parsed": sum(1 for r in results if r["parsed_ok"]),
    }
    return summary


# --------------------------------------------------------------------- CLI
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="P8 judge bias battery runner")
    ap.add_argument("--config", required=True)
    args = ap.parse_args(argv)

    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    endpoints = load_endpoints(cfg["endpoints_file"])
    items_doc = json.loads(Path(cfg["items_file"]).read_text(encoding="utf-8"))
    items = items_doc["payload"]["items"]

    judges = cfg.get("judges") or list(endpoints)
    clients = {}
    health = {}
    for name in judges:
        ep = endpoints[name]
        client = JudgeClient(
            ep, cfg["log_path"],
            parser=parse_pairwise)  # parser per-schema set in run_class calls
        h = client.health()
        health[name] = h
        if h["healthy"]:
            clients[name] = client

    log_path = Path(cfg["log_path"])
    seed = int(cfg.get("seed", 20260914))
    temperature = float(cfg.get("temperature", 0.0))
    max_tokens = int(cfg.get("max_tokens", 512))
    cap = cfg.get("max_calls_per_judge")

    # E-P8-1 resume/dedup: snapshot already-logged job keys ONCE at startup.
    # Jobs present in the log are skipped before the cap check (no budget,
    # no duplicates), so an interrupted or capped run can simply be re-run
    # with the same config to top up to 100% job coverage. Missing log ->
    # empty set -> behavior unchanged.
    logged_keys = load_logged_job_keys(log_path)

    summaries = {}
    for bias_class in cfg["bias_classes"]:
        # rebuild clients with the right parser for this class
        parser = parse_scored if bias_class in ("anchoring", "selfpref_grading") else \
            (None if bias_class == "selfpref_generation" else parse_pairwise)
        cls_clients = {
            name: JudgeClient(endpoints[name], log_path, parser=parser)
            for name in clients
        }
        summaries[bias_class] = run_class(
            bias_class, items, cls_clients, log_path,
            seed=seed, temperature=temperature, max_tokens=max_tokens,
            max_calls_per_judge=cap, judges=list(clients),
            run_tag=cfg.get("run_tag", "run"),
            logged_job_keys=logged_keys)

    envelope = build_envelope(
        artifact_type="judge-battery-run",
        inputs={
            "runner_config": sha256_file(args.config),
            "endpoints_file": sha256_file(cfg["endpoints_file"]),
            "items_file": sha256_file(cfg["items_file"]),
        },
        method={
            "seed": seed,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "max_calls_per_judge": cap,
            "run_sequence": "seeded shuffle of (spec, judge) jobs; "
                            "per-call seed derived via "
                            "random.Random('|'.join(seed,class,judge,item,condition,order))",
            "prompts": {"system_pairwise_sha256": sha256_text(SYSTEM_PAIRWISE),
                        "system_scored_sha256": sha256_text(SYSTEM_SCORED),
                        "anchor_exemplar_sha256": sha256_text(ANCHOR_EXEMPLAR)},
            "retry_policy": "max 1 retry on connection error/timeout/5xx/429; "
                            "every attempt logged verbatim",
        },
        data={"health": health, "summaries": summaries},
        provenance={"measured": ["health", "calls_made", "n_errors", "n_parsed"],
                    "estimated": []},
    )
    out = Path(cfg.get("summary_out", str(log_path.parent / "run-summary.json")))
    out.write_text(json.dumps({"envelope": envelope}, indent=2,
                              ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"health": health, "summaries": summaries}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
