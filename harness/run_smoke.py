"""Capped smoke run of the bias battery: a few calls per judge, then a
per-judge summary with the section-3 envelope.

Design (BRIEF-20260914-sprint-p8-judge-audit Day-1 smoke):
- deterministic item selection from the pool (seeded shuffle, recorded)
- per-judge call cap enforced BEFORE dispatch (<= 10/judge/day incl. precheck)
- one worker thread per healthy endpoint (parallel judges, sequential items)
- verbatim log shared by all threads (append-locked line writes)
- summary computes format compliance, flips, etc. FROM THE RAW LOG, labeled
  "smoke, not powered"

Usage:
    python run_smoke.py --config smoke-config.json
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from envelope import build_envelope, sha256_file, sha256_text  # noqa: E402
from judge_client import JudgeClient, load_endpoints  # noqa: E402
from parse import parse_pairwise, parse_scored  # noqa: E402
import randomize as rnd  # noqa: E402
import run_battery as rb  # noqa: E402
import summarize as sm  # noqa: E402

SMOKE_CLASSES = {
    # class: (items_selected, calls per (item x judge))
    "position": 3,
    "cbw": 2,
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    args = ap.parse_args(argv)

    raw = json.loads(Path(args.config).read_text(encoding="utf-8"))
    # accept both flat and section-3 envelope+payload forms (envelope form
    # added per REVIEW-STEWARD.md R7); the 2026-09-14 smoke consumed the
    # flat form
    cfg = raw["payload"] if isinstance(raw, dict) and "envelope" in raw else raw
    base = Path(args.config).resolve().parent  # relative paths resolve
    for key in ("endpoints_file", "log_path", "summary_out"):
        if key in cfg and cfg[key]:
            cfg[key] = str((base / cfg[key]).resolve())
    for key, val in (cfg.get("items_files") or {}).items():
        cfg["items_files"][key] = str((base / val).resolve())
    seed = int(cfg.get("seed", 20260914))
    log_path = Path(cfg["log_path"])
    endpoints = load_endpoints(cfg["endpoints_file"])
    judges = cfg.get("judges") or list(endpoints)

    clients, health = {}, {}
    for name in judges:
        probe = JudgeClient(endpoints[name], log_path, parser=None)
        h = probe.health()
        health[name] = h
        if h["healthy"]:
            clients[name] = probe
    unhealthy = [j for j in judges if j not in clients]

    # load item pools and select smoke items deterministically
    items_by_class: dict[str, list[dict]] = {}
    for cls, n_items in SMOKE_CLASSES.items():
        doc = json.loads(Path(cfg["items_files"][cls]).read_text(encoding="utf-8"))
        pool = doc["payload"]["items"]
        items_by_class[cls] = rnd.shuffled(pool, seed, f"smoke-select|{cls}")[:n_items]

    summaries: dict[str, dict] = {}
    errors: dict[str, str] = {}

    # cumulative per-day call budget per judge (binding rule 7: <=10
    # calls/judge/day). Calls already spent today (e.g. precheck) count.
    day_cap = int(cfg.get("max_day_calls_per_judge", 10))
    day_used = {j: int(cfg.get("day_calls_already_used", {}).get(j, 0))
                for j in clients}

    def work(judge: str) -> None:
        try:
            per_judge = {}
            for cls, _n in SMOKE_CLASSES.items():
                parser = parse_scored if cls == "anchoring" else parse_pairwise
                client = JudgeClient(endpoints[judge], log_path, parser=parser)
                # REVIEWER FIX 2026-09-14: pass the DAY cap; the
                # day_calls_used accumulator already nets out calls spent
                # today (precheck + earlier classes). Passing a pre-decremented
                # `remaining` double-counted and starved later classes.
                per_judge[cls] = rb.run_class(
                    cls, items_by_class[cls], {judge: client}, log_path,
                    seed=seed, temperature=float(cfg.get("temperature", 0.0)),
                    max_tokens=int(cfg.get("max_tokens", 256)),
                    max_calls_per_judge=day_cap, judges=[judge],
                    run_tag=cfg.get("run_tag", "smoke"),
                    day_calls_used=day_used)
            summaries[judge] = per_judge
        except Exception as exc:  # keep other judges running
            errors[judge] = repr(exc)

    threads = [threading.Thread(target=work, args=(j,), name=f"judge-{j}")
               for j in clients]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    records = sm.load_log(str(log_path))
    payload = sm.summarize_log(records, cfg.get("run_tag", "smoke"))
    payload["health"] = health
    payload["unhealthy_judges"] = unhealthy
    payload["runner_errors"] = errors
    payload["smoke_selection"] = {
        cls: [it["item_id"] for it in items] for cls, items in items_by_class.items()
    }
    payload["call_budget"] = {
        "day_cap_per_judge": day_cap,
        "day_calls_already_used_at_start": cfg.get("day_calls_already_used", {}),
        "day_calls_used_after_run": day_used,
        "note": "cap enforced cumulatively across classes (run_battery "
                "day_calls_used accumulator)",
    }

    status = "PARTIAL" if unhealthy or errors else "COMPLETE"
    envelope = build_envelope(
        artifact_type="smoke-summary",
        inputs={
            "judge_call_log": sha256_file(str(log_path)),
            "endpoints_file": sha256_file(cfg["endpoints_file"]),
            **{f"items_{cls}": sha256_file(p)
               for cls, p in cfg["items_files"].items()},
        },
        method={
            "seed": seed,
            "temperature": cfg.get("temperature", 0.0),
            "max_tokens": cfg.get("max_tokens", 256),
            "item_selection": "seeded shuffle (random.Random(seed|smoke-select|class)), "
                              "first N taken; recorded in smoke_selection",
            "run_sequence": "per-judge seeded shuffle via run_battery.run_class; "
                            "one thread per judge",
            "call_cap": "per-judge DAY cap enforced cumulatively across "
                        "classes (day_calls_used accumulator); 1 retry max, "
                        "all attempts logged",
            "prompts": {"system_pairwise_sha256": sha256_text(rb.SYSTEM_PAIRWISE),
                        "system_scored_sha256": sha256_text(rb.SYSTEM_SCORED),
                        "anchor_exemplar_sha256": sha256_text(rb.ANCHOR_EXEMPLAR)},
        },
        data=payload,
        provenance={
            "measured": ["health", "calls", "parse_rate",
                         "format_compliance", "flips", "kappa"],
            "estimated": ["all observed rates (smoke, not powered)"],
            "measured_vs_estimated":
                "measured: health, call counts, parse and format-compliance "
                "rates, position flips, kappa and latencies, all recomputed "
                "from the verbatim raw call log; estimated: all observed "
                "rates as population estimates (smoke, not powered)",
            "artifact_type_extension_note":
                "artifact_type 'smoke-summary' is a section-3 enum extension, "
                "declared per lab data-steward policy, 2026-09-14",
        },
        notes=f"smoke status: {status}; observed rates are NOT powered estimates",
    )
    out = Path(cfg.get("summary_out", str(log_path.parent / "smoke-summary.json")))
    out.write_text(json.dumps({"envelope": envelope}, indent=2,
                              ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": status, "health": {k: v["healthy"]
                                                   for k, v in health.items()},
                      "errors": errors}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
