"""Section-3 JSON envelope builder for P8 experiment artifacts.

Every experiment artifact (battery spec, item files, run summaries, smoke
logs) must carry this envelope per BRIEF-20260914-sprint-p8-judge-audit and
the standing governance rules:

    artifact_type, created_utc, agent, mission_brief,
    inputs (with sha256s), environment,
    method (seeds, temps, prompts, randomization -- replayable),
    data, provenance (measured_vs_estimated).

stdlib only.
"""
from __future__ import annotations

import hashlib
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HARNESS_VERSION = "1.0.0"
AGENT = "WORKER-2 (GLM)"
MISSION_BRIEF = "glm/briefs/BRIEF-20260914-sprint-p8-judge-audit.md"


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def environment() -> dict[str, Any]:
    return {
        "python": sys.version.replace("\n", " "),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "harness_version": HARNESS_VERSION,
        "stdlib_only": True,
    }


def build_envelope(
    artifact_type: str,
    inputs: dict[str, str] | list[dict[str, Any]] | None = None,
    method: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
    provenance: dict[str, Any] | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Build a complete section-3 envelope.

    inputs: mapping of {path_or_name: sha256} (or list of {name, sha256}).
    method: seeds, temperatures, prompt ids/templates, randomization scheme --
            must be sufficient to replay the artifact.
    provenance: {"measured": [...], "estimated": [...]} split of claims.
    """
    if inputs is None:
        inputs = {}
    if isinstance(inputs, dict):
        inputs = [{"name": k, "sha256": v} for k, v in inputs.items()]
    env: dict[str, Any] = {
        "artifact_type": artifact_type,
        "created_utc": utc_now_iso(),
        "agent": AGENT,
        "mission_brief": MISSION_BRIEF,
        "inputs": inputs,
        "environment": environment(),
        "method": method or {},
        "data": data or {},
        "provenance": provenance or {"measured": [], "estimated": []},
    }
    if notes:
        env["notes"] = notes
    return env


def write_json_with_envelope(
    path: str | Path,
    artifact_type: str,
    payload: dict[str, Any],
    envelope: dict[str, Any],
) -> None:
    """Write {"envelope": ..., "payload": ...} as pretty JSON."""
    obj = {"envelope": envelope, "payload": payload}
    Path(path).write_text(
        json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
