"""Verdict parser + format-compliance tracking for the P8 judge harness.

Rubric output formats this parser accepts (strict spec shown to judges):

Pairwise schema ("which answer is better?"):
    {"preference": "A" | "B" | "tie", "confidence": <float 0..1>,
     "reasoning_short": "<= 40 words"}

Scored schema (rubric anchoring / self-preference grading):
    {"score": <float 0..10>, "confidence": <float 0..1>,
     "reasoning_short": "<= 40 words"}

Parsing is TOLERANT (extract a verdict if any decodable JSON object with the
key field exists anywhere in the response -- wrapped in prose, markdown
fences, or Qwen3 <think> blocks); FORMAT COMPLIANCE is STRICT (compliant
only if the response matches the spec: valid key field, numeric confidence
in [0,1], string reasoning_short). Compliance rate per judge is a measured
finding, never a silent drop.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

THINK_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL | re.IGNORECASE)
THINK_OPEN_RE = re.compile(r"<think>(.*)\Z", re.DOTALL | re.IGNORECASE)

PREFERENCE_VALUES = {"A", "B", "TIE"}


@dataclass
class ParseResult:
    ok: bool
    format_compliant: bool
    verdict: dict[str, Any] | None
    error: str | None = None
    schema: str = "pairwise"
    think_block_present: bool = False
    raw_keys: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "format_compliant": self.format_compliant,
            "verdict": self.verdict,
            "error": self.error,
            "schema": self.schema,
            "think_block_present": self.think_block_present,
            "raw_keys": self.raw_keys,
        }


def strip_think(text: str) -> tuple[str, bool]:
    """Remove <think>...</think> (and unclosed <think>...) blocks.

    Returns (clean_text, think_block_present). Think-block presence is
    recorded per call in the parse result -- a measured field, per the
    Qwen3 thinking-mode protocol.
    """
    present = bool(THINK_RE.search(text)) or bool(THINK_OPEN_RE.search(text))
    clean = THINK_RE.sub("", text)
    clean = THINK_OPEN_RE.sub("", clean)
    return clean, present


def _extract_json_objects(text: str) -> list[tuple[dict[str, Any], int]]:
    """All decodable top-level JSON objects in `text`, with start offsets.

    Tolerant of surrounding prose and markdown fences (fence characters
    don't interfere with brace scanning).
    """
    dec = json.JSONDecoder()
    out: list[tuple[dict[str, Any], int]] = []
    for idx, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            obj, _end = dec.raw_decode(text, idx)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            out.append((obj, idx))
    return out


def _as_float(v: Any) -> tuple[float | None, bool]:
    """-> (value_or_None, is_spec_compliant). Spec: JSON number in range."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None, False
    return float(v), True


def _parse_common(obj: dict[str, Any], schema: str) -> tuple[dict[str, Any] | None, str | None, bool]:
    """Normalize the verdict dict. Returns (verdict, error, format_compliant)."""
    key = "preference" if schema == "pairwise" else "score"
    compliant = True

    if schema == "pairwise":
        raw_pref = obj.get(key)
        pref = raw_pref.strip().upper() if isinstance(raw_pref, str) else None
        if pref not in PREFERENCE_VALUES:
            return None, f"invalid preference value: {raw_pref!r}", False
        pref_out: Any = pref
    else:
        score, score_ok = _as_float(obj.get(key))
        if score is None or not (0.0 <= score <= 10.0):
            return None, f"invalid score value: {obj.get(key)!r}", False
        pref_out = score

    conf, conf_ok = _as_float(obj.get("confidence"))
    if conf is None or not (0.0 <= conf <= 1.0):
        conf = None if not conf_ok else conf
        if not conf_ok or conf is None or not (0.0 <= conf <= 1.0):
            compliant = False  # out-of-range or non-numeric confidence

    reasoning = obj.get("reasoning_short")
    if not isinstance(reasoning, str):
        compliant = False
        reasoning = None

    verdict = {
        ("preference" if schema == "pairwise" else "score"): pref_out,
        "confidence": conf,
        "reasoning_short": reasoning,
    }
    return verdict, None, compliant


def parse_judgment(raw_text: str, schema: str = "pairwise") -> dict[str, Any]:
    """Parse a judge response. Returns ParseResult.to_dict().

    Tolerant extraction; strict compliance flag. Never raises.
    """
    if schema not in {"pairwise", "scored"}:
        raise ValueError(f"unknown schema: {schema}")
    if not isinstance(raw_text, str) or not raw_text.strip():
        return ParseResult(False, False, None, "empty response",
                           schema=schema).to_dict()

    clean, think_present = strip_think(raw_text)
    objs = _extract_json_objects(clean)
    key = "preference" if schema == "pairwise" else "score"
    candidates = [(o, i) for o, i in objs if key in o]

    if not candidates:
        # record what keys we did see, for diagnosis
        seen = sorted({k for o, _ in objs for k in o}) if objs else []
        err = "no JSON object with key %r found" % key
        if not objs:
            err = "no decodable JSON object found"
        return ParseResult(False, False, None, err, schema=schema,
                           think_block_present=think_present,
                           raw_keys=seen).to_dict()

    # take the LAST object carrying the key field (models tend to emit
    # reasoning JSON first, final verdict last; final answer wins)
    obj, _ = candidates[-1]
    verdict, err, compliant = _parse_common(obj, schema)
    if verdict is None:
        return ParseResult(False, False, None, err, schema=schema,
                           think_block_present=think_present,
                           raw_keys=sorted(obj.keys())).to_dict()
    return ParseResult(ok=True, format_compliant=compliant, verdict=verdict,
                       error=None, schema=schema,
                       think_block_present=think_present,
                       raw_keys=sorted(obj.keys())).to_dict()


def parse_pairwise(raw_text: str) -> dict[str, Any]:
    return parse_judgment(raw_text, schema="pairwise")


def parse_scored(raw_text: str) -> dict[str, Any]:
    return parse_judgment(raw_text, schema="scored")


class FormatTracker:
    """Per-judge format-compliance bookkeeping over parsed log records."""

    def __init__(self) -> None:
        self.counts: dict[str, dict[str, int]] = {}

    def add(self, judge: str, parsed: dict[str, Any] | None,
            had_error: bool = False) -> None:
        c = self.counts.setdefault(
            judge, {"calls": 0, "http_errors": 0, "parsed": 0,
                    "compliant": 0, "think_blocks": 0})
        c["calls"] += 1
        if had_error:
            c["http_errors"] += 1
            return
        if parsed and parsed.get("ok"):
            c["parsed"] += 1
        if parsed and parsed.get("format_compliant"):
            c["compliant"] += 1
        if parsed and parsed.get("think_block_present"):
            c["think_blocks"] += 1

    def summary(self) -> dict[str, dict[str, Any]]:
        out = {}
        for judge, c in self.counts.items():
            calls = c["calls"]
            out[judge] = {
                **c,
                "parse_rate": (c["parsed"] / calls) if calls else None,
                "format_compliance_rate": (c["compliant"] / calls) if calls else None,
            }
        return out
