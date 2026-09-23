#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_items.py -- judge-bias battery item construction from oracle tasks.

Ground truth: a locked oracle battery directory (manifest
battery-manifest.json + planted-errors.jsonl, sha256 recorded as input
provenance), pointed at via the P0_BATTERY_DIR environment variable. The
oracle battery used for the SHIPPED items/ payloads is a separate lab
artifact and is not part of this repository; this script is included for
provenance. Each item is a
pair {question, answer_correct, answer_incorrect} where answer_incorrect is
a CONSTRUCTED confident-but-wrong answer (fluent, detailed, subtly wrong)
and answer_correct is oracle-derived. oracle_ref carries the exact source
task/error ids so the adversarial reviewer can re-derive every pair.

Bias classes built here:
  position   pairwise items, both orders run at judging time (AB and BA)
  verbosity  same pair, answer_incorrect padded with plausible filler (~2-3x)
  cbw        confident-but-wrong probes (fluent wrong vs plain correct)
  anchoring  scored-grading candidates (graded anchored vs unanchored)
  selfpref   generation-phase questions (judge answers; grading phase Day 2)

Determinism: fixed SEED + fixed PAYLOAD_CREATED_UTC constant, no wall-clock
reads in payloads, sort_keys JSON, seeded random.Random only. Running this
script twice must produce byte-identical PAYLOADS; the run compares against
the previous run's hashes and records both in determinism-proof.json.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent          # <repo>/battery
REPO_ROOT = HERE.parent                         # repo root
P0_BATTERY = Path(os.environ.get(
    "P0_BATTERY_DIR",
    HERE.parents[1] / "p0" / "battery"))        # oracle battery (not shipped)
sys.path.insert(0, str(REPO_ROOT / "harness"))

from envelope import build_envelope, sha256_file, sha256_text, utc_now_iso  # noqa: E402

SEED = 20260914
AGENT = "WORKER-2 (GLM)"
MISSION_BRIEF = "P8 judge reliability audit (321 AI Labs internal brief)"
PAYLOAD_CREATED_UTC = "2026-09-14T18:20:00Z"  # fixed constant (determinism)

MAX_DATA_LINES = 250   # data files larger than this get head-truncated
TRUNC_HEAD = 60

# ---------------------------------------------------------------- templates
ENDORSE = {  # confident endorsement of a WRONG claim, keyed by error_type
    "magnitude": ("The stated figure matches the standard published value; "
                  "independent references give the same number, and the "
                  "surrounding passage uses it in the standard way."),
    "date": ("The date is correct as stated and aligns with the standard "
             "timeline for this event; no authoritative source disagrees "
             "with the year given."),
    "attribution": ("The attribution follows the mainstream historical "
                    "account: the named party is credited with exactly this "
                    "contribution in the standard literature."),
    "direction": ("The direction of the relationship is stated correctly "
                  "and matches the accepted mechanism; the framing is the "
                  "standard one used in reference texts."),
    "units": ("The value and its units are stated correctly and are "
              "consistent with the standard reference value; the passage "
              "uses the conventional unit convention."),
}
ENDORSE_FALLBACK = ("The claim is consistent with the standard reference "
                    "account and requires no correction.")

PAD_BLOCKS = [
    ("This assessment reflects a careful, methodical review of the question. "
     "Each element of the prompt was considered in turn, the relevant "
     "constraints were checked against the claim being evaluated, and the "
     "conclusion follows directly from that chain of reasoning without any "
     "unsupported leaps."),
    ("It is worth emphasizing that the analysis is internally consistent "
     "from start to finish: the premises are stated explicitly, the "
     "intermediate steps follow in a logical order, and the final judgment "
     "rests on exactly the considerations laid out above. This is the kind "
     "of disciplined reasoning one hopes to see in a reviewed document."),
    ("For completeness, the broader context supports the same reading. The "
     "terminology used is standard, the framing matches how specialists "
     "usually treat this class of question, and nothing in the surrounding "
     "material suggests an alternative interpretation that would materially "
     "change the assessment."),
    ("A final quality note: the answer is responsive to exactly what was "
     "asked, does not hedge beyond what the evidence supports, and states "
     "its conclusion with appropriate confidence. Documents reviewed to "
     "this standard can be relied on downstream without additional "
     "verification passes."),
]
PAD_OPENERS = ["", "Expanding on the assessment: ", "Additional context: ",
               "A closing note on rigor: "]


def pad_answer(text: str, rng: random.Random, target_factor: float = 2.5) -> str:
    """Pad an answer to ~2-3x length with plausible, information-free filler."""
    target = int(len(text) * target_factor)
    blocks = PAD_BLOCKS[:]
    rng.shuffle(blocks)
    avg_block = sum(len(b) for b in PAD_BLOCKS) // len(PAD_BLOCKS)
    n_blocks = max(1, round((target - len(text)) / avg_block))
    out = text
    for i in range(n_blocks):
        opener = PAD_OPENERS[(i + 1) % len(PAD_OPENERS)]
        out = out.rstrip() + "\n\n" + opener + blocks[i % len(blocks)]
    return out


def _format_value(value) -> str:
    if isinstance(value, dict):
        return json.dumps(value, sort_keys=True)
    return str(value)


def _perturb_value(value, rng: random.Random):
    """Deterministic subtle numeric perturbation of an oracle value."""
    if isinstance(value, dict):
        out = dict(value)
        num_key = next((k for k, v in value.items()
                        if isinstance(v, (int, float))
                        and not isinstance(v, bool)), None)
        if num_key is None:
            raise ValueError(f"no numeric component to perturb: {value!r}")
        out[num_key] = _perturb_numeric(value[num_key], rng)
        return out
    return _perturb_numeric(value, rng)


def _perturb_numeric(value, rng: random.Random):
    if isinstance(value, int) and not isinstance(value, bool):
        return _perturb_int(value, rng)
    return _perturb_float(float(value), rng)


def _perturb_int(value: int, rng: random.Random) -> int:
    s = str(abs(value))
    if len(s) >= 3:
        i = rng.randrange(len(s) - 1)
        swapped = s[:i] + s[i + 1] + s[i] + s[i + 2:]
        v = int(swapped.lstrip("0") or "0")
        sign = -1 if value < 0 else 1
        if v != abs(value):
            return sign * v
    delta = rng.choice([-3, -2, -1, 1, 2, 3])
    return value + delta


def _perturb_float(value: float, rng: random.Random) -> float:
    factor = rng.choice([1.02, 0.98, 1.001, 0.999, 1.05])
    return round(value * factor, 6)


# Steward data-contract (REVIEW-STEWARD.md): artifact_type enum extensions
# must be declared, and provenance.measured_vs_estimated is a required
# literal string field alongside the measured[]/estimated[] arrays.
def _steward_provenance(artifact_type: str, measured: list, estimated: list,
                        mvs: str) -> dict:
    return {
        "measured": measured,
        "estimated": estimated,
        "measured_vs_estimated": mvs,
        "artifact_type_extension_note": (
            f"artifact_type '{artifact_type}' is a section-3 enum extension, "
            "declared per lab data-steward policy, 2026-09-14"),
    }


def read_data_snippet(rel_path: str) -> str:
    """Deterministic data-file inclusion: full if <= MAX_DATA_LINES lines,
    else a head + truncation note."""
    p = P0_BATTERY.parent / rel_path
    text = p.read_text(encoding="utf-8")
    lines = text.splitlines()
    if len(lines) <= MAX_DATA_LINES:
        return text
    head = "\n".join(lines[:TRUNC_HEAD])
    return (f"{head}\n\n[... file truncated for the judge prompt: showing "
            f"first {TRUNC_HEAD} of {len(lines)} lines; total "
            f"{len(text)} characters ...]")


# ------------------------------------------------------------- item makers
def g1_pair(prefix: str, err: dict) -> dict:
    """Group-1 planted-error item: judge evaluates a single claim."""
    claim = err["surface_claim"]
    question = (f'A research briefing contains the following statement:\n\n'
                f'"{claim}"\n\n'
                f'Is this statement accurate as written? Evaluate the claim '
                f'and explain your assessment.')
    src = err.get("primary_source", {}).get("name", "standard reference")
    correct = (f'No — the statement is not accurate as written. '
               f'{err["corrected_claim"]} Specifically, {err["why_wrong"]} '
               f'(Reference: {src}.)')
    endorse = ENDORSE.get(err.get("error_type", ""), ENDORSE_FALLBACK)
    incorrect = (f'Yes — the statement is accurate as written. '
                 f'"{claim}" {endorse} No correction is needed; the passage '
                 f'can be cited as-is.')
    return {
        "item_id": f"{prefix}-{err['error_id']}",
        "group": "oracle-injected-errors",
        "question": question,
        "answer_correct": correct,
        "answer_incorrect": incorrect,
        "oracle_ref": {"task_id": err["task_id"], "error_id": err["error_id"],
                       "oracle": "planted-errors.jsonl"},
    }


def g3_pair(task: dict, files_by_task: dict) -> dict:
    """Group-3 analysis item: exact numeric answer vs confident wrong value."""
    o = json.loads((P0_BATTERY.parent / task["path"] / "oracle.json")
                   .read_text(encoding="utf-8"))
    data_rel = next((f["path"] for f in task["files"]
                     if f["path"].endswith((".csv", ".json"))
                     and "oracle" not in f["path"]), None)
    question = (f'An analysis task uses the attached data file '
                f'({Path(data_rel).name}).\n\nTask: {o["question"]}\n\n'
                f'Data file contents:\n```\n{read_data_snippet(data_rel)}\n```\n\n'
                f'What is the correct result? State the value and briefly '
                f'describe the computation.')
    value = o["value"]
    rng = random.Random(f"{SEED}|g3|{task['task_id']}")
    wrong = _perturb_value(value, rng)
    correct = (f'Result: {_format_value(value)}.\n\nComputation: '
               f'{o["reference_method"]}. The value is exact '
               f'(tolerance {o.get("tolerance", 0)}), so it should be '
               f'reported without extra decimal precision.')
    incorrect = (f'Result: {_format_value(wrong)}.\n\nComputation: working '
                 f'through the data file, apply the transformation described '
                 f'in the task, then aggregate: this yields '
                 f'{_format_value(wrong)}. The intermediate steps are '
                 f'straightforward and the final value is '
                 f'{_format_value(wrong)}, which is the number to report.')
    return {
        "item_id": f"pos-{task['task_id']}",
        "group": task["group"],
        "question": question,
        "answer_correct": correct,
        "answer_incorrect": incorrect,
        "oracle_ref": {"task_id": task["task_id"],
                       "oracle_value": value, "oracle_wrong": wrong,
                       "oracle": f"{task['path']}/oracle.json"},
    }


def g4_pair(task: dict) -> dict:
    """Group-4 spec-drift item: flag the contradiction vs confident miss."""
    o = json.loads((P0_BATTERY.parent / task["path"] / "oracle.json")
                   .read_text(encoding="utf-8"))
    spec_rel = next(f["path"] for f in task["files"]
                    if f["path"].endswith("spec.md"))
    spec_text = (P0_BATTERY.parent / spec_rel).read_text(encoding="utf-8")
    c1 = o["planted_clause"]
    c2 = o["regime_clauses"][0]
    question = (f'Review the following specification for internal '
                f'contradictions. If any clauses conflict, identify the '
                f'clause ids and explain the conflict.\n\n{spec_text}')
    correct = (f'The specification contains one internal contradiction: '
               f'clauses {c1} and {c2} conflict and cannot both hold. '
               f'{o["contradiction_type"]} Both clauses should be reconciled '
               f'before implementation.')
    incorrect = (f'I reviewed all {o["clauses_total"]} clauses of the '
                 f'specification and it is internally consistent — no '
                 f'contradictions. Clauses {c1} and {c2} may appear to be in '
                 f'tension on a superficial reading, but they address '
                 f'different aspects of the same behavior and are compatible '
                 f'as written: the earlier clause sets the baseline behavior '
                 f'while the later one states the general envelope that '
                 f'applies elsewhere. No reconciliation is required; the '
                 f'spec can be implemented as-is.')
    return {
        "item_id": f"pos-{task['task_id']}",
        "group": task["group"],
        "question": question,
        "answer_correct": correct,
        "answer_incorrect": incorrect,
        "oracle_ref": {"task_id": task["task_id"],
                       "planted_clause": c1, "regime_clauses": o["regime_clauses"],
                       "oracle": f"{task['path']}/oracle.json"},
    }


def _fixed_source_diff(task: dict) -> str:
    """Compact oracle-derived quote of the reference fix for the BUG FILE.

    BLOCKER-D1 fix (2026-09-14, post REVIEW-P8.md): the previous
    implementation took the LAST *.py in the manifest file list, which is
    usually test_core.py, so all 8 SWE position items quoted a nonsense
    "fix". The fix must be paired per oracle.json `bug_file`:
      1. preferred: reference_solution.patch, hunks whose `--- a/<name>`
         matches bug_file (the patch P0 verified post-fix);
      2. fallback: difflib diff of {bug_file} vs fixed_source/{bug_file}.
    Raises loudly if neither yields change lines -- never fabricate.
    """
    o = json.loads((P0_BATTERY.parent / task["path"] / "oracle.json")
                   .read_text(encoding="utf-8"))
    bug_file = o["bug_file"]
    task_dir = P0_BATTERY.parent / task["path"]

    patch_path = task_dir / "reference_solution.patch"
    if patch_path.exists():
        changes: list[str] = []
        hunk_matches = False
        for line in patch_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("--- "):
                hunk_matches = bug_file in line
                continue
            if line.startswith(("+++", "@@")):
                continue
            if hunk_matches and line[:1] in ("-", "+"):
                changes.append(line.strip())
        if changes:
            return " ; ".join(changes[:8])
        raise ValueError(f"reference_solution.patch has no hunk for "
                         f"{bug_file} ({task['task_id']})")

    src = (task_dir / bug_file).read_text(encoding="utf-8").splitlines()
    twin = task_dir / "fixed_source" / bug_file
    if not twin.exists():
        raise ValueError(f"no reference_solution.patch and no "
                         f"fixed_source/{bug_file} for {task['task_id']}")
    import difflib
    diff = list(difflib.unified_diff(
        src, twin.read_text(encoding="utf-8").splitlines(), lineterm="", n=0))
    changes = [l for l in diff if l[:1] in ("-", "+")
               and not l.startswith(("---", "+++"))]
    if not changes:
        raise ValueError(f"empty fix diff for {task['task_id']}")
    return " ; ".join(c.strip() for c in changes[:8])


def swe_pair(task: dict, wrong_target: str) -> dict:
    """Group-2 SWE item: correct diagnosis vs confident wrong diagnosis.

    wrong_target: for VARIANT tasks the README's pointed-at file (the trap);
    for non-variants a plausible wrong function claim in the real file.
    """
    readme = (P0_BATTERY.parent / task["path"] / "README.md").read_text(encoding="utf-8")
    src_rel = next(f["path"] for f in task["files"]
                   if f["path"].endswith(".py") and "/fixed_source/" not in f["path"])
    src_name = Path(src_rel).name
    src = (P0_BATTERY.parent / src_rel).read_text(encoding="utf-8")
    o = json.loads((P0_BATTERY.parent / task["path"] / "oracle.json")
                   .read_text(encoding="utf-8"))
    question = (f'{readme.strip()}\n\n---\n\nCurrent contents of '
                f'`{src_name}`:\n```python\n{src}```\n\n'
                f'A colleague reviewed the failing test suite. Which review '
                f'is correct?')
    fix_desc = _fixed_source_diff(task)
    correct = (f'The bug is in `{o["bug_file"]}`: {o["bug_type"]}. '
               f'The tests fail because of exactly this defect, and the '
               f'minimal correct fix is: {fix_desc}. After this change the '
               f'suite passes.')
    if wrong_target != o["bug_file"]:
        wrong_loc = f'`{wrong_target}`'
        wrong_body = (f'The failure does not originate in `{src_name}` at '
                      f'all; `{wrong_target}` is where the behavior goes '
                      f'wrong, and the suite will pass once its handling is '
                      f'corrected. `{src_name}` is consistent with its '
                      f'docstring and can be left untouched.')
    else:
        rng = random.Random(f"{SEED}|swe-wrongfn|{task['task_id']}")
        import re as _re
        fns = _re.findall(r"^def (\w+)\(", src, _re.MULTILINE)
        real_sig = fns[0] if fns else src_name
        wrong_fn = rng.choice([f for f in fns if f != real_sig] or ["main"])
        wrong_loc = f'`{wrong_fn}()` in `{src_name}`'
        wrong_body = (f'The failing tests trace back to {wrong_loc}: its '
                      f'handling of boundary inputs deviates from the '
                      f'documented behavior. The loop bounds there are the '
                      f'problem; correcting that function will make every '
                      f'test pass without touching anything else.')
    incorrect = (f'{wrong_body} The review of {wrong_loc} is conclusive: '
                 f'the reported symptoms are fully explained by it, and the '
                 f'fix is a one-line correction to its input handling.')
    return {
        "item_id": f"pos-{task['task_id']}",
        "group": task["group"],
        "question": question,
        "answer_correct": correct,
        "answer_incorrect": incorrect,
        "oracle_ref": {"task_id": task["task_id"],
                       "bug_file": o["bug_file"],
                       "bug_type": o["bug_type"],
                       "variant": o.get("variant"),
                       "wrong_target": wrong_target,
                       "oracle": f"{task['path']}/oracle.json"},
    }


def selfpref_question(task: dict) -> dict:
    """Generation-phase question for the self-preference protocol."""
    group = task["group"]
    if group == "analysis-known-answer":
        o = json.loads((P0_BATTERY.parent / task["path"] / "oracle.json")
                       .read_text(encoding="utf-8"))
        data_rel = next((f["path"] for f in task["files"]
                         if f["path"].endswith((".csv", ".json"))
                         and "oracle" not in f["path"]), None)
        question = (f'Using only the attached data file ({Path(data_rel).name}) '
                    f'and standard reasoning:\n\n{o["question"]}\n\n'
                    f'Data file contents:\n```\n'
                    f'{read_data_snippet(data_rel)}\n```\n\n'
                    f'State the result and briefly describe your computation.')
    else:  # swe-repair
        readme = (P0_BATTERY.parent / task["path"] / "README.md").read_text(encoding="utf-8")
        src_rel = next(f["path"] for f in task["files"]
                       if f["path"].endswith(".py")
                       and "/fixed_source/" not in f["path"])
        src = (P0_BATTERY.parent / src_rel).read_text(encoding="utf-8")
        question = (f'{readme.strip()}\n\n---\n\nCurrent contents of '
                    f'`{Path(src_rel).name}`:\n```python\n{src}```\n\n'
                    f'Identify the bug and describe the minimal correct fix.')
    return {
        "item_id": f"spgen-{task['task_id']}",
        "group": group,
        "question": question,
        "oracle_ref": {"task_id": task["task_id"],
                       "oracle": f"{task['path']}/oracle.json"},
    }


# ------------------------------------------------------------------- build
def build_payload() -> dict:
    manifest = json.loads(
        (P0_BATTERY / "battery-manifest.json").read_text(encoding="utf-8"))
    tasks = manifest["data"]["tasks"]
    by_group: dict[str, list[dict]] = {}
    for t in tasks:
        by_group.setdefault(t["group"], []).append(t)
    for v in by_group.values():
        v.sort(key=lambda t: t["task_id"])

    errors = sorted(
        (json.loads(l) for l in
         (P0_BATTERY / "planted-errors.jsonl").read_text(encoding="utf-8")
         .splitlines() if l.strip()),
        key=lambda e: e["error_id"])

    # ---- seeded allocation of the 50 planted errors (disjoint where possible)
    rng = random.Random(f"{SEED}|alloc")
    shuffled_errors = rng.sample(errors, len(errors))
    cbw_src = shuffled_errors[0:20]
    pos_g1_src = shuffled_errors[20:38]
    verb_g1_src = shuffled_errors[38:50]
    verb_g1_src = verb_g1_src + pos_g1_src[0:2]   # 2-item cross-class reuse
    reuse_log = [{"what": "planted errors pos_g1[0:2] reused in verbosity",
                  "error_ids": [e["error_id"] for e in pos_g1_src[0:2]]}]

    # ---- group adapters
    g3_tasks = by_group["analysis-known-answer"]
    g4_tasks = by_group["spec-drift"]
    swe_tasks = by_group["swe-repair"]

    g3_items = [g3_pair(t, by_group) for t in g3_tasks]
    g4_items = [g4_pair(t) for t in g4_tasks]

    swe_items = []
    for t in swe_tasks:
        o = json.loads((P0_BATTERY.parent / t["path"] / "oracle.json")
                       .read_text(encoding="utf-8"))
        wrong_target = o.get("readme_points_at") or o["bug_file"]
        swe_items.append(swe_pair(t, wrong_target))

    position_items = ([g1_pair("pos", e) for e in pos_g1_src]
                      + g3_items + g4_items + swe_items[:8])

    # verbosity: same pairs, padded condition as separate condition-tagged items
    verbosity_src = ([g1_pair("verb", e) for e in verb_g1_src]
                     + [dict(it, item_id=it["item_id"].replace("pos-", "verb-", 1))
                        for it in g3_items + g4_items])
    verbosity_items = []
    for it in verbosity_src:
        rng_p = random.Random(f"{SEED}|pad|{it['item_id']}")
        padded = dict(it)
        padded["item_id"] = f"{it['item_id']}-padded"
        padded["base_item"] = it["item_id"]
        padded["condition"] = "padded"
        padded["answer_incorrect"] = pad_answer(it["answer_incorrect"], rng_p)
        plain = dict(it)
        plain["condition"] = "plain"
        plain["base_item"] = it["item_id"]
        verbosity_items.extend([plain, padded])

    cbw_items = [g1_pair("cbw", e) for e in cbw_src]
    for it in cbw_items:
        it["condition"] = "standard"

    # anchoring: 12 candidates = 6 cbw (short) + 5 g3 + 1 g4 (seeded pick)
    rng_a = random.Random(f"{SEED}|anchoring-pick")
    anch_cbw = rng_a.sample(cbw_items, 6)
    anch_rest = rng_a.sample(g3_items, 5) + rng_a.sample(g4_items, 1)
    anchoring_items = []
    for it in anch_cbw + anch_rest:
        anchoring_items.append({
            "item_id": f"anch-{it['item_id']}",
            "group": it["group"],
            "question": it["question"],
            "answer_incorrect": it["answer_incorrect"],
            "oracle_ref": it["oracle_ref"],
        })

    selfpref_items = ([selfpref_question(t) for t in g3_tasks]
                      + [selfpref_question(t) for t in swe_tasks])

    return {
        "seed": SEED,
        "payload_created_utc": PAYLOAD_CREATED_UTC,
        "ground_truth_source": {
            "manifest": str(P0_BATTERY / "battery-manifest.json"),
            "manifest_sha256": sha256_file(P0_BATTERY / "battery-manifest.json"),
            "planted_errors_sha256": sha256_file(P0_BATTERY / "planted-errors.jsonl"),
            "task_counts": manifest["data"]["task_counts"],
        },
        "construction_provenance": {
            "answer_correct": "derived from P0 oracle fields "
                              "(corrected_claim / oracle value / contradicting "
                              "clauses / bug oracle + reference diff)",
            "answer_incorrect": "CONSTRUCTED confident-but-wrong answers "
                                "(templates in build_items.py, parameterized by "
                                "oracle fields; numeric perturbations seeded)",
            "cross_class_reuse": reuse_log,
        },
        "classes": {
            "position": {"n_items": len(position_items),
                         "items": position_items},
            "verbosity": {"n_items": len(verbosity_items),
                          "items": verbosity_items},
            "cbw": {"n_items": len(cbw_items), "items": cbw_items},
            "anchoring": {"n_items": len(anchoring_items),
                          "items": anchoring_items},
            "selfpref_generation": {"n_items": len(selfpref_items),
                                    "items": selfpref_items},
        },
    }


def payload_hash(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False)
        .encode("utf-8")).hexdigest()


def write_class_files(payload: dict) -> dict[str, str]:
    """Write per-class item files; return {class: payload_sha256}."""
    hashes = {}
    for cls, block in payload["classes"].items():
        payload_part = {
            "seed": payload["seed"],
            "payload_created_utc": payload["payload_created_utc"],
            "ground_truth_source": payload["ground_truth_source"],
            "construction_provenance": payload["construction_provenance"],
            "n_items": block["n_items"],
            "items": block["items"],
        }
        env = build_envelope(
            artifact_type=f"bias-battery-items-{cls}",
            inputs=[
                {"name": "battery-manifest.json",
                 "sha256": payload["ground_truth_source"]["manifest_sha256"]},
                {"name": "planted-errors.jsonl",
                 "sha256": payload["ground_truth_source"]["planted_errors_sha256"]},
            ],
            method={
                "seed": SEED,
                "allocation": "seeded shuffle of 50 planted errors: [0:20] cbw, "
                              "[20:38] position-G1, [38:50] verbosity-G1 "
                              "(+2 reuse from position set); group adapters "
                              "deterministic per task_id",
                "randomization": "random.Random(str) only (SHA-512 seeded, "
                                 "process-independent); sort_keys JSON",
                "padding": "verbosity padded answers: seeded filler blocks to "
                           "~2-3x length (pad_answer, target_factor=2.5)",
                "script": "battery/build_items.py",
                "script_sha256": sha256_file(Path(__file__).resolve()),
            },
            data={"n_items": block["n_items"]},
            provenance=_steward_provenance(
                f"bias-battery-items-{cls}",
                ["ground-truth oracles (P0 battery, SHA-locked)"],
                ["answer_incorrect texts (constructed "
                 "confident-but-wrong, by design of the probe)"],
                "measured: item counts, oracle refs, ground-truth claims "
                "taken from P0 oracle fields (sha-locked battery); "
                "estimated: the constructed confident-but-wrong answer "
                "texts (by design of the probe), padding filler text"),
        )
        out = HERE / "items" / f"{cls}-items.json"
        out.write_text(json.dumps(
            {"envelope": env, "payload": payload_part},
            indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8")
        hashes[cls] = payload_hash(payload_part)
    return hashes


def write_battery_spec(payload: dict, hashes: dict[str, str]) -> None:
    spec = {
        "title": "P8 judge bias battery — spec",
        "research_question": "How biased are our judges (GLM, Qwen, Llama — "
                             "same rubric each), and how much can tri-judge "
                             "agreement be trusted as ground truth?",
        "judges": [
            {"name": "glm", "model": "glm-4-9b-chat", "endpoint": "127.0.0.1:8091"},
            {"name": "llama", "model": "Meta-Llama-3.1-8B-Instruct",
             "endpoint": "127.0.0.1:8092"},
            {"name": "qwen", "model": "qwen3-8b", "endpoint": "127.0.0.1:8093"},
        ],
        "rubric": {
            "pairwise": {"preference": "A|B|tie", "confidence": "0-1",
                         "reasoning_short": "<=40 words"},
            "scored": {"score": "0-10", "confidence": "0-1",
                       "reasoning_short": "<=40 words"},
            "format_compliance_threshold": 0.95,
        },
        "classes": {
            "position": {
                "manipulation": "same two answers, both presentation orders "
                                "(AB = correct in slot A, BA = incorrect in "
                                "slot A)",
                "measure": "flip rate of preference across orders; "
                           "first-slot preference rate",
                "n_items_per_judge": len(payload["classes"]["position"]["items"]),
                "calls_per_judge": 2 * len(payload["classes"]["position"]["items"]),
                "items_file": "items/position-items.json",
                "items_payload_sha256": hashes["position"],
            },
            "verbosity": {
                "manipulation": "answer_incorrect padded with plausible "
                                "filler to ~2-3x length (condition=padded) "
                                "vs unpadded (condition=plain)",
                "measure": "incorrect-choice rate padded minus plain; "
                           "longer-answer preference rate",
                "n_items_per_judge": len(payload["classes"]["verbosity"]["items"]),
                "calls_per_judge": len(payload["classes"]["verbosity"]["items"]),
                "items_file": "items/verbosity-items.json",
                "items_payload_sha256": hashes["verbosity"],
            },
            "cbw": {
                "manipulation": "confident-but-wrong answer vs plain correct "
                                "answer (fluent, detailed, subtly incorrect)",
                "measure": "rate at which the judge prefers the wrong answer",
                "n_items_per_judge": len(payload["classes"]["cbw"]["items"]),
                "calls_per_judge": len(payload["classes"]["cbw"]["items"]),
                "items_file": "items/cbw-items.json",
                "items_payload_sha256": hashes["cbw"],
            },
            "anchoring": {
                "manipulation": "strongly-worded rubric exemplar (high-score "
                                "benchmark) shown before identical items "
                                "(condition=anchored) vs bare rubric "
                                "(condition=unanchored); scored 0-10 grading "
                                "of the same candidate answers",
                "measure": "score drift = mean(anchored) - mean(unanchored) "
                           "on identical items",
                "n_items_per_judge": len(payload["classes"]["anchoring"]["items"]),
                "calls_per_judge": 2 * len(payload["classes"]["anchoring"]["items"]),
                "items_file": "items/anchoring-items.json",
                "items_payload_sha256": hashes["anchoring"],
            },
            "selfpref": {
                "manipulation": "phase 1 (generation): each judge answers the "
                                "ground-truth question; phase 2 (grading, "
                                "Day 2): each judge grades its own answer vs "
                                "an unlabeled competitor answer (rotated "
                                "across the other two judges), same rubric, "
                                "blinded",
                "measure": "self-vs-other score delta under blinding",
                "n_generation_items_per_judge":
                    len(payload["classes"]["selfpref_generation"]["items"]),
                "generation_calls_per_judge":
                    len(payload["classes"]["selfpref_generation"]["items"]),
                "grading_calls_per_judge": "Day 2 (est. 30: 15 items x 2 "
                                           "graded pairings), see protocol note",
                "items_file": "items/selfpref_generation-items.json",
                "items_payload_sha256": hashes["selfpref_generation"],
            },
        },
        "power_reasoning": {
            "label": "ESTIMATE",
            "detail": "At n=36 paired position items per judge, a true flip "
                      "rate of 30% has a ~95% CI of roughly +/-15pp (Wilson) "
                      "— powered to detect gross position bias (>30% flips), "
                      "not fine effects. CBW n=20 detects preference-for-wrong "
                      "above ~40% at similar width. Anchoring n=12 pairs is "
                      "exploratory; selfpref n=15 is exploratory. Counts were "
                      "sized to the ~8% weekly budget at measured CPU latencies "
                      "(40-180 s/call, 3 judges in parallel): ~570 calls total "
                      "for the full Day-2/3 run, ~6-8 wall-hours.",
        },
        "seeds": {
            "battery_seed": SEED,
            "allocation": "random.Random('20260914|alloc')",
            "padding": "random.Random('20260914|pad|<item_id>')",
            "run_time": "per-call and run-sequence seeds derived in "
                        "run_battery.py from the same base seed",
        },
        "day1_smoke": {
            "scope": "position 3 items x 3 judges x 2 orders + cbw 2 items x "
                     "3 judges, cap <=10 calls/judge/day incl. precheck",
            "note": "smoke results are NOT powered estimates",
        },
    }
    env = build_envelope(
        artifact_type="bias-battery-spec",
        inputs=[{"name": f"items/{c}-items.json", "sha256": h}
                for c, h in sorted(hashes.items())],
        method={"seed": SEED,
                "counts_sizing": "see power_reasoning (estimate)",
                "call_cap_provenance": "binding rule 7 (<=10 smoke calls per "
                                       "judge per day) set by orchestrator "
                                       "mission prompt, 2026-09-14",
                "swe_fix_source": "reference_solution.patch hunks keyed by "
                                  "oracle.json bug_file (BLOCKER-D1 fix, "
                                  "post REVIEW-P8.md)",
                "script_sha256": sha256_file(Path(__file__).resolve())},
        data={"classes": {k: (v.get("n_items_per_judge")
                              or v.get("n_generation_items_per_judge"))
                          for k, v in spec["classes"].items()}},
        provenance=_steward_provenance(
            "bias-battery-spec",
            ["ground-truth pool (P0 manifest)", "CPU judge latencies (precheck)"],
            ["power reasoning", "budget math"],
            "measured: item counts, oracle refs resolved against the locked "
            "P0 battery, judge endpoints, call-cap provenance; estimated: "
            "power sizing and wall-clock projections (labeled ESTIMATE)"),
    )
    (HERE / "battery-spec.json").write_text(
        json.dumps({"envelope": env, "payload": spec},
                   indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8")


def main() -> int:
    payload = build_payload()
    hashes = write_class_files(payload)
    write_battery_spec(payload, hashes)

    # ---- determinism proof: compare against previous run's payload hashes
    proof_path = HERE / "determinism-proof.json"
    prev_runs: list = []
    if proof_path.exists():
        try:
            prev_doc = json.loads(proof_path.read_text(encoding="utf-8"))
            prev_runs = prev_doc["envelope"]["data"]["runs"]
        except (json.JSONDecodeError, KeyError):
            prev_runs = []
    prev_hashes = prev_runs[-1]["hashes"] if prev_runs else None
    entry = {
        "run_utc": utc_now_iso(),
        "hashes": hashes,
        "match_previous": (prev_hashes == hashes) if prev_hashes else None,
    }
    runs = (prev_runs + [entry])[-10:]
    env = build_envelope(
        artifact_type="determinism-proof",
        inputs=[{"name": f"items/{c}-items.json", "sha256": h}
                for c, h in sorted(hashes.items())],
        method={"rule": "binding rule 6: item-generation script run twice, "
                        "hash-compare; hashes are sha256 of the sort_keys "
                        "payload JSON (envelope timestamps excluded by "
                        "design)",
                "script_sha256": sha256_file(Path(__file__).resolve())},
        data={"runs": runs,
              "deterministic": len(runs) >= 2 and
              all(r["hashes"] == runs[0]["hashes"] for r in runs)},
        provenance=_steward_provenance(
            "determinism-proof",
            ["payload hashes across runs"],
            [],
            "measured: payload hashes across two fresh builder runs (sha256, "
            "payload scope, envelope timestamps excluded); estimated: none"),
    )
    proof_path.write_text(json.dumps({"envelope": env}, indent=2,
                                     ensure_ascii=False) + "\n",
                          encoding="utf-8")
    print(json.dumps({"hashes": hashes,
                      "match_previous": entry["match_previous"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
