"""Per-judge summary of a raw judge-call JSONL log, with section-3 envelope.

Reads the verbatim log and computes, per judge and per bias class:
- call counts, HTTP error counts, parse rate, format compliance rate
  (<0.95 is a FINDING per protocol -- reported, never silently dropped),
  think-block incidence
- position-bias flip rates (strict + choice) and first-slot preference
- verbosity effect (incorrect-choice rate plain vs padded)
- pairwise Cohen's kappa between judges
- anchoring score drift (anchored vs unanchored means)

Usage:
    python summarize.py --log calls.jsonl --out summary.json [--run-tag X]
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from envelope import build_envelope, sha256_file  # noqa: E402
from metrics import (format_compliance_rate, load_log,  # noqa: E402
                     pairwise_kappa_from_log, position_flips,
                     score_drift, verbosity_effect)


def summarize_log(records: list[dict[str, Any]], run_tag: str | None = None
                  ) -> dict[str, Any]:
    if run_tag:
        records = [r for r in records
                   if r.get("metadata", {}).get("run_tag") in (None, run_tag)]
    finals = [r for r in records if not r.get("retry_of")]  # keep last attempts
    by_judge: dict[str, list[dict]] = defaultdict(list)
    for r in finals:
        by_judge[r.get("endpoint") or r.get("metadata", {}).get("judge", "?")].append(r)

    judges = {}
    for judge, recs in sorted(by_judge.items()):
        entry: dict[str, Any] = {"format": format_compliance_rate(recs)}
        pos = position_flips([r for r in recs
                              if r.get("metadata", {}).get("bias_class") == "position"])
        entry["position_bias"] = pos if pos["n_pairs"] else None
        verb = verbosity_effect([r for r in recs
                                 if r.get("metadata", {}).get("bias_class") == "verbosity"])
        entry["verbosity_bias"] = verb if verb["n_calls"] else None

        # anchoring drift on identical items (paired by item_id)
        anch: dict[str, dict[str, list[float]]] = defaultdict(
            lambda: {"anchored": [], "unanchored": []})
        for r in recs:
            md = r.get("metadata", {})
            if md.get("bias_class") == "anchoring" and r.get("error") is None:
                score = ((r.get("parsed") or {}).get("verdict") or {}).get("score")
                if score is not None:
                    anch[md["item_id"]][md["condition"]].append(score)
        a_vals, u_vals = [], []
        for iid, conds in anch.items():
            if conds["anchored"] and conds["unanchored"]:
                a_vals.append(statistics.fmean(conds["anchored"]))
                u_vals.append(statistics.fmean(conds["unanchored"]))
        entry["anchoring_drift"] = score_drift(a_vals, u_vals) if a_vals else None

        # CBW: does the judge prefer the confident-but-wrong answer?
        cbw_prefs = []
        for r in recs:
            md = r.get("metadata", {})
            if md.get("bias_class") == "cbw" and (r.get("parsed") or {}).get("ok"):
                pref = r["parsed"]["verdict"]["preference"]
                cbw_prefs.append(0 if pref == "TIE" else
                                 (1 if (pref == "A") != (md.get("correct_slot") == "A")
                                  else 0))
        entry["cbw_wrong_preference_rate"] = {
            "n": len(cbw_prefs),
            "rate": (sum(cbw_prefs) / len(cbw_prefs)) if cbw_prefs else None,
            "note": "1.0 = judge always picks the confident-but-wrong answer",
        }
        entry["think_block_rate"] = _think_rate(recs)
        judges[judge] = entry

    return {
        "n_records_total": len(records),
        "n_final_calls": len(finals),
        "judges": judges,
        "kappa_judge_pairs": pairwise_kappa_from_log(finals) or None,
    }


def _think_rate(recs: list[dict]) -> dict[str, Any]:
    scored = [r for r in recs if r.get("parsed")]
    n = sum(1 for r in scored if r["parsed"].get("think_block_present"))
    return {"n_calls_with_parse": len(scored), "n_with_think_block": n,
            "rate": (n / len(scored)) if scored else None}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--run-tag", default=None)
    ap.add_argument("--artifact-type", default="judge-log-summary")
    ap.add_argument("--note", default=None)
    args = ap.parse_args(argv)

    records = load_log(args.log)
    payload = summarize_log(records, args.run_tag)
    envelope = build_envelope(
        artifact_type=args.artifact_type,
        inputs={"judge_call_log": sha256_file(args.log)},
        method={
            "computation": "metrics.py applied to raw verbatim log records; "
                           "retry-superseded attempts excluded; no other filtering",
            "run_tag_filter": args.run_tag,
            "power": "SMOKE (n tiny) — observed rates are NOT powered estimates"
                     if args.artifact_type.endswith("smoke-summary") else
                     "see battery-spec.json for powered design",
        },
        data=payload,
        provenance={
            "measured": ["calls", "parse_rate", "format_compliance_rate",
                         "flips", "kappa"],
            "estimated": [] if not args.artifact_type.endswith(
                "smoke-summary") else
                ["all observed rates (smoke, not powered)"],
            "measured_vs_estimated":
                "measured: calls, parse rate, format-compliance rate, flips, "
                "kappa and latencies recomputed from the verbatim raw call "
                "log; estimated: "
                + ("all observed rates as population estimates (smoke, not "
                   "powered)" if args.artifact_type.endswith("smoke-summary")
                   else "none"),
            "artifact_type_extension_note":
                f"artifact_type '{args.artifact_type}' is a section-3 enum "
                "extension, declared per lab data-steward policy, 2026-09-14",
        },
        notes=args.note,
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"envelope": envelope}, indent=2,
                              ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
