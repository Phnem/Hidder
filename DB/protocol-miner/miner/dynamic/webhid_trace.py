"""Validate and normalize immutable fake-WebHID JSONL traces."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from miner import __version__
from miner.schemas.models import ConfidenceClass, Observation

_METHODS = {"sendReport", "sendFeatureReport", "receiveFeatureReport", "open", "close"}


def load(path: Path, artifact_sha256: str) -> list[Observation]:
    observations: list[Observation] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        item = json.loads(line)
        method = item.get("method")
        if method not in _METHODS:
            raise ValueError(f"unsupported fake-WebHID trace method at line {line_number}: {method}")
        value = {"method": method, "report_id": item.get("report_id"), "bytes_hex": item.get("bytes_hex"), "ui_action": item.get("ui_action")}
        if value["bytes_hex"] is not None:
            try:
                payload = bytes.fromhex(value["bytes_hex"])
            except (TypeError, ValueError) as error:
                raise ValueError(f"invalid bytes_hex at line {line_number}") from error
            value["length"] = len(payload)
        canonical = json.dumps(value, sort_keys=True, separators=(",", ":"))
        identifier = f"obs-{hashlib.sha256(f'{artifact_sha256}|{path.name}|{line_number}|{canonical}'.encode()).hexdigest()[:20]}"
        observations.append(Observation(identifier, artifact_sha256, "dynamic.fake_webhid_trace", __version__, "dynamic.webhid_call", value, f"trace/{path.name}:line={line_number}", ConfidenceClass.VERIFIED_DYNAMIC_VENDOR_SOFTWARE))
    return observations
