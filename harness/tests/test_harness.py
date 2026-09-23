"""Offline unit tests for the P8 judge harness. No live judge calls.

Runs a stdlib http.server mock of the OpenAI chat-completions API and
exercises: client logging/retry schema, verdict parsing + strict format
compliance, seeded randomization determinism, metrics math, and the
run_battery orchestrator end-to-end (incl. per-judge call caps).

Run:  python -m unittest tests.test_harness -v     (from harness/)
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HARNESS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HARNESS_DIR))

from envelope import build_envelope  # noqa: E402
from judge_client import EndpointConfig, JudgeClient, load_endpoints  # noqa: E402
from metrics import (bootstrap_ci, cohen_kappa, format_compliance_rate,  # noqa: E402
                     load_log, pairwise_kappa_from_log, position_flips,
                     score_drift, verbosity_effect)
from parse import parse_judgment, parse_pairwise, parse_scored  # noqa: E402
import randomize as rnd  # noqa: E402
import run_battery as rb  # noqa: E402


# ================================================================ mock server
GOOD_PAIRWISE = '{"preference": "A", "confidence": 0.8, "reasoning_short": "A is correct."}'
GOOD_SCORED = '{"score": 7.5, "confidence": 0.9, "reasoning_short": "Mostly right."}'

BEHAVIOR_TEXTS = {
    "ok": GOOD_PAIRWISE,
    "score": GOOD_SCORED,
    "think": "<think>The user wants me to compare. Let me think hard about this.\nAfter thinking, A is right.</think>\n" + GOOD_PAIRWISE,
    "prose": "Comparing both answers, answer A computes the sum correctly while B drops a term.\n" + GOOD_PAIRWISE,
    "fenced": "```json\n" + GOOD_PAIRWISE + "\n```",
    "invalid_pref": '{"preference": "B (probably)", "confidence": 0.7, "reasoning_short": "hm"}',
    "missing_conf": '{"preference": "A", "reasoning_short": "ok"}',
    "conf_out_of_range": '{"preference": "A", "confidence": 1.5, "reasoning_short": "ok"}',
    "conf_string": '{"preference": "A", "confidence": "0.8", "reasoning_short": "ok"}',
    "no_reasoning": '{"preference": "A", "confidence": 0.8}',
    "nojson": "Answer A is better because it is correct.",
    "empty": "",
}


class MockJudgeServer:
    """Scripted OpenAI-compatible mock. behaviors are consumed in order;
    the last one repeats. Records every received chat request body."""

    def __init__(self) -> None:
        self.behaviors: list[str] = ["ok"]
        self.requests: list[dict] = []
        self.lock = threading.Lock()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # silence
                pass

            def do_GET(self):
                if self.path.endswith("/v1/models"):
                    body = json.dumps({"models": [{"name": "mock"}]}).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_response(404)
                    self.end_headers()

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length)
                with outer.lock:
                    outer.requests.append(json.loads(raw))
                    behavior = (outer.behaviors.pop(0)
                                if len(outer.behaviors) > 1
                                else outer.behaviors[0])
                if behavior == "fail500":
                    self._send(500, json.dumps({"error": "boom"}))
                    return
                if behavior == "fail400":
                    self._send(400, json.dumps({"error": "bad request"}))
                    return
                if behavior == "slow":
                    time.sleep(1.5)
                text = BEHAVIOR_TEXTS.get(behavior, GOOD_PAIRWISE)
                resp = {
                    "id": "chatcmpl-mock", "object": "chat.completion",
                    "model": "mock",
                    "choices": [{"index": 0, "finish_reason": "stop",
                                 "message": {"role": "assistant",
                                             "content": text}}],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 30,
                              "total_tokens": 130},
                }
                self._send(200, json.dumps(resp))

            def _send(self, code: int, body: str):
                data = body.encode()
                try:
                    self.send_response(code)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError,
                        ConnectionAbortedError, OSError):
                    pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever,
                                       daemon=True)
        self.thread.start()

    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def next_behavior(self, *behaviors: str) -> None:
        with self.lock:
            self.behaviors = list(behaviors)

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


# ================================================================== helpers
def make_record(judge: str, item_id: str, order: str, pref: str,
                bias_class: str = "position", condition: str = "standard",
                correct_slot: str = "A") -> dict:
    parsed = None
    if pref is not None:
        parsed = {"ok": True, "format_compliant": True,
                  "verdict": {"preference": pref, "confidence": 0.9,
                              "reasoning_short": "x"},
                  "error": None, "schema": "pairwise",
                  "think_block_present": False, "raw_keys": []}
    return {
        "call_id": f"{judge}-{item_id}-{order}", "retry_of": None,
        "error": None, "parsed": parsed,
        "metadata": {"judge": judge, "item_id": item_id, "order": order,
                     "bias_class": bias_class, "condition": condition,
                     "correct_slot": correct_slot, "schema": "pairwise"},
    }


# ==================================================================== tests
class TestParse(unittest.TestCase):
    def test_clean_ok_compliant(self):
        r = parse_judgment(GOOD_PAIRWISE)
        self.assertTrue(r["ok"] and r["format_compliant"])
        self.assertEqual(r["verdict"]["preference"], "A")
        self.assertFalse(r["think_block_present"])

    def test_fenced_ok(self):
        r = parse_judgment(BEHAVIOR_TEXTS["fenced"])
        self.assertTrue(r["ok"] and r["format_compliant"])

    def test_think_wrapped(self):
        r = parse_judgment(BEHAVIOR_TEXTS["think"])
        self.assertTrue(r["ok"])
        self.assertTrue(r["think_block_present"])
        self.assertEqual(r["verdict"]["preference"], "A")

    def test_prose_wrapped(self):
        r = parse_judgment(BEHAVIOR_TEXTS["prose"])
        self.assertTrue(r["ok"] and r["format_compliant"])

    def test_tie_case_insensitive(self):
        r = parse_judgment('{"preference": "Tie", "confidence": 0, "reasoning_short": ""}')
        self.assertTrue(r["ok"] and r["format_compliant"])
        self.assertEqual(r["verdict"]["preference"], "TIE")

    def test_invalid_preference_not_ok(self):
        r = parse_judgment(BEHAVIOR_TEXTS["invalid_pref"])
        self.assertFalse(r["ok"])

    def test_missing_confidence_parsed_but_not_compliant(self):
        r = parse_judgment(BEHAVIOR_TEXTS["missing_conf"])
        self.assertTrue(r["ok"])
        self.assertFalse(r["format_compliant"])
        self.assertIsNone(r["verdict"]["confidence"])

    def test_conf_out_of_range_not_compliant(self):
        r = parse_judgment(BEHAVIOR_TEXTS["conf_out_of_range"])
        self.assertTrue(r["ok"])
        self.assertFalse(r["format_compliant"])

    def test_conf_string_parsed_but_not_compliant(self):
        r = parse_judgment(BEHAVIOR_TEXTS["conf_string"])
        self.assertTrue(r["ok"])
        self.assertFalse(r["format_compliant"])

    def test_missing_reasoning_not_compliant(self):
        r = parse_judgment(BEHAVIOR_TEXTS["no_reasoning"])
        self.assertTrue(r["ok"])
        self.assertFalse(r["format_compliant"])

    def test_no_json(self):
        r = parse_judgment(BEHAVIOR_TEXTS["nojson"])
        self.assertFalse(r["ok"])

    def test_empty(self):
        r = parse_judgment(BEHAVIOR_TEXTS["empty"])
        self.assertFalse(r["ok"])

    def test_scored_schema(self):
        r = parse_judgment(GOOD_SCORED, schema="scored")
        self.assertTrue(r["ok"] and r["format_compliant"])
        self.assertEqual(r["verdict"]["score"], 7.5)

    def test_scored_out_of_range(self):
        r = parse_judgment('{"score": 12, "confidence": 0.5, "reasoning_short": "x"}',
                           schema="scored")
        self.assertFalse(r["ok"])

    def test_last_object_wins(self):
        text = ('{"note": "thinking out loud"} then final: '
                '{"preference": "B", "confidence": 0.6, "reasoning_short": "final"}')
        r = parse_judgment(text)
        self.assertEqual(r["verdict"]["preference"], "B")


class TestClient(unittest.TestCase):
    def setUp(self):
        self.server = MockJudgeServer()
        self.tmp = Path(tempfile.mkdtemp(prefix="p8test-"))
        self.log = self.tmp / "calls.jsonl"

    def tearDown(self):
        self.server.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _client(self, **kw):
        ep = EndpointConfig(name="mock", base_url=self.server.url(),
                            model="mock-1", timeout_s=kw.pop("timeout_s", 10))
        return JudgeClient(ep, self.log, parser=None, **kw)

    def test_health(self):
        c = self._client()
        self.assertTrue(c.health()["healthy"])

    def test_success_record_schema(self):
        from parse import parse_pairwise
        c = JudgeClient(self._client().endpoint, self.log, parser=parse_pairwise)
        msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "u"}]
        rec = c.chat(msgs, temperature=0.3, seed=42, max_tokens=128,
                     metadata={"bias_class": "position", "item_id": "it-1"})
        for key in ("call_id", "timestamp_utc", "endpoint", "model_id",
                    "raw_response_text", "finish_reason", "token_usage",
                    "latency_ms", "seed" if False else "request", "error",
                    "harness", "metadata", "parsed"):
            self.assertIn(key, rec)
        self.assertEqual(rec["raw_response_text"], GOOD_PAIRWISE)  # verbatim
        self.assertEqual(rec["finish_reason"], "stop")
        self.assertEqual(rec["token_usage"]["total_tokens"], 130)
        self.assertEqual(rec["request"]["temperature"], 0.3)
        self.assertEqual(rec["request"]["seed"], 42)
        self.assertEqual(rec["request"]["messages"], msgs)  # verbatim prompt
        self.assertTrue(rec["parsed"]["ok"])
        self.assertEqual(rec["metadata"]["item_id"], "it-1")
        self.assertIn("judge_client.py", rec["harness"]["code_sha256"])
        # JSONL on disk matches
        lines = load_log(self.log)
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["call_id"], rec["call_id"])

    def test_retry_on_500_logged(self):
        self.server.next_behavior("fail500", "ok")
        c = self._client(retry_backoff_s=0.05)
        rec = c.chat([{"role": "user", "content": "hi"}])
        lines = load_log(self.log)
        self.assertEqual(len(lines), 2)  # both attempts logged
        self.assertEqual(lines[0]["http_status"], 500)
        self.assertIsNotNone(lines[0]["error"])
        self.assertEqual(lines[1]["retry_of"], lines[0]["call_id"])
        self.assertIsNone(rec["error"])

    def test_no_retry_on_400(self):
        self.server.next_behavior("fail400", "ok")
        c = self._client(retry_backoff_s=0.05)
        rec = c.chat([{"role": "user", "content": "hi"}])
        lines = load_log(self.log)
        self.assertEqual(len(lines), 1)  # 4xx: not retried
        self.assertIsNotNone(rec["error"])

    def test_timeout_retries_then_errors(self):
        self.server.next_behavior("slow", "slow")
        c = self._client(timeout_s=0.4, retry_backoff_s=0.05)
        t0 = time.monotonic()
        rec = c.chat([{"role": "user", "content": "hi"}])
        self.assertIsNotNone(rec["error"])
        lines = load_log(self.log)
        self.assertEqual(len(lines), 2)  # 1 retry max
        self.assertLess(time.monotonic() - t0, 8)

    def test_endpoint_config_loader(self):
        p = self.tmp / "eps.json"
        p.write_text(json.dumps({"endpoints": [
            {"name": "a", "base_url": "http://x:1/", "model": "m"}]}),
            encoding="utf-8")
        eps = load_endpoints(p)
        self.assertEqual(eps["a"].base_url, "http://x:1")
        self.assertEqual(eps["a"].timeout_s, 240.0)

    def test_endpoint_loader_orchestrator_envelope(self):
        p = self.tmp / "orch.json"
        p.write_text(json.dumps({
            "artifact_type": "judge-endpoints",
            "inputs": {"models": [
                {"judge": "glm",
                 "model_path": "A:\\m\\glm-4-9b-chat-Q4_K_M.gguf",
                 "source_file": "glm-4-9b-chat-Q4_K_M.gguf", "port": 8091},
                {"judge": "qwen", "source_file": "qwen3 8B Q4_K_M",
                 "port": 8093},
            ]},
            "data": {"ports": {"glm": 8091, "qwen": 8093}},
        }), encoding="utf-8")
        eps = load_endpoints(p)
        self.assertEqual(eps["glm"].base_url, "http://127.0.0.1:8091")
        self.assertEqual(eps["glm"].model, "glm-4-9b-chat")
        self.assertEqual(eps["qwen"].model, "qwen3-8b")


class TestRandomize(unittest.TestCase):
    def test_deterministic_across_calls(self):
        a = rnd.shuffled(list(range(20)), 12345, "t")
        b = rnd.shuffled(list(range(20)), 12345, "t")
        self.assertEqual(a, b)
        self.assertNotEqual(a, list(range(20)))  # actually shuffled

    def test_pick_order_distribution_and_determinism(self):
        o1 = [rnd.pick_order(7, f"i{i}") for i in range(50)]
        o2 = [rnd.pick_order(7, f"i{i}") for i in range(50)]
        self.assertEqual(o1, o2)
        self.assertIn("AB", o1)
        self.assertIn("BA", o1)

    def test_both_orders_content(self):
        item = {"item_id": "x", "question": "q?",
                "answer_correct": "RIGHT", "answer_incorrect": "WRONG"}
        ab, ba = rnd.both_orders(item)
        self.assertEqual((ab["order"], ab["slot_A"], ab["slot_B"],
                          ab["correct_slot"]), ("AB", "RIGHT", "WRONG", "A"))
        self.assertEqual((ba["order"], ba["slot_A"], ba["slot_B"],
                          ba["correct_slot"]), ("BA", "WRONG", "RIGHT", "B"))

    def test_assign_conditions_balanced(self):
        m = rnd.assign_conditions([f"s{i}" for i in range(10)],
                                  ["anchored", "unanchored"], 99, "tag")
        vals = sorted(m.values())
        self.assertEqual(vals.count("anchored"), 5)
        self.assertEqual(vals.count("unanchored"), 5)
        m2 = rnd.assign_conditions([f"s{i}" for i in range(10)],
                                   ["anchored", "unanchored"], 99, "tag")
        self.assertEqual(m, m2)


class TestMetrics(unittest.TestCase):
    def test_format_compliance(self):
        recs = [make_record("j", "i1", "AB", "A"),
                make_record("j", "i2", "AB", "B")]
        bad = dict(make_record("j", "i3", "AB", "A"))
        bad["parsed"] = {"ok": True, "format_compliant": False,
                         "verdict": {"preference": "A", "confidence": None,
                                     "reasoning_short": None}}
        s = format_compliance_rate(recs + [bad])
        self.assertEqual(s["n_calls"], 3)
        self.assertAlmostEqual(s["format_compliance_rate"], 2 / 3)

    def test_retry_superseded_dropped(self):
        a = make_record("j", "i1", "AB", "A")
        b = dict(make_record("j", "i1", "AB", "B"))
        b["call_id"] = a["call_id"] + "-r2"
        b["retry_of"] = a["call_id"]
        s = format_compliance_rate([a, b])
        self.assertEqual(s["n_calls"], 1)

    def test_position_flips(self):
        recs = [
            make_record("j", "i1", "AB", "A", correct_slot="A"),   # correct
            make_record("j", "i1", "BA", "A", correct_slot="B"),   # incorrect -> FLIP
            make_record("j", "i2", "AB", "A", correct_slot="A"),   # correct
            make_record("j", "i2", "BA", "B", correct_slot="B"),   # correct -> no flip
        ]
        m = position_flips(recs)
        self.assertEqual(m["n_pairs_both_parsed"], 2)
        self.assertAlmostEqual(m["flip_rate_strict"], 0.5)
        self.assertAlmostEqual(m["flip_rate_choice"], 0.5)
        # first-slot preference: 3 of 4 non-tie calls picked slot A
        self.assertAlmostEqual(m["first_slot_preference_rate"], 0.75)

    def test_position_tie_counts_strict_only(self):
        recs = [make_record("j", "i1", "AB", "A", correct_slot="A"),
                make_record("j", "i1", "BA", "TIE", correct_slot="B")]
        m = position_flips(recs)
        self.assertAlmostEqual(m["flip_rate_strict"], 1.0)
        self.assertIsNone(m["flip_rate_choice"])  # no non-tie pair

    def test_verbosity_effect(self):
        recs = [
            make_record("j", "i1", "AB", "B", bias_class="verbosity",
                        condition="plain", correct_slot="A"),
            make_record("j", "i1", "AB", "B", bias_class="verbosity",
                        condition="padded", correct_slot="A"),
        ]
        # plain: picked B=incorrect; padded: picked B=incorrect
        m = verbosity_effect(recs)
        self.assertAlmostEqual(m["incorrect_choice_rate_plain"], 1.0)
        self.assertAlmostEqual(m["incorrect_choice_rate_padded"], 1.0)
        self.assertAlmostEqual(m["verbosity_delta"], 0.0)

    def test_kappa_perfect_and_known(self):
        self.assertAlmostEqual(cohen_kappa(["A"] * 5 + ["B"] * 5,
                                           ["A"] * 5 + ["B"] * 5)["kappa"], 1.0)
        labels1 = ["A"] * 20 + ["B"] * 20 + ["C"] * 10
        labels2 = ["A"] * 15 + ["B"] * 25 + ["C"] * 10
        # po = 45/50, pe = (.4*.3)+(.4*.5)+(.2*.2) = .12+.20+.04=.36
        expected = (0.9 - 0.36) / (1 - 0.36)
        self.assertAlmostEqual(cohen_kappa(labels1, labels2)["kappa"], expected)

    def test_kappa_degenerate(self):
        r = cohen_kappa(["A", "A"], ["A", "A"])
        self.assertIsNone(r["kappa"])

    def test_kappa_shared_cells_counted_once(self):
        # REVIEWER regression (2026-09-14): each shared (item, order) cell
        # must count ONCE per judge pair, not once per third judge.
        recs = []
        for judge in ("a", "b", "c"):
            for item in ("i1", "i2"):
                for order, pref in (("AB", "A"), ("BA", "B")):
                    recs.append(make_record(judge, item, order, pref))
        out = pairwise_kappa_from_log(recs)
        for pair in out.values():
            self.assertEqual(pair["n"], 4)  # 2 items x 2 orders
        self.assertAlmostEqual(out["a__b"]["kappa"], 1.0)

    def test_bootstrap_deterministic(self):
        vals = [1.0, 2.0, 3.0, 4.0, 5.0]
        c1 = bootstrap_ci(vals, seed=1)
        c2 = bootstrap_ci(vals, seed=1)
        self.assertEqual(c1, c2)
        self.assertLessEqual(c1[0], 3.0)
        self.assertGreaterEqual(c1[1], 3.0)

    def test_score_drift(self):
        d = score_drift([8, 8, 8], [6, 6, 6])
        self.assertAlmostEqual(d["drift"], 2.0)
        self.assertEqual(len(d["drift_ci95"]), 2)


class TestRunBatteryEndToEnd(unittest.TestCase):
    def setUp(self):
        self.server = MockJudgeServer()
        self.tmp = Path(tempfile.mkdtemp(prefix="p8run-"))

    def tearDown(self):
        self.server.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_items(self):
        items = [{"item_id": f"pos-{i}",
                  "question": f"Question {i}?",
                  "answer_correct": "The correct answer is 4.",
                  "answer_incorrect": "The answer is 5."}
                 for i in range(2)]
        doc = {"envelope": build_envelope("items"), "payload": {"items": items}}
        p = self.tmp / "items.json"
        p.write_text(json.dumps(doc), encoding="utf-8")
        return p

    def test_position_run_with_cap(self):
        self.server.next_behavior(*(["ok"] * 50))
        items_path = self._write_items()
        log = self.tmp / "calls.jsonl"
        eps = self.tmp / "eps.json"
        eps.write_text(json.dumps({"endpoints": [
            {"name": "mock-a", "base_url": self.server.url(), "model": "m-a"},
            {"name": "mock-b", "base_url": self.server.url(), "model": "m-b"},
        ]}), encoding="utf-8")
        cfg = {
            "endpoints_file": str(eps), "log_path": str(log),
            "items_file": str(items_path), "bias_classes": ["position"],
            "seed": 20260914, "temperature": 0.0, "max_tokens": 64,
            "max_calls_per_judge": 3,
            "judges": ["mock-a", "mock-b"],
            "summary_out": str(self.tmp / "summary.json"),
        }
        cfg_path = self.tmp / "cfg.json"
        cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
        rc = rb.main(["--config", str(cfg_path)])
        self.assertEqual(rc, 0)

        lines = load_log(log)
        # 2 items x 2 orders = 4 specs per judge; cap 3 -> 3 calls per judge
        self.assertEqual(len(lines), 6)
        per_judge = {}
        for r in lines:
            per_judge.setdefault(r["endpoint"], set()).add(r["call_id"])
        self.assertEqual(len(per_judge["mock-a"]), 3)
        self.assertEqual(len(per_judge["mock-b"]), 3)
        for r in lines:
            self.assertEqual(r["parsed"]["verdict"]["preference"], "A")
            self.assertEqual(r["metadata"]["bias_class"], "position")
            self.assertIn(r["metadata"]["order"], {"AB", "BA"})
            self.assertEqual(r["request"]["temperature"], 0.0)
            self.assertIsNotNone(r["request"]["seed"])
        summary = json.loads((self.tmp / "summary.json").read_text(encoding="utf-8"))
        env = summary["envelope"]
        for key in ("artifact_type", "created_utc", "agent", "mission_brief",
                    "inputs", "environment", "method", "data", "provenance"):
            self.assertIn(key, env)
        self.assertEqual(env["data"]["summaries"]["position"]["calls_made"]["mock-a"], 3)
        self.assertEqual(env["data"]["summaries"]["position"]["n_skipped_by_cap"], 2)

    def test_expand_classes(self):
        items = [{"item_id": "a", "question": "q",
                  "answer_correct": "c", "answer_incorrect": "w"}]
        self.assertEqual(len(rb.expand_class("position", items, 1)), 2)
        self.assertEqual(len(rb.expand_class("verbosity", items, 1)), 2)
        self.assertEqual(len(rb.expand_class("cbw", items, 1)), 2)
        anch = rb.expand_class("anchoring", items, 1)
        self.assertEqual(sorted(s["condition"] for s in anch),
                         ["anchored", "unanchored"])
        self.assertTrue(all(s["schema"] == "scored" for s in anch))
        gen = rb.expand_class("selfpref_generation", items, 1)
        self.assertEqual(gen[0]["schema"], "generation")

    def test_day_cap_cumulative_across_classes(self):
        # REVIEWER regression (2026-09-14): the day_calls_used accumulator
        # must let two classes jointly reach (but never exceed) the day cap.
        # 1 precheck + position 3 items x 2 orders (6) + cbw 2 items x 2
        # orders (4 specs, only 3 fit) == exactly 10 calls for the day.
        self.server.next_behavior(*(["ok"] * 20))
        pos = [{"item_id": f"pos-{i}", "question": f"Q{i}?",
                "answer_correct": "RIGHT", "answer_incorrect": "WRONG"}
               for i in range(3)]
        cbw = [{"item_id": f"cbw-{i}", "question": f"Q{i}?",
                "answer_correct": "RIGHT", "answer_incorrect": "WRONG"}
               for i in range(2)]
        log = self.tmp / "calls.jsonl"
        ep = EndpointConfig(name="mock", base_url=self.server.url(),
                            model="m", timeout_s=10)
        day_used = {"mock": 1}
        s1 = rb.run_class("position", pos, {"mock": JudgeClient(ep, log,
                          parser=parse_pairwise)}, log, seed=1,
                          temperature=0.0, max_tokens=32, max_calls_per_judge=10,
                          judges=["mock"], run_tag="t", day_calls_used=day_used)
        s2 = rb.run_class("cbw", cbw, {"mock": JudgeClient(ep, log,
                          parser=parse_pairwise)}, log, seed=1,
                          temperature=0.0, max_tokens=32, max_calls_per_judge=10,
                          judges=["mock"], run_tag="t", day_calls_used=day_used)
        self.assertEqual(s1["calls_made"]["mock"], 6)
        self.assertEqual(s2["calls_made"]["mock"], 3)
        self.assertEqual(s2["n_skipped_by_cap"], 1)
        self.assertEqual(day_used["mock"], 10)

    def test_resume_dedup_tops_up_capped_run(self):
        # E-P8-1 (2026-09-15): a capped run logs only part of the job list;
        # re-running with the same seed/run_tag and resume enabled must
        # dispatch ONLY the not-yet-logged jobs (zero duplicates) so
        # coverage completes. Audited coverage per judge:
        #   n_jobs_by_judge == calls_made + n_already_logged
        #                      + n_skipped_by_cap
        self.server.next_behavior(*(["ok"] * 50))
        items = [{"item_id": f"pos-{i}", "question": f"Q{i}?",
                  "answer_correct": "RIGHT", "answer_incorrect": "WRONG"}
                 for i in range(4)]  # position: 4 items x 2 orders = 8 specs
        log = self.tmp / "calls.jsonl"
        ep = EndpointConfig(name="mock", base_url=self.server.url(),
                            model="m", timeout_s=10)

        def _run(logged):
            return rb.run_class(
                "position", items,
                {"mock": JudgeClient(ep, log, parser=parse_pairwise)},
                log, seed=20260914, temperature=0.0, max_tokens=32,
                max_calls_per_judge=4, judges=["mock"], run_tag="t",
                logged_job_keys=logged)

        # run 1: cap stops it at half coverage
        s1 = _run(None)
        self.assertEqual(s1["n_jobs_total"], 8)
        self.assertEqual(s1["calls_made"]["mock"], 4)
        self.assertEqual(s1["n_skipped_by_cap"], 4)
        self.assertEqual(s1["n_skipped_already_logged"], 0)
        keys1 = {r["metadata"]["item_seed_source"] for r in load_log(log)}
        self.assertEqual(len(keys1), 4)

        # run 2 (top-up): only the remaining jobs dispatch, cap not consumed
        # by the already-logged ones
        logged = rb.load_logged_job_keys(log)
        self.assertEqual(logged, keys1)
        s2 = _run(logged)
        self.assertEqual(s2["n_jobs_total"], 8)
        self.assertEqual(s2["calls_made"]["mock"], 4)
        self.assertEqual(s2["n_skipped_by_cap"], 0)
        self.assertEqual(s2["n_already_logged"], 4)
        self.assertEqual(s2["n_skipped_already_logged"], 4)
        self.assertEqual(s2["n_jobs_by_judge"]["mock"],
                         s2["calls_made"]["mock"] + s2["n_already_logged"]
                         + s2["n_skipped_by_cap"])
        self.assertEqual(s2["n_errors"], 0)

        all_recs = load_log(log)
        keys2 = {r["metadata"]["item_seed_source"]
                 for r in all_recs} - keys1
        self.assertEqual(len(keys2), 4)
        self.assertFalse(keys1 & keys2)          # zero overlap: no duplicates
        all_keys = {r["metadata"]["item_seed_source"] for r in all_recs}
        # full coverage: every (item, order, judge) job logged exactly once
        self.assertEqual(len(all_keys),
                         len(rb.expand_class("position", items, 20260914)))
        self.assertEqual(len(all_recs), len(all_keys))

    def test_resume_with_log_absent_unchanged_behavior(self):
        # E-P8-1: when the log file does not exist the loaded key set is
        # empty and behavior is byte-identical to dedup disabled.
        self.server.next_behavior(*(["ok"] * 50))
        items = [{"item_id": f"pos-{i}", "question": f"Q{i}?",
                  "answer_correct": "RIGHT", "answer_incorrect": "WRONG"}
                 for i in range(2)]
        missing = self.tmp / "does-not-exist.jsonl"
        self.assertEqual(rb.load_logged_job_keys(missing), set())

        ep = EndpointConfig(name="mock", base_url=self.server.url(),
                            model="m", timeout_s=10)

        def _run(log, logged):
            return rb.run_class(
                "position", items,
                {"mock": JudgeClient(ep, log, parser=parse_pairwise)},
                log, seed=7, temperature=0.0, max_tokens=32,
                judges=["mock"], run_tag="t", logged_job_keys=logged)

        log_a = self.tmp / "a.jsonl"
        log_b = self.tmp / "b.jsonl"
        s_a = _run(log_a, rb.load_logged_job_keys(missing))  # as main() passes
        s_b = _run(log_b, None)                              # dedup disabled
        self.assertEqual(s_a, s_b)
        self.assertEqual(s_a["calls_made"]["mock"], 4)  # 2 items x 2 orders
        self.assertEqual(s_a["n_skipped_already_logged"], 0)
        self.assertEqual(len(load_log(log_a)), 4)

    def test_expand_selfpref_grading(self):
        # Day-4 selfpref grading phase: one scored spec per item, grader
        # routing field preserved, condition "grading" (never anchored).
        items = [{"item_id": "spgrade-x-glm-own", "question": "Q?",
                  "answer": "ANSWER-TEXT", "grader_judge": "glm",
                  "competitor": "qwen", "graded_role": "own"}]
        specs = rb.expand_class("selfpref_grading", items, 1)
        self.assertEqual(len(specs), 1)
        s = specs[0]
        self.assertEqual(s["schema"], "scored")
        self.assertEqual(s["condition"], "grading")
        self.assertEqual(s["grader_judge"], "glm")
        self.assertEqual(s["answer"], "ANSWER-TEXT")

    def test_selfpref_grading_routes_to_grader_only_and_blind(self):
        # 3 grader-judges x 2 judge-specific items each: every item must be
        # dispatched to EXACTLY its grader (6 calls total, not 18), the
        # prompt must carry question+answer but never the role/competitor
        # metadata (blinding), and the coverage identity must hold.
        self.server.next_behavior(*(["score"] * 20))
        items = []
        for j, comp in (("jmax", "kmin"), ("kmin", "jmax"), ("midx", "jmax")):
            for role in ("own", "competitor"):
                items.append({
                    "item_id": f"sp-{j}-{role}", "question": "Question set alpha?",
                    "answer": "Answer body alpha.",
                    "grader_judge": j, "competitor": comp, "graded_role": role})
        log = self.tmp / "calls.jsonl"
        eps = {name: EndpointConfig(name=name, base_url=self.server.url(),
                                    model="m", timeout_s=10)
               for name in ("jmax", "kmin", "midx")}
        clients = {n: JudgeClient(ep, log, parser=parse_scored)
                   for n, ep in eps.items()}
        s = rb.run_class("selfpref_grading", items, clients, log, seed=1,
                         temperature=0.0, max_tokens=32, judges=["jmax", "kmin", "midx"],
                         run_tag="t")
        self.assertEqual(s["n_jobs_total"], 6)  # 6 items x 1 grader, NOT 18
        self.assertEqual(s["calls_made"], {"jmax": 2, "kmin": 2, "midx": 2})
        for j in s["calls_made"]:
            self.assertEqual(s["n_jobs_by_judge"][j],
                             s["calls_made"][j] + s["n_already_logged_by_judge"][j]
                             + s["n_skipped_by_cap_by_judge"][j])
        recs = load_log(log)
        self.assertEqual(len(recs), 6)
        for r in recs:
            md = r["metadata"]
            self.assertEqual(md["bias_class"], "selfpref_grading")
            self.assertEqual(md["condition"], "grading")
            # routing: the item targets exactly the judge that served it
            self.assertTrue(md["item_id"].startswith(f"sp-{md['judge']}-"))
            # scored parse
            self.assertTrue(r["parsed"]["ok"])
            self.assertIn("score", r["parsed"]["verdict"])
            # blinding: prompt content free of role/competitor metadata
            user_msg = r["request"]["messages"][-1]["content"]
            self.assertNotIn("graded_role", user_msg)
            self.assertNotIn("competitor", user_msg)
        # dedup keys unique per (grader, item)
        keys = {r["metadata"]["item_seed_source"] for r in recs}
        self.assertEqual(len(keys), 6)

    def test_selfpref_grading_resume_dedup(self):
        # Re-running the same config must skip all already-logged jobs
        # (no duplicate calls, coverage identity intact).
        self.server.next_behavior(*(["score"] * 20))
        items = [{"item_id": "sp-g-own", "question": "Q?", "answer": "A",
                  "grader_judge": "g", "competitor": "h", "graded_role": "own"}]
        log = self.tmp / "calls.jsonl"
        eps = {"g": EndpointConfig(name="g", base_url=self.server.url(),
                                   model="m", timeout_s=10),
               "h": EndpointConfig(name="h", base_url=self.server.url(),
                                   model="m", timeout_s=10)}
        clients = {n: JudgeClient(ep, log, parser=parse_scored) for n, ep in eps.items()}
        s1 = rb.run_class("selfpref_grading", items, clients, log, seed=1,
                          temperature=0.0, max_tokens=32, judges=["g", "h"], run_tag="t")
        self.assertEqual(s1["calls_made"], {"g": 1, "h": 0})
        s2 = rb.run_class("selfpref_grading", items, clients, log, seed=1,
                          temperature=0.0, max_tokens=32, judges=["g", "h"], run_tag="t",
                          logged_job_keys=rb.load_logged_job_keys(log))
        self.assertEqual(s2["calls_made"], {"g": 0, "h": 0})
        self.assertEqual(s2["n_already_logged"], 1)
        self.assertEqual(s2["n_jobs_total"], 1)  # routed to g only, not 2
        self.assertEqual(len(load_log(log)), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
