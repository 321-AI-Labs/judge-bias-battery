"""OpenAI-compatible judge client with verbatim JSONL call logging.

P8 judge-calibration harness, deliverable 1. stdlib only (urllib.request).

Design contract (binding rules from BRIEF-20260914-sprint-p8-judge-audit):
- EVERY judge call is logged verbatim to JSONL: prompt (full messages),
  raw response text, parsed verdict (if a parser is supplied), finish
  reason, token usage, latency, seed, temperature, endpoint, model id,
  harness code sha. The log IS the dataset -- no summarization at capture.
- Retries: at most 1 retry per call on retryable failures (connection
  error, timeout, HTTP 5xx/429). EVERY attempt gets its own log line so
  retries are auditable; attempt 2 carries retry_of = attempt-1 call_id.
- Temperature/seed/max_tokens are per-call parameters, recorded per line.
"""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from envelope import sha256_text, utc_now_iso

LOG_SCHEMA_VERSION = 1

_RETRYABLE_HTTP = {429, 500, 502, 503, 504}


@dataclass
class EndpointConfig:
    name: str
    base_url: str
    model: str
    timeout_s: float = 240.0
    api_key: str | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "EndpointConfig":
        return cls(
            name=d["name"],
            base_url=d["base_url"].rstrip("/"),
            model=d["model"],
            timeout_s=float(d.get("timeout_s", 240.0)),
            api_key=d.get("api_key"),
        )


def load_endpoints(path: str | Path) -> dict[str, EndpointConfig]:
    """Load endpoint configs. Accepts two formats:

    1. Harness-native:
       {"endpoints": [{"name", "base_url", "model", "timeout_s"?}, ...]}
    2. Orchestrator envelope (judge-endpoints.json from the P8 orchestrator):
       artifact_type == "judge-endpoints", with inputs.models[].judge/.port
       and data.ports. Names become the judge keys; model ids are derived
       from the model file names (explicit table for the three P8 judges,
       quant-suffix regex fallback otherwise).
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(raw, dict) and "endpoints" in raw:
        endpoints = raw["endpoints"]
    elif isinstance(raw, dict) and raw.get("artifact_type") == "judge-endpoints":
        endpoints = _from_orchestrator_envelope(raw)
    else:
        endpoints = raw
    return {e["name"]: EndpointConfig.from_dict(e) for e in endpoints}


# model ids for the P8 judges; llama.cpp serves one model per instance and
# tolerates any "model" string, but the log must record the true model id
_KNOWN_MODEL_IDS = {
    "glm": "glm-4-9b-chat",
    "llama": "Meta-Llama-3.1-8B-Instruct",
    "qwen": "qwen3-8b",
}
_QUANT_SUFFIX_RE = re.compile(
    r"[-_ ]?(?:IQ[0-9]_[A-Z]+|UD-[A-Z]+|Q[0-9][A-Z0-9_]*?)$",
    re.IGNORECASE)


def _model_id_for(judge: str, models_entry: dict[str, Any]) -> str:
    if judge in _KNOWN_MODEL_IDS:
        return _KNOWN_MODEL_IDS[judge]
    src = str(models_entry.get("source_file")
              or models_entry.get("model_path") or judge)
    stem = src.rsplit("/", 1)[-1]
    if stem.lower().endswith(".gguf"):
        stem = stem[:-5]
    return _QUANT_SUFFIX_RE.sub("", stem).strip("-_ ") or judge


def _from_orchestrator_envelope(raw: dict[str, Any]) -> list[dict[str, Any]]:
    models = raw.get("inputs", {}).get("models", [])
    out = []
    for m in models:
        judge = m["judge"]
        port = m.get("port") or raw.get("data", {}).get("ports", {}).get(judge)
        if port is None:
            continue
        out.append({
            "name": judge,
            "base_url": f"http://127.0.0.1:{port}",
            "model": _model_id_for(judge, m),
            "timeout_s": float(m.get("timeout_s", 240.0)),
        })
    return out


def _harness_code_shas() -> dict[str, str]:
    """sha256 of the harness source files, for the audit trail."""
    here = Path(__file__).resolve().parent
    out = {}
    for name in ("judge_client.py", "parse.py", "randomize.py",
                 "metrics.py", "run_battery.py", "envelope.py"):
        p = here / name
        if p.exists():
            out[name] = sha256_text(p.read_text(encoding="utf-8"))
    return out


class JudgeClient:
    """Thin OpenAI-compatible chat-completions client + verbatim logger."""

    # one lock shared across instances: JSONL line appends must not
    # interleave when several judges are driven in parallel threads
    _LOG_LOCK = threading.Lock()

    def __init__(
        self,
        endpoint: EndpointConfig,
        log_path: str | Path,
        parser: Callable[[str], Any] | None = None,
        retry_backoff_s: float = 2.0,
    ) -> None:
        self.endpoint = endpoint
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.parser = parser
        self.retry_backoff_s = retry_backoff_s
        self._shas = _harness_code_shas()

    # ------------------------------------------------------------- health
    def health(self) -> dict[str, Any]:
        """GET {base}/v1/models. Returns {"healthy": bool, "status": ..., "detail": ...}."""
        url = f"{self.endpoint.base_url}/v1/models"
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                return {"healthy": resp.status == 200, "status": resp.status,
                        "detail": body[:500]}
        except Exception as exc:  # noqa: BLE001 -- health probe must not raise
            return {"healthy": False, "status": None, "detail": repr(exc)}

    # --------------------------------------------------------------- call
    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.0,
        seed: int | None = None,
        max_tokens: int = 512,
        extra_params: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """One logical judge call (<= 2 HTTP attempts), fully logged.

        Returns the final (last attempt's) log record.
        """
        payload: dict[str, Any] = {
            "model": self.endpoint.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if seed is not None:
            payload["seed"] = int(seed)
        if extra_params:
            payload.update(extra_params)

        retry_of: str | None = None
        record: dict[str, Any] | None = None
        for attempt in (1, 2):
            record = self._attempt(payload, attempt=attempt, retry_of=retry_of,
                                   metadata=metadata)
            self._append_log(record)
            if record["error"] is None or attempt == 2:
                return record
            if record["http_status"] is not None and \
                    record["http_status"] not in _RETRYABLE_HTTP:
                return record  # non-retryable HTTP error (4xx): do not retry
            retry_of = record["call_id"]
            time.sleep(self.retry_backoff_s)
        return record  # pragma: no cover -- loop always returns

    # ------------------------------------------------------------- internals
    def _attempt(
        self,
        payload: dict[str, Any],
        *,
        attempt: int,
        retry_of: str | None,
        metadata: dict[str, Any] | None,
    ) -> dict[str, Any]:
        call_id = uuid.uuid4().hex
        started = time.monotonic()
        record: dict[str, Any] = {
            "schema_version": LOG_SCHEMA_VERSION,
            "call_id": call_id,
            "attempt": attempt,
            "retry_of": retry_of,
            "timestamp_utc": utc_now_iso(),
            "endpoint": self.endpoint.name,
            "base_url": self.endpoint.base_url,
            "model_id": self.endpoint.model,
            "request": {
                "messages": payload["messages"],
                "temperature": payload.get("temperature"),
                "seed": payload.get("seed"),
                "max_tokens": payload.get("max_tokens"),
                "extra_params": {
                    k: v for k, v in payload.items()
                    if k not in {"model", "messages", "temperature",
                                 "max_tokens", "seed"}
                } or None,
            },
            "raw_response_text": "",
            "raw_response_json": None,
            "finish_reason": None,
            "token_usage": None,
            "latency_ms": None,
            "http_status": None,
            "error": None,
            "harness": {"version": "1.0.0", "code_sha256": self._shas},
            "metadata": metadata or {},
            "parsed": None,
        }
        body = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.endpoint.api_key:
            headers["Authorization"] = f"Bearer {self.endpoint.api_key}"
        req = urllib.request.Request(
            f"{self.endpoint.base_url}/v1/chat/completions",
            data=body, headers=headers, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.endpoint.timeout_s) as resp:
                record["http_status"] = resp.status
                raw_body = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            record["http_status"] = exc.code
            record["error"] = f"HTTPError {exc.code}: {exc.reason}"
            record["latency_ms"] = round((time.monotonic() - started) * 1000, 1)
            try:
                record["raw_response_json"] = json.loads(
                    exc.read().decode("utf-8", errors="replace"))
            except Exception:
                pass
            return record
        except Exception as exc:  # timeout, connection refused, etc.
            record["error"] = f"{type(exc).__name__}: {exc}"
            record["latency_ms"] = round((time.monotonic() - started) * 1000, 1)
            return record

        record["latency_ms"] = round((time.monotonic() - started) * 1000, 1)
        try:
            rj = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            record["error"] = f"non-JSON response body: {exc}"
            record["raw_response_text"] = raw_body
            return record
        record["raw_response_json"] = rj

        choices = rj.get("choices") or []
        if not choices:
            record["error"] = "response has no choices"
            return record
        msg = choices[0].get("message") or {}
        text = msg.get("content")
        if text is None:
            # some servers put content in 'reasoning_content' or return
            # empty content with a finish_reason; capture verbatim either way
            text = msg.get("reasoning_content") or ""
        record["raw_response_text"] = text  # verbatim
        record["finish_reason"] = choices[0].get("finish_reason")
        record["token_usage"] = rj.get("usage")
        record["error"] = None

        if self.parser is not None:
            try:
                record["parsed"] = self.parser(text)
            except Exception as exc:  # parser must never take down a run
                record["parsed"] = {"ok": False, "format_compliant": False,
                                    "verdict": None,
                                    "error": f"parser crashed: {exc!r}"}
        return record

    def _append_log(self, record: dict[str, Any]) -> None:
        line = json.dumps(record, ensure_ascii=False) + "\n"
        with self._LOG_LOCK:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(line)
                f.flush()
