"""Bias metrics for the P8 judge audit, computed from raw call-log records.

Every function consumes plain dicts (the JSONL log records the harness
writes), so the adversarial reviewer can recompute any number straight from
the raw logs without trusting this module's outputs.

Metrics:
- format_compliance_rate      (per judge; <0.95 is a finding per protocol)
- position_flips              (position bias: preference flips across AB/BA)
- verbosity_effect            (does preference track length over correctness)
- self_other_delta            (self-preference under blinding)
- score_drift                 (rubric anchoring: anchored vs unanchored means)
- cohen_kappa                 (inter-judge agreement on categorical verdicts)
- bootstrap_ci                (seeded percentile bootstrap for CIs)
"""
from __future__ import annotations

import json
import math
import random
import statistics
from collections import defaultdict
from typing import Any, Callable, Iterable, Sequence


# ------------------------------------------------------------------ loading
def load_log(path: str) -> list[dict[str, Any]]:
    records = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def _final_calls(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop retry superseded attempts: keep the last attempt per (item, order,
    judge) tuple as identified by retry_of chains."""
    by_id = {r["call_id"]: r for r in records}
    superseded = {r["retry_of"] for r in records if r.get("retry_of")}
    return [r for r in records if r["call_id"] not in superseded]


def _ok_calls(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in _final_calls(records)
            if r.get("error") is None and r.get("parsed", {})
            and (r["parsed"] or {}).get("ok")]


# -------------------------------------------------------- format compliance
def format_compliance_rate(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Compliance over ALL final calls (errors count as non-compliant)."""
    finals = _final_calls(records)
    n = len(finals)
    compliant = sum(1 for r in finals
                    if (r.get("parsed") or {}).get("format_compliant"))
    parsed = sum(1 for r in finals if (r.get("parsed") or {}).get("ok"))
    return {
        "n_calls": n,
        "n_parsed": parsed,
        "n_compliant": compliant,
        "parse_rate": parsed / n if n else None,
        "format_compliance_rate": compliant / n if n else None,
    }


# -------------------------------------------------------------- position bias
def _choice_identity(rec: dict[str, Any]) -> str | None:
    """Map a parsed pairwise verdict to which CONTENT won: 'correct',
    'incorrect', or 'tie'. Requires metadata.correct_slot."""
    md = rec.get("metadata", {})
    pref = ((rec.get("parsed") or {}).get("verdict") or {}).get("preference")
    if pref is None:
        return None
    if pref == "TIE":
        return "tie"
    slot = "A" if pref == "A" else "B"
    return "correct" if slot == md.get("correct_slot") else "incorrect"


def position_flips(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Flip rate across AB/BA presentations, per (item, judge) pairs.

    - flip_rate_strict: any difference in content-choice across orders,
      counting tie-mismatches (e.g. A->tie) as flips.
    - flip_rate_choice: flips among pairs where both orders produced a
      non-tie choice (the classic position-bias flip).
    - first_slot_preference_rate: rate at which the judge picks whichever
      content sits in slot A (positional loyalty).
    """
    finals = [r for r in _final_calls(records)
              if r.get("error") is None]
    groups: dict[tuple[str, str], dict[str, dict[str, Any]]] = defaultdict(dict)
    for r in finals:
        md = r.get("metadata", {})
        if md.get("bias_class") != "position" or "order" not in md:
            continue
        if (r.get("parsed") or {}).get("ok"):
            groups[(md.get("judge"), md.get("item_id"))][md["order"]] = r

    n_pairs = len(groups)
    strict_flips = choice_pairs = choice_flips = 0
    first_slot_hits = first_slot_total = 0
    for pair in groups.values():
        if "AB" not in pair or "BA" not in pair:
            continue
        ab, ba = _choice_identity(pair["AB"]), _choice_identity(pair["BA"])
        if ab is None or ba is None:
            continue
        if ab != ba:
            strict_flips += 1
        if ab in {"correct", "incorrect"} and ba in {"correct", "incorrect"}:
            choice_pairs += 1
            if ab != ba:
                choice_flips += 1
        for rec in (pair["AB"], pair["BA"]):
            pref = rec["parsed"]["verdict"]["preference"]
            if pref in {"A", "B"}:
                # first-slot preference: judge picked whichever answer sat in
                # slot A, regardless of content
                first_slot_total += 1
                if pref == "A":
                    first_slot_hits += 1

    return {
        "n_pairs": n_pairs,
        "n_pairs_both_parsed": sum(
            1 for p in groups.values()
            if "AB" in p and "BA" in p
            and _choice_identity(p["AB"]) and _choice_identity(p["BA"])),
        "flip_rate_strict": strict_flips / n_pairs if n_pairs else None,
        "flip_rate_choice": (choice_flips / choice_pairs) if choice_pairs else None,
        "n_choice_pairs": choice_pairs,
        "first_slot_preference_rate": (
            first_slot_hits / first_slot_total) if first_slot_total else None,
    }


# ------------------------------------------------------------- verbosity bias
def verbosity_effect(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Per item: condition 'plain' (unpadded pair) vs 'padded' (incorrect
    answer padded with filler). Measures whether preference tracks length.

    - incorrect_choice_rate per condition
    - verbosity_delta = padded_rate - plain_rate  (positive => padding wins)
    - longer_answer_preference_rate across all calls
    """
    finals = [r for r in _final_calls(records) if r.get("error") is None
              and (r.get("parsed") or {}).get("ok")
              and r.get("metadata", {}).get("bias_class") == "verbosity"]
    by_cond: dict[str, list[int]] = {"plain": [], "padded": []}
    longer_hits = longer_total = 0
    for r in finals:
        md = r.get("metadata", {})
        pref = r["parsed"]["verdict"]["preference"]
        if pref in {"A", "B"}:
            slot = "A" if pref == "A" else "B"
            incorrect_chosen = (slot != md["correct_slot"])
            by_cond[md["condition"]].append(1 if incorrect_chosen else 0)
            longer_total += 1
            if slot == md.get("longer_slot"):
                longer_hits += 1

    def _rate(xs: list[int]) -> float | None:
        return sum(xs) / len(xs) if xs else None

    plain, padded = _rate(by_cond["plain"]), _rate(by_cond["padded"])
    delta = (padded - plain) if (plain is not None and padded is not None) else None
    return {
        "n_calls": len(finals),
        "incorrect_choice_rate_plain": plain,
        "incorrect_choice_rate_padded": padded,
        "verbosity_delta": delta,
        "longer_answer_preference_rate": (
            longer_hits / longer_total) if longer_total else None,
    }


# ---------------------------------------------------------- self-preference
def self_other_delta(scored: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """scored: [{"own": float, "other": float}, ...] -- judge's score of its
    own answer vs the competitor answer on the same item (blind)."""
    if not scored:
        return {"n": 0, "mean_own": None, "mean_other": None,
                "delta": None, "delta_ci95": None}
    own = [s["own"] for s in scored]
    other = [s["other"] for s in scored]
    deltas = [o - t for o, t in zip(own, other)]
    ci = bootstrap_ci(deltas, statistics.fmean)
    return {
        "n": len(deltas),
        "mean_own": statistics.fmean(own),
        "mean_other": statistics.fmean(other),
        "delta": statistics.fmean(deltas),
        "delta_ci95": ci,
    }


# --------------------------------------------------------------- anchoring
def score_drift(anchored: Sequence[float],
                unanchored: Sequence[float],
                seed: int = 20260914) -> dict[str, Any]:
    """Mean score anchored-condition minus unanchored, on identical items."""
    if not anchored or not unanchored:
        return {"n_anchored": len(anchored), "n_unanchored": len(unanchored),
                "mean_anchored": None, "mean_unanchored": None,
                "drift": None, "drift_ci95": None}
    ci = bootstrap_ci(
        [a - u for a, u in zip(anchored, unanchored)], statistics.fmean,
        seed=seed) if len(anchored) == len(unanchored) else None
    out = {
        "n_anchored": len(anchored),
        "n_unanchored": len(unanchored),
        "mean_anchored": statistics.fmean(anchored),
        "mean_unanchored": statistics.fmean(unanchored),
        "drift": statistics.fmean(anchored) - statistics.fmean(unanchored),
        "drift_ci95": ci,
    }
    return out


# ------------------------------------------------------------------- kappa
def cohen_kappa(labels1: Sequence[str], labels2: Sequence[str]) -> dict[str, Any]:
    """Cohen's kappa over categorical labels (e.g. A/B/tie)."""
    if len(labels1) != len(labels2) or not labels1:
        return {"kappa": None, "n": 0,
                "reason": "empty or length-mismatched label sets"}
    cats = sorted(set(labels1) | set(labels2))
    n = len(labels1)
    po = sum(1 for a, b in zip(labels1, labels2) if a == b) / n
    counts1 = {c: sum(1 for x in labels1 if x == c) for c in cats}
    counts2 = {c: sum(1 for x in labels2 if x == c) for c in cats}
    pe = sum((counts1[c] / n) * (counts2[c] / n) for c in cats)
    if math.isclose(pe, 1.0):
        return {"kappa": None, "n": n, "po": po, "pe": pe,
                "reason": "kappa undefined: expected agreement = 1 "
                          "(degenerate label distribution)"}
    return {"kappa": (po - pe) / (1 - pe), "n": n, "po": po, "pe": pe,
            "categories": cats}


def pairwise_kappa_from_log(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Judge-vs-judge kappa over shared (item, order) pairwise verdicts.

    Each shared (item_id, order) cell counts ONCE per judge pair -- REVIEWER
    FIX 2026-09-14: the previous comprehension iterated over cell keys (which
    include the judge), counting every shared cell once per third judge and
    inflating n by the judge count (kappa value unaffected).
    """
    finals = _ok_calls([r for r in records
                        if r.get("metadata", {}).get("schema") == "pairwise"])
    cell: dict[tuple[str, str, str], str] = {}
    for r in finals:
        md = r.get("metadata", {})
        key = (md.get("item_id"), md.get("order", ""), md.get("judge"))
        pref = r["parsed"]["verdict"].get("preference")
        if pref:
            cell[key] = pref
    judges = sorted({k[2] for k in cell})
    out: dict[str, Any] = {}
    for i in range(len(judges)):
        for j in range(i + 1, len(judges)):
            shared = sorted({(k[0], k[1]) for k in cell
                             if (k[0], k[1], judges[i]) in cell
                             and (k[0], k[1], judges[j]) in cell})
            l1 = [cell[(k[0], k[1], judges[i])] for k in shared]
            l2 = [cell[(k[0], k[1], judges[j])] for k in shared]
            out[f"{judges[i]}__{judges[j]}"] = cohen_kappa(l1, l2)
    return out


# ---------------------------------------------------------------- bootstrap
def bootstrap_ci(
    values: Sequence[float],
    stat: Callable[[Sequence[float]], float] = statistics.fmean,
    n_boot: int = 2000,
    seed: int = 20260914,
    alpha: float = 0.05,
) -> list[float] | None:
    """Seeded percentile bootstrap CI. Deterministic given (values, seed)."""
    vals = list(values)
    if len(vals) < 2:
        return None
    rng = random.Random(seed)
    stats = sorted(
        stat([vals[k] for k in (rng.randrange(len(vals)) for _ in vals)])
        for _ in range(n_boot))
    lo = stats[max(0, int(math.floor((alpha / 2) * n_boot)) - 1)]
    hi = stats[min(n_boot - 1, int(math.ceil((1 - alpha / 2) * n_boot)) - 1)]
    return [lo, hi]
