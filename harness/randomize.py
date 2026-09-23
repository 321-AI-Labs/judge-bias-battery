"""Seeded randomization utilities: order assignment, shuffling, condition
assignment. Every function derives an independent `random.Random` instance
from an explicit string seed so results are deterministic across processes
(random.Random(str) hashes the string with SHA-512 internally, which is not
affected by PYTHONHASHSEED). Seeds are always recorded in the call log
metadata and in the artifact envelope `method` block.
"""
from __future__ import annotations

import random
from typing import Any, Sequence


def make_rng(*parts: Any) -> random.Random:
    """Deterministic RNG from arbitrary seed parts."""
    return random.Random("|".join(str(p) for p in parts))


def pick_order(seed: Any, item_id: str) -> str:
    """Per-item coin flip: 'AB' (correct answer in slot A) or 'BA'."""
    return "AB" if make_rng(seed, "position", item_id).random() < 0.5 else "BA"


def both_orders(item: dict[str, Any]) -> list[dict[str, Any]]:
    """Expand one pairwise item into its two presentation orders.

    item must have keys: item_id, question, answer_correct, answer_incorrect.
    Returns two call-input dicts, order 'AB' (correct in slot A) and 'BA'
    (incorrect in slot A). Both are always run -- position bias is measured
    by comparing them; the seed decides run sequence, not inclusion.
    """
    out = []
    for order in ("AB", "BA"):
        if order == "AB":
            a, b = item["answer_correct"], item["answer_incorrect"]
        else:
            a, b = item["answer_incorrect"], item["answer_correct"]
        out.append({
            "item_id": item["item_id"],
            "order": order,
            "slot_A": a,
            "slot_B": b,
            "correct_slot": "A" if order == "AB" else "B",
        })
    return out


def shuffled(seq: Sequence[Any], seed: Any, tag: str) -> list[Any]:
    """Deterministic shuffle (returns a new list)."""
    rng = make_rng(seed, "shuffle", tag)
    return rng.sample(list(seq), len(seq))


def assign_conditions(
    subjects: Sequence[Any],
    arms: Sequence[str],
    seed: Any,
    tag: str,
) -> dict[str, str]:
    """Map subject -> arm: balanced round-robin over a seeded shuffle."""
    order = shuffled(subjects, seed, tag)
    return {s: arms[i % len(arms)] for i, s in enumerate(order)}
