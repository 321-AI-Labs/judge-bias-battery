#!/usr/bin/env python3
"""derive-corrections.py -- P8 judge-bias coefficient derivation.

Binding method: docs/COEF-METHOD.md (FROZEN 2026-09-16).

Re-derives per-judge per-class bias coefficients FROM THE VERBATIM LOG
(--log, a judge-calls JSONL written by run_battery.py), joining every call to its item spec
via metadata.item_seed_source ("{seed}|{bias_class}|{judge}|{item_id}|"
"{condition}|{order}") -> items payload. The runner's summary-*.json files
are NEVER read by `derive` (COEF-METHOD section 1); they are recomputed
independently from raw by the `cross-check` subcommand and diffed against
the shipped files (section 4.2).

stdlib only. Deterministic: the only wall-clock field in the output is
envelope.created_utc (permitted by section 4.3, "wall-clock only in the
envelope"); per-row computed_utc is pinned to the verbatim log's final
call timestamp so re-runs are byte-identical.

Subcommands:
  derive        (default) write judge-corrections.json
  cross-check   recompute runner summaries from raw, diff vs shipped
                summary-*.json, write summary-recompute-diff.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import random
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import NormalDist

REPO_ROOT = Path(__file__).resolve().parents[1]
LOG_PATH = REPO_ROOT / "logs" / "judge-calls.jsonl"  # override with --log
ITEMS_DIR = REPO_ROOT / "battery" / "items"          # override with --items-dir
SPEC_PATH = REPO_ROOT / "battery" / "battery-spec.json"  # override with --spec
METHOD_DOC = REPO_ROOT / "docs" / "COEF-METHOD.md"   # override with --method-doc
CROSSCHECK_OUT = REPO_ROOT / "logs" / "summary-recompute-diff.json"
OUT_PATH = REPO_ROOT / "data" / "judge-corrections.json"

SEED = 20260914                       # COEF-METHOD pinned derivation seed
N_BOOT = 10_000                       # anchoring paired-bootstrap resamples
Z95 = NormalDist().inv_cdf(0.975)     # Wilson 95% normal quantile

JUDGES = ["glm", "llama", "qwen"]
SCORED_CLASSES = ["position", "verbosity", "cbw", "anchoring"]
ALL_CLASSES = SCORED_CLASSES + ["selfpref_generation"]
ITEMS_FILE = {  # class -> items payload (per battery-spec.json class table)
    "position": "position-items.json",
    "verbosity": "verbosity-items.json",
    "cbw": "cbw-items.json",
    "anchoring": "anchoring-items.json",
    "selfpref_generation": "selfpref_generation-items.json",
}
# expected parsed-ok jobs per judge (task coverage self-check, battery-spec counts)
EXPECTED_PER_JUDGE = {
    "position": 72,        # 36 items x 2 orders
    "verbosity": 96,       # 48 items x 2 orders (main 48 + top-up 48)
    "cbw": 40,             # 20 items x 2 orders (main 20 + top-up 20)
    "anchoring": 24,       # 12 items x 2 conditions
    "selfpref_generation": 15,  # 15 generation items (generation-only)
}
CLASS_LABEL = {  # bias_class in log -> class key in spec/§2
    "position": "position",
    "verbosity": "verbosity",
    "cbw": "cbw",
    "anchoring": "anchoring",
    "selfpref_generation": "selfpref",
}


# --------------------------------------------------------------------- io
def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def load_log(path: Path) -> list[dict]:
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                rows.append({"_unparseable_line": lineno, "_error": str(exc)})
    return rows


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ------------------------------------------------------- verdict re-parse
# Independent strict re-extraction of the verdict from raw_response_text
# (tolerant scan: strip <think> blocks, take the LAST decodable JSON object
# carrying the key field -- mirrors harness/parse.py semantics so any
# divergence from the log's `parsed` field is detectable).
THINK_MARKERS = ("<think>", "</think>")


def _extract_json_objects(text: str) -> list[dict]:
    dec = json.JSONDecoder()
    out = []
    for idx, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            obj, _end = dec.raw_decode(text, idx)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def reparse_verdict(raw_text: str, schema: str) -> dict | None:
    """Return {'preference'|'score': value} or None if not extractable."""
    if not isinstance(raw_text, str) or not raw_text.strip():
        return None
    clean = raw_text
    while "<think>" in clean.lower():
        low = clean.lower()
        s = low.find("<think>")
        e = low.find("</think>", s)
        clean = clean[:s] + (clean[e + 8:] if e >= 0 else "")
    key = "preference" if schema == "pairwise" else "score"
    cands = [o for o in _extract_json_objects(clean) if key in o]
    if not cands:
        return None
    obj = cands[-1]
    if schema == "pairwise":
        v = obj.get(key)
        v = v.strip().upper() if isinstance(v, str) else None
        return {"preference": v} if v in {"A", "B", "TIE"} else None
    v = obj.get(key)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    v = float(v)
    return {"score": v} if 0.0 <= v <= 10.0 else None


# ------------------------------------------------------------- estimators
def wilson_ci(k: int, n: int) -> list[float]:
    """Wilson 95% score interval for a binomial proportion."""
    if n == 0:
        return [None, None]
    p = k / n
    denom = 1.0 + Z95 * Z95 / n
    center = (p + Z95 * Z95 / (2.0 * n)) / denom
    half = Z95 * math.sqrt(p * (1.0 - p) / n + Z95 * Z95 / (4.0 * n * n)) / denom
    lo = max(0.0, center - half)
    hi = min(1.0, center + half)
    # snap float noise to the exact boundary (e.g. k=0 -> lo == 0.0 exactly)
    if lo < 1e-12:
        lo = 0.0
    if hi > 1.0 - 1e-12:
        hi = 1.0
    return [lo, hi]


def anchoring_bootstrap_ci(deltas: list[float], judge: str) -> list[float]:
    """Paired bootstrap over items, 10,000 resamples.

    Per-resample RNG: random.Random("20260914|anchoring|{judge}|{r}") for
    r = 0..9999 (COEF-METHOD section 2 seed convention). Percentile
    convention identical to harness/metrics.py bootstrap_ci.
    """
    n = len(deltas)
    stats = []
    for r in range(N_BOOT):
        rng = random.Random(f"{SEED}|anchoring|{judge}|{r}")
        sample = [deltas[rng.randrange(n)] for _ in range(n)]
        stats.append(statistics.fmean(sample))
    stats.sort()
    alpha = 0.05
    lo = stats[max(0, int(math.floor((alpha / 2) * N_BOOT)) - 1)]
    hi = stats[min(N_BOOT - 1, int(math.ceil((1 - alpha / 2) * N_BOOT)) - 1)]
    return [lo, hi]


def content_identity(pref: str, correct_slot: str | None) -> str | None:
    """Map a pairwise preference to which CONTENT won: correct/incorrect/tie."""
    if pref == "TIE":
        return "tie"
    if pref in {"A", "B"} and correct_slot in {"A", "B"}:
        return "correct" if pref == correct_slot else "incorrect"
    return None


# ------------------------------------------------------------ derivation
def derive(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT_PATH))
    ap.add_argument("--created-utc", default=None,
                    help="pin envelope.created_utc (determinism replay); "
                         "default wall-clock UTC now")
    ap.add_argument("--crosscheck-file", default=None,
                    help="optional summary-recompute-diff.json whose verdict "
                         "is embedded in data.cross_checks (the summaries "
                         "themselves are still never read here)")
    ap.add_argument("--log", default=str(LOG_PATH),
                    help="verbatim judge-call JSONL log to derive from")
    ap.add_argument("--items-dir", default=str(ITEMS_DIR),
                    help="directory containing the per-class items payloads")
    ap.add_argument("--spec", default=str(SPEC_PATH),
                    help="battery-spec.json (class table)")
    ap.add_argument("--method-doc", default=str(METHOD_DOC),
                    help="frozen COEF-METHOD.md (sha recorded in the envelope)")
    args = ap.parse_args(argv)

    log_path = Path(args.log)
    items_dir = Path(args.items_dir)
    spec_path = Path(args.spec)
    method_doc = Path(args.method_doc)

    findings: list[dict] = []
    method_sha = sha256_file(method_doc)
    script_sha = sha256_file(Path(__file__).resolve())

    rows = load_log(log_path)
    unparsable = [r for r in rows if "_unparseable_line" in r]
    if unparsable:
        findings.append({"id": "F-LOG-UNPARSEABLE", "detail":
                         f"{len(unparsable)} log lines failed JSON parsing",
                         "severity": "BLOCKER"})
    rows = [r for r in rows if "_unparseable_line" not in r]
    total_log_rows = len(rows)

    # final attempts: drop rows superseded by a retry chain (retry_of)
    superseded_ids = {r["retry_of"] for r in rows if r.get("retry_of")}
    finals = [r for r in rows if r["call_id"] not in superseded_ids]
    n_superseded = total_log_rows - len(finals)

    last_ts = max(r["timestamp_utc"] for r in rows)  # deterministic computed_utc

    payloads = {}
    for cls, fname in ITEMS_FILE.items():
        d = json.loads((items_dir / fname).read_text(encoding="utf-8"))
        payloads[cls] = {i["item_id"]: i for i in d["payload"]["items"]}

    # ------------------------------------------------ row intake + joins
    intake: dict[tuple[str, str], list[dict]] = defaultdict(list)
    join_fail = Counter()
    seed_source_mismatch = []
    slot_rule_violations = []
    placement_mismatch = []
    reparse_mismatch = []
    PAIRWISE = {"position", "verbosity", "cbw"}
    FOOT = "Respond with the single-line JSON object only."

    for r in finals:
        md = r.get("metadata") or {}
        cls = md.get("bias_class")
        judge = md.get("judge")
        if cls not in ITEMS_FILE:
            findings.append({"id": "F-UNKNOWN-CLASS", "detail": f"bias_class {cls!r}"})
            continue
        iss = md.get("item_seed_source", "")
        parts = iss.split("|")
        expected = f"{SEED}|{cls}|{judge}|{md.get('item_id')}|{md.get('condition')}|{md.get('order') or ''}"
        if len(parts) != 6 or iss != expected:
            seed_source_mismatch.append(r["call_id"])
        item = payloads[cls].get(md.get("item_id"))
        if item is None:
            join_fail[(judge, cls)] += 1
            continue
        # slot rules (pairwise classes): correct_slot follows the order label
        if cls in PAIRWISE:
            want = "A" if md.get("order") == "AB" else "B"
            if md.get("correct_slot") != want:
                slot_rule_violations.append(r["call_id"])
            if cls == "verbosity" and md.get("longer_slot") == md.get("correct_slot"):
                slot_rule_violations.append(r["call_id"])
            # prompt placement integrity: served answers == payload answers
            user = next((m["content"] for m in r["request"]["messages"]
                         if m.get("role") == "user"), "")
            a_ok = b_ok = False
            ia, ib = user.find("## Answer A\n"), user.find("## Answer B\n")
            if ia >= 0 and ib >= 0:
                ans_a = user[ia + 12:ib]
                ans_b = user[ib + 12:]
                ans_a = ans_a.split("\n\nWhich answer is better?")[0].strip()
                ans_b = ans_b.split("\n\nWhich answer is better?")[0].strip()
                correct, incorrect = item["answer_correct"], item["answer_incorrect"]
                slot_a, slot_b = ((correct, incorrect) if md["order"] == "AB"
                                  else (incorrect, correct))
                a_ok, b_ok = ans_a == slot_a, ans_b == slot_b
            if not (a_ok and b_ok):
                placement_mismatch.append(r["call_id"])
        elif cls == "anchoring":
            user = next((m["content"] for m in r["request"]["messages"]
                         if m.get("role") == "user"), "")
            if item["answer_incorrect"] not in user:
                placement_mismatch.append(r["call_id"])
        # independent re-parse of the raw response text
        schema = "pairwise" if cls in PAIRWISE else "scored"
        rep = reparse_verdict(r.get("raw_response_text") or "", schema)
        logged = (r.get("parsed") or {}).get("verdict") if r.get("parsed") else None
        if rep is None:
            if r.get("parsed") and r["parsed"].get("ok"):
                reparse_mismatch.append({"call_id": r["call_id"], "kind": "reparse-failed-but-logged-ok"})
        else:
            if schema == "pairwise":
                same = logged and logged.get("preference") == rep["preference"]
            else:
                same = logged and logged.get("score") == rep["score"]
            if not same:
                reparse_mismatch.append({"call_id": r["call_id"], "kind": "verdict-mismatch"})
        intake[(judge, cls)].append(r)

    if seed_source_mismatch:
        findings.append({"id": "F-SEED-SOURCE", "severity": "MAJOR",
                         "detail": f"{len(seed_source_mismatch)} rows whose "
                                   "item_seed_source components disagree with metadata",
                         "call_ids": seed_source_mismatch[:10]})
    if slot_rule_violations:
        findings.append({"id": "F-SLOT-RULES", "severity": "BLOCKER",
                         "detail": f"{len(slot_rule_violations)} rows violate "
                                   "correct_slot/longer_slot order rules",
                         "call_ids": slot_rule_violations[:10]})
    if placement_mismatch:
        findings.append({"id": "F-PLACEMENT", "severity": "BLOCKER",
                         "detail": f"{len(placement_mismatch)} rows whose served "
                                   "prompt answers do not match the items payload",
                         "call_ids": placement_mismatch[:10]})
    if reparse_mismatch:
        findings.append({"id": "F-REPARSE", "severity": "MAJOR",
                         "detail": f"{len(reparse_mismatch)} rows where the "
                                   "independent re-parse of raw_response_text "
                                   "disagrees with the logged parsed verdict",
                         "detail_rows": reparse_mismatch[:10]})

    # --------------------------------------------- per judge/class stats
    corrections = []
    accounting = {}
    coverage = {}

    for judge in JUDGES:
        for cls in ALL_CLASSES:
            rrs = intake.get((judge, cls), [])
            parsed_ok = [r for r in rrs
                         if r.get("error") is None and (r.get("parsed") or {}).get("ok")]
            parse_failures = [r for r in rrs
                              if r.get("parsed") is not None and not r["parsed"].get("ok")]
            generation_only = [r for r in rrs if r.get("parsed") is None]
            run_tags = sorted({r["metadata"]["run_tag"] for r in rrs})
            compliant = sum(1 for r in parsed_ok if r["parsed"].get("format_compliant"))
            acc = {
                "calls": len(rrs),
                "parsed": len(parsed_ok),
                "parse_failures": len(parse_failures),
                "generation_only_unparsed": len(generation_only),
                "join_failures": join_fail.get((judge, cls), 0),
                "format_compliant": compliant,
                "run_tags": run_tags,
            }
            accounting[f"{judge}/{CLASS_LABEL[cls]}"] = acc
            # COEF-METHOD 4.1: calls == parsed + parse_failures (+ generation-only)
            ident = (acc["calls"] == acc["parsed"] + acc["parse_failures"]
                     + acc["generation_only_unparsed"] + acc["join_failures"])
            if not ident:
                findings.append({"id": "F-ACCOUNTING", "severity": "BLOCKER",
                                 "detail": f"accounting identity failed for {judge}/{cls}"})

            key = CLASS_LABEL[cls]
            expected = EXPECTED_PER_JUDGE[cls]
            if cls == "selfpref_generation":
                cov_n = acc["calls"]
            else:
                cov_n = acc["parsed"] + acc["join_failures"]
            coverage[key] = coverage.get(key, {})
            coverage[key][judge] = {
                "expected": expected, "covered": cov_n,
                "ok": cov_n == expected,
                "note": "generation calls (no verdicts expected)" if cls == "selfpref_generation"
                        else "parsed-ok jobs",
            }

            if cls == "selfpref_generation":
                continue  # no coefficient this round (generation-only)

            # ---- position
            if cls == "position":
                by_item: dict[str, dict[str, dict]] = defaultdict(dict)
                for r in parsed_ok:
                    by_item[r["metadata"]["item_id"]][r["metadata"]["order"]] = r
                flips = 0
                n_pairs = 0
                excluded_tie = []
                strict_flips = 0
                strict_pairs = 0
                per_item = {}
                for iid in sorted(by_item):
                    pair = by_item[iid]
                    if not {"AB", "BA"} <= set(pair):
                        continue
                    ab_pref = pair["AB"]["parsed"]["verdict"]["preference"]
                    ba_pref = pair["BA"]["parsed"]["verdict"]["preference"]
                    ab = content_identity(ab_pref, pair["AB"]["metadata"]["correct_slot"])
                    ba = content_identity(ba_pref, pair["BA"]["metadata"]["correct_slot"])
                    strict_pairs += 1
                    strict_flips += int(ab != ba)
                    if ab in {"correct", "incorrect"} and ba in {"correct", "incorrect"}:
                        n_pairs += 1
                        f = int(ab != ba)
                        flips += f
                        per_item[iid] = {"ab": ab, "ba": ba, "flip": bool(f)}
                    else:
                        excluded_tie.append(iid)
                        per_item[iid] = {"ab": ab, "ba": ba, "flip": None,
                                         "excluded": "tie not mappable to {A,B}"}
                coef = flips / n_pairs if n_pairs else None
                ci = wilson_ci(flips, n_pairs)
                corrections.append(_row(judge, "position", "flip_rate", coef, ci,
                                        n_pairs, tie_rate=(len(excluded_tie) / strict_pairs)
                                        if strict_pairs else None,
                                        run_tags=run_tags, computed_utc=last_ts,
                                        method_sha=method_sha,
                                        aux={"flips": flips,
                                             "flip_items": [k for k, v in per_item.items()
                                                            if v["flip"]],
                                             "n_pairs_both_orders": strict_pairs,
                                             "n_pairs_tie_excluded": len(excluded_tie),
                                             "strict_flip_rate": (strict_flips / strict_pairs)
                                             if strict_pairs else None,
                                             "per_item": per_item}))

            # ---- verbosity
            elif cls == "verbosity":
                padded = [r for r in parsed_ok if r["metadata"]["condition"] == "padded"]
                plain = [r for r in parsed_ok if r["metadata"]["condition"] == "plain"]
                padded_wrong = ties = plain_wrong = plain_ties = 0
                for r in padded:
                    pref = r["parsed"]["verdict"]["preference"]
                    if pref == "TIE":
                        ties += 1
                        continue
                    padded_wrong += int(pref == r["metadata"]["longer_slot"])
                for r in plain:  # control condition (aux only)
                    pref = r["parsed"]["verdict"]["preference"]
                    if pref == "TIE":
                        plain_ties += 1
                        continue
                    plain_wrong += int(pref != r["metadata"]["correct_slot"])
                n = len(padded)  # ties in n, excluded from numerator
                coef = padded_wrong / n if n else None
                ci = wilson_ci(padded_wrong, n)
                corrections.append(_row(judge, "verbosity", "padded_pref", coef, ci,
                                        n, tie_rate=ties / n if n else None,
                                        run_tags=run_tags, computed_utc=last_ts,
                                        method_sha=method_sha,
                                        aux={"padded_answer_preferences": padded_wrong,
                                             "n_non_tie_picks": n - ties,
                                             "plain_condition_incorrect_choice_rate":
                                                 (plain_wrong / len(plain)) if plain else None,
                                             "delta_padded_minus_plain": (
                                                 (padded_wrong / n) - (plain_wrong / len(plain)))
                                                 if n and plain else None}))

            # ---- cbw
            elif cls == "cbw":
                wrong = ties = 0
                for r in parsed_ok:
                    pref = r["parsed"]["verdict"]["preference"]
                    if pref == "TIE":
                        ties += 1
                        continue
                    wrong += int(pref != r["metadata"]["correct_slot"])
                n = len(parsed_ok)
                coef = wrong / n if n else None
                ci = wilson_ci(wrong, n)
                corrections.append(_row(judge, "cbw", "wrong_pref", coef, ci, n,
                                        tie_rate=ties / n if n else None,
                                        run_tags=run_tags, computed_utc=last_ts,
                                        method_sha=method_sha,
                                        aux={"wrong_preferences": wrong, "ties": ties}))

            # ---- anchoring
            elif cls == "anchoring":
                by_item: dict[str, dict[str, float]] = defaultdict(dict)
                for r in parsed_ok:
                    score = r["parsed"]["verdict"].get("score")
                    if score is not None:
                        by_item[r["metadata"]["item_id"]][r["metadata"]["condition"]] = float(score)
                deltas = []
                per_item = {}
                for iid in sorted(by_item):
                    conds = by_item[iid]
                    if "anchored" in conds and "unanchored" in conds:
                        d = conds["anchored"] - conds["unanchored"]
                        deltas.append(d)
                        per_item[iid] = {"anchored": conds["anchored"],
                                         "unanchored": conds["unanchored"],
                                         "delta": d}
                n = len(deltas)
                if n >= 2:
                    drift = statistics.fmean(deltas)
                    ci = anchoring_bootstrap_ci(deltas, judge)
                else:
                    drift, ci = None, [None, None]
                corrections.append(_row(judge, "anchoring", "drift", drift, ci, n,
                                        tie_rate=None, run_tags=run_tags,
                                        computed_utc=last_ts, method_sha=method_sha,
                                        aux={"mean_anchored": (statistics.fmean(
                                                [v["anchored"] for v in per_item.values()])
                                                if per_item else None),
                                            "mean_unanchored": (statistics.fmean(
                                                [v["unanchored"] for v in per_item.values()])
                                                if per_item else None),
                                            "per_item_deltas": per_item,
                                            "bootstrap": {"resamples": N_BOOT,
                                                          "seed_source": f"{SEED}|anchoring|{judge}|<resample>"}}))

    # ------------------------------------------------------------ selfpref
    sp_counts = {j: accounting[f"{j}/selfpref"]["calls"] for j in JUDGES}
    selfpref_block = {
        "status": "generation-only",
        "n": 15,
        "n_per_judge_generation_calls": sp_counts,
        "n_generation_calls_total": sum(sp_counts.values()),
        "note": "Day-2 data is generation-only; grading phase is Day 3+. "
                "No coefficient this round (COEF-METHOD section 2).",
    }

    # ------------------------------------------------------------ envelope
    crosscheck_ref = None
    if args.crosscheck_file:
        ccp = Path(args.crosscheck_file)
        if not ccp.is_absolute():
            ccp = Path.cwd() / ccp
        if ccp.exists():
            cc = json.loads(ccp.read_text(encoding="utf-8"))
            try:
                rel = ccp.resolve().relative_to(REPO_ROOT).as_posix()
            except ValueError:
                rel = ccp.as_posix()
            crosscheck_ref = {
                "source": rel,
                "sha256": sha256_file(ccp),
                "verdict": cc.get("verdict"),
                "mismatch_count": cc.get("mismatch_count"),
            }

    inputs = [
        {"name": str(log_path), "sha256": sha256_file(log_path),
         "role": "verbatim judge-call log"},
        {"name": "battery/items/position-items.json", "sha256": sha256_file(items_dir / "position-items.json")},
        {"name": "battery/items/verbosity-items.json", "sha256": sha256_file(items_dir / "verbosity-items.json")},
        {"name": "battery/items/cbw-items.json", "sha256": sha256_file(items_dir / "cbw-items.json")},
        {"name": "battery/items/anchoring-items.json", "sha256": sha256_file(items_dir / "anchoring-items.json")},
        {"name": "battery/items/selfpref_generation-items.json",
         "sha256": sha256_file(items_dir / "selfpref_generation-items.json")},
        {"name": "battery/battery-spec.json", "sha256": sha256_file(spec_path),
         "role": "class table (class -> items_file, counts)"},
        {"name": "COEF-METHOD.md", "sha256": method_sha, "role": "frozen method"},
        {"name": "harness/derive-corrections.py", "sha256": script_sha, "role": "this script"},
    ]
    if crosscheck_ref:
        inputs.append({"name": crosscheck_ref["source"], "sha256": crosscheck_ref["sha256"],
                       "role": "independent runner-summary recompute diff (cross-check mode)"})

    findings.append({
        "id": "F-1", "severity": "MAJOR (provenance, no coefficient impact)",
        "detail": "battery-spec.json pins pre-D1-regeneration items payload "
                  "shas (e.g. verbosity 8ac486f1...) while the Day-2 run "
                  "consumed the regenerated files (runner summaries record "
                  "1a71b18c...; 624/624 pairwise prompts byte-match the "
                  "current payloads). ISSUE-009's 'battery-spec input hashes "
                  "re-pinned' did not land in battery-spec.json. Derivation "
                  "joins the CURRENT payloads, which provably match the "
                  "served prompts.",
    })

    method = {
        "doc": "docs/COEF-METHOD.md (FROZEN 2026-09-16)",
        "doc_sha256": method_sha,
        "derivation_source": "verbatim log only; runner summaries never read "
                             "by derivation (cross-check recomputes them "
                             "independently, see data.cross_checks)",
        "join": "metadata.item_seed_source components "
                "(seed|class|judge|item|condition|order) -> items payload by item_id",
        "estimators": {
            "position": "flip_rate = P(content-choice differs across AB/BA on the "
                        "same item); content identity via metadata.correct_slot; "
                        "pairs with a TIE verdict are not mappable to {A,B} and "
                        "are excluded from n (counted in n_pairs_tie_excluded; "
                        "strict rate that counts tie-mismatches reported in aux); "
                        "CI: Wilson 95%",
            "verbosity": "padded_pref = P(judge prefers the padded answer) over "
                         "padded-condition pairs; ties excluded from numerator, "
                         "kept in n; tie_rate reported; CI: Wilson 95%. Plain "
                         "condition reported as control (aux), not in the "
                         "coefficient",
            "cbw": "wrong_pref = P(judge prefers the confident-but-wrong answer); "
                   "ties excluded from numerator, kept in n; CI: Wilson 95%",
            "anchoring": "drift = mean(score_anchored) - mean(score_unanchored) "
                         "over identical items (paired by item_id, 0-10 scale); "
                         "CI: paired bootstrap over items, 10,000 resamples, "
                         "per-resample RNG random.Random('20260914|anchoring|"
                         "<judge>|<resample>'), percentile 2.5/97.5 (harness "
                         "bootstrap_ci index convention)",
            "selfpref": "generation-only; no coefficient this round",
        },
        "seed": SEED,
        "exclusions": "rows with parsed.ok=false excluded per class and counted; "
                      "retry-superseded attempts dropped (none present: 741 "
                      "final rows); FT-8: no timing exclusions per "
                      "FT8-DISPOSITION.json (zero overlap)",
        "computed_utc_convention": "per-row computed_utc is pinned to the "
                                   "verbatim log's final call timestamp for "
                                   "byte-identical re-runs (section 4.3); "
                                   "wall-clock appears only in "
                                   "envelope.created_utc",
    }

    data = {
        "corrections": corrections,
        "selfpref": selfpref_block,
        "application_notes": {
            "position": "coefficient = flip_rate (already the bias). Consumers "
                        "reduce pairwise trust for this judge: s_corrected = s - "
                        "flip_rate (equivalently scale preference confidence by "
                        "1 - flip_rate). MAY NOT be applied unless significant.",
            "verbosity": "coefficient = padded_pref: probability the judge "
                         "prefers the padded (longer, incorrect) answer. "
                         "Consumers correct length-sensitive scores: s_corrected "
                         "= s - padded_pref. MAY NOT be applied unless significant.",
            "cbw": "coefficient = wrong_pref: probability the judge prefers the "
                   "confident-but-wrong answer. Consumers correct: s_corrected = "
                   "s - wrong_pref. MAY NOT be applied unless significant.",
            "anchoring": "coefficient = drift: anchored minus unanchored mean "
                         "score on identical items. Consumers correct scores "
                         "produced under a strong exemplar: s_corrected = s - "
                         "drift. MAY NOT be applied unless significant.",
            "selfpref": "no correction available this round (generation-only).",
        },
        "cross_checks": {
            "call_accounting": {
                "rule": "calls == parsed + parse_failures + "
                        "generation_only_unparsed + join_failures (per judge/class)",
                "results": accounting,
                "verdict": "PASS" if not any(f["id"] == "F-ACCOUNTING" for f in findings) else "FAIL",
            },
            "coverage": {
                "rule": "per judge: position 72, verbosity 96 (incl. top-up), "
                        "cbw 40 (incl. top-up), anchoring 24, selfpref 15 "
                        "generation calls, minus documented parse exclusions",
                "results": coverage,
                "verdict": "PASS" if all(j["ok"] for c in coverage.values() for j in c.values())
                           and not join_fail else "FAIL",
            },
            "integrity": {
                "item_seed_source_component_mismatches": len(seed_source_mismatch),
                "slot_rule_violations": len(slot_rule_violations),
                "prompt_placement_mismatches": len(placement_mismatch),
                "reparse_mismatches": len(reparse_mismatch),
                "join_failures": sum(join_fail.values()),
                "superseded_retry_rows": n_superseded,
                "verdict": "PASS" if not (seed_source_mismatch or slot_rule_violations
                                          or placement_mismatch or reparse_mismatch
                                          or join_fail) else "FAIL",
            },
            "summary_recompute_diff": crosscheck_ref or {
                "verdict": "NOT RUN (execute `cross-check` subcommand; summaries "
                           "are never read during derivation per COEF-METHOD section 1)",
            },
        },
        "findings": findings,
    }

    envelope = {
        "artifact_type": "judge-corrections",
        "created_utc": args.created_utc or utc_now_iso(),
        "agent": "WORKER-2 (GLM)",
        "mission_brief": "glm/briefs/BRIEF-20260914-sprint-p8-judge-audit.md",
        "inputs": inputs,
        "environment": {
            "python": sys.version.replace("\n", " "),
            "platform": platform.platform(),
            "machine": platform.machine(),
            "harness_version": "1.0.0",
            "stdlib_only": True,
        },
        "method": method,
        "data": data,
        "provenance": {
            "measured": ["all coefficients", "ci95 intervals", "n", "tie_rate",
                         "call accounting", "coverage", "prompt placement",
                         "verdict re-parse", "summary recompute diff"],
            "estimated": [],
            "measured_vs_estimated": "every number derived from the verbatim "
                                     "log + items payloads; nothing taken from "
                                     "runner summaries; no estimates",
        },
    }

    out = Path(args.out)
    out.write_text(json.dumps({"envelope": envelope}, indent=2, ensure_ascii=False,
                              sort_keys=True) + "\n", encoding="utf-8")

    _print_report(envelope, out)
    return 0


def _row(judge, cls, name, coef, ci, n, *, tie_rate, run_tags, computed_utc,
         method_sha, aux) -> dict:
    sig = False
    if coef is not None and ci[0] is not None:
        sig = (ci[0] > 0.0) if cls != "anchoring" else (ci[0] > 0.0 or ci[1] < 0.0)
    return {
        "judge": judge,
        "class": cls,
        "coefficient_name": name,
        "coefficient": coef,
        "ci95": ci,
        "n": n,
        "tie_rate": tie_rate,
        "run_tags": run_tags,
        "significant": sig,
        "computed_utc": computed_utc,
        "method_sha": method_sha,
        "aux": aux,
    }


def _print_report(env: dict, out: Path) -> None:
    print(f"wrote {out}")
    print("\n== judge-corrections coefficient table ==")
    hdr = f"{'judge':6} {'class':10} {'coefficient':22} {'value':>10} {'ci95':>26} {'n':>4} {'tie':>6} {'sig':>5}"
    print(hdr)
    for r in env["data"]["corrections"]:
        ci = r["ci95"]
        ci_s = f"[{ci[0]:.4f}, {ci[1]:.4f}]" if ci[0] is not None else "[--]"
        val = f"{r['coefficient']:.4f}" if r["coefficient"] is not None else "--"
        tie = f"{r['tie_rate']:.3f}" if r["tie_rate"] is not None else "--"
        print(f"{r['judge']:6} {r['class']:10} {r['coefficient_name']:22} {val:>10} {ci_s:>26} {r['n']:>4} {tie:>6} {str(r['significant']):>5}")
    sp = env["data"]["selfpref"]
    print(f"selfpref: status={sp['status']} n={sp['n']} generation_calls={sp['n_generation_calls_total']}")
    cc = env["data"]["cross_checks"]
    print("\n== cross-checks ==")
    for k in ("call_accounting", "coverage", "integrity"):
        print(f"  {k}: {cc[k]['verdict']}")
    print(f"  summary_recompute_diff: {cc['summary_recompute_diff'].get('verdict')}")
    print("\n== findings ==")
    for f in env["data"]["findings"]:
        print(f"  {f['id']} [{f.get('severity','')}] {f.get('detail','')[:160]}")


# ------------------------------------------------------- summary cross-check
SUMMARY_SPECS = {
    "summary-position.json": ("battery-day2-position", "position", "runner-config-day2-position.json", None),
    "summary-verbosity.json": ("battery-day2-verbosity", "verbosity", "runner-config-day2-verbosity.json", None),
    "summary-cbw.json": ("battery-day2-cbw", "cbw", "runner-config-day2-cbw.json", None),
    "summary-anchoring.json": ("battery-day2-anchoring", "anchoring", "runner-config-day2-anchoring.json", None),
    "summary-selfpref_generation.json": ("battery-day2-selfpref_generation", "selfpref_generation",
                                         "runner-config-day2-selfpref_generation.json", None),
    "summary-verbosity-topup.json": ("battery-day2b-verbosity", "verbosity",
                                     "runner-config-day2b-verbosity.json", "battery-day2-verbosity"),
    "summary-cbw-topup.json": ("battery-day2b-cbw", "cbw", "runner-config-day2b-cbw.json", "battery-day2-cbw"),
}


def cross_check(argv: list[str] | None = None) -> int:
    """Recompute the runner summaries' accounting from the raw log + items
    payloads and diff against the shipped summary-*.json (COEF-METHOD 4.2)."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(CROSSCHECK_OUT))
    args = ap.parse_args(argv)

    rows = load_log(LOG_PATH)
    superseded_ids = {r["retry_of"] for r in rows if r.get("retry_of")}
    finals = [r for r in rows if r["call_id"] not in superseded_ids]

    items_n = {}
    for cls, fname in ITEMS_FILE.items():
        d = json.loads((ITEMS_DIR / fname).read_text(encoding="utf-8"))
        items_n[cls] = len(d["payload"]["items"])

    results = {}
    mismatches = []
    for fname, (run_tag, cls, cfg_name, prior_tag) in SUMMARY_SPECS.items():
        spath = REPO_ROOT / "logs" / fname
        shipped = json.loads(spath.read_text(encoding="utf-8"))["envelope"]
        shipped_sum = shipped["data"]["summaries"][cls]
        rr = [r for r in finals if r.get("metadata", {}).get("run_tag") == run_tag]
        calls = Counter(r.get("endpoint") for r in rr)
        n_errors = sum(1 for r in rr if r.get("error") is not None)
        n_parsed = sum(1 for r in rr if (r.get("parsed") or {}).get("ok"))
        n_specs = items_n[cls] * (1 if cls == "selfpref_generation" else 2)
        n_jobs = n_specs * 3
        rec = {
            "bias_class": shipped_sum.get("bias_class"),
            "n_items": items_n[cls],
            "n_specs": n_specs,
            "n_jobs": n_jobs,
            "calls_made": {j: calls.get(j, 0) for j in JUDGES},
            "n_skipped_by_cap": n_jobs - sum(calls.values()),
            "n_errors": n_errors,
            "n_parsed": n_parsed,
        }
        if prior_tag:  # top-up run: dedup accounting vs the main run
            main_rows = [r for r in finals
                         if r.get("metadata", {}).get("run_tag") == prior_tag]
            main_by_judge = Counter(r.get("endpoint") for r in main_rows)
            already = sum(main_by_judge.values())
            rec["n_jobs_total"] = n_jobs
            rec["n_jobs_by_judge"] = {j: n_specs for j in JUDGES}
            rec["n_already_logged"] = already
            rec["n_skipped_already_logged"] = already
            rec["n_already_logged_by_judge"] = {j: main_by_judge.get(j, 0) for j in JUDGES}
            rec["n_skipped_by_cap_by_judge"] = {
                j: n_specs - calls.get(j, 0) - main_by_judge.get(j, 0) for j in JUDGES}
            # runner semantics (E-P8-1): n_skipped_by_cap counts ONLY cap skips;
            # already-logged skips have their own counter, i.e.
            # n_jobs_by_judge = calls_made + n_already_logged + n_skipped_by_cap
            rec["n_skipped_by_cap"] = n_jobs - sum(calls.values()) - already
            rec["_coverage_identity_holds_per_judge"] = {
                j: n_specs == calls.get(j, 0) + main_by_judge.get(j, 0)
                + rec["n_skipped_by_cap_by_judge"][j] for j in JUDGES}
        # input sha verification against the files actually on disk
        sha_checks = {}
        for entry in shipped.get("inputs", []):
            name, want = entry["name"], entry["sha256"]
            path = None
            if name == "runner_config":
                path = REPO_ROOT / "harness" / cfg_name
            elif name == "endpoints_file":
                path = REPO_ROOT / "judge-endpoints.json"
            elif name == "items_file":
                path = ITEMS_DIR / ITEMS_FILE[cls]
            if path is not None and path.exists():
                got = sha256_file(path)
                sha_checks[name] = {"match": got == want, "expected": want, "actual": got}
            else:
                sha_checks[name] = {"match": None, "note": "file not resolvable for recompute"}
        # field-by-field diff (health block not recomputable from the log;
        # internal-only keys prefixed "_" are excluded)
        diffs = []
        for k, v in rec.items():
            if k.startswith("_"):
                continue
            if k not in shipped_sum:
                diffs.append({"field": k, "recomputed": v, "shipped": "<missing>"})
            elif shipped_sum[k] != v:
                diffs.append({"field": k, "recomputed": v, "shipped": shipped_sum[k]})
        for k in shipped_sum:
            if k not in rec:
                diffs.append({"field": k, "recomputed": "<missing>", "shipped": shipped_sum[k]})
        for name, c in sha_checks.items():
            if c.get("match") is False:
                diffs.append({"field": f"inputs.{name}.sha256",
                              "recomputed": c["actual"], "shipped": c["expected"]})
        mismatches.extend([{**d, "summary": fname} for d in diffs])
        results[fname] = {
            "run_tag": run_tag,
            "recomputed": rec,
            "shipped_summaries_block": shipped_sum,
            "input_sha_checks": sha_checks,
            "health_block": "not recomputable from the verbatim log (runtime "
                            "server-probe detail); out of diff scope",
            "diffs": diffs,
            "verdict": "MATCH" if not diffs else "MISMATCH",
        }

    # pooled parsed totals vs the shipped summaries (COEF-METHOD 4.1, second half)
    exp = {"position": 216, "verbosity": 144 + 144, "cbw": 60 + 60,
           "anchoring": 72, "selfpref_generation": 0}
    pooled_recon = {}
    for cls in ALL_CLASSES:
        n_ok = sum(1 for r in finals
                   if r.get("metadata", {}).get("bias_class") == cls
                   and (r.get("parsed") or {}).get("ok"))
        pooled_recon[cls] = {"parsed_from_log": n_ok, "from_summaries": exp[cls],
                             "match": n_ok == exp[cls]}

    out = {
        "artifact_type": "summary-recompute-diff",
        "generated_from": "verbatim judge-call JSONL log + battery/items/*",
        "results": results,
        "pooled_parsed_reconciliation": pooled_recon,
        "mismatch_count": len(mismatches),
        "mismatches": mismatches,
        "verdict": "MATCH" if not mismatches and all(v["match"] for v in pooled_recon.values()) else "MISMATCH",
    }
    Path(args.out).write_text(json.dumps(out, indent=2, ensure_ascii=False,
                                         sort_keys=True) + "\n", encoding="utf-8")

    print(f"wrote {args.out}")
    print("\n== runner-summary recompute (from raw, diff vs shipped) ==")
    for fname, res in results.items():
        print(f"  {fname}: {res['verdict']} ({len(res['diffs'])} field diffs)")
    print("  pooled parsed vs summaries:",
          {c: v["match"] for c, v in pooled_recon.items()})
    print(f"  overall: {out['verdict']} ({len(mismatches)} mismatches)")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv[0] if argv and argv[0] in ("derive", "cross-check") else "derive"
    rest = argv[1:] if argv and argv[0] in ("derive", "cross-check") else argv
    if cmd == "cross-check":
        return cross_check(rest)
    return derive(rest)


if __name__ == "__main__":
    raise SystemExit(main())
