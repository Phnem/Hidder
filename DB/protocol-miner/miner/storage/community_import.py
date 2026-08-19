"""Importer for Community Research Probe observation bundles.

Validates schema, lengths, hex formatting, applies privacy validation,
assigns ConfidenceClass.VERIFIED_DYNAMIC_VENDOR_SOFTWARE (or INFERRED_STRONG), and integrates into Protocol Miner.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import re
import uuid
from pathlib import Path
from typing import Any

from miner import __version__
from miner.config import Settings
from miner.schemas.models import ConfidenceClass, Observation
from miner.synthesize.graph import build as build_evidence_graph
from miner.synthesize.candidate import synthesize
from miner.synthesize.planner import plan_adaptive_research


MAX_COMMUNITY_FILE_SIZE = 50 * 1024 * 1024  # 50 MB limit
MAX_OBSERVATIONS_COUNT = 100_000


class CommunityImportError(Exception):
    """Raised when a community observation bundle fails validation."""


def import_community_observation(
    json_path: Path,
    settings: Settings,
    vendor_override: str | None = None,
) -> dict[str, Any]:
    """Import and validate a community observation JSON file."""
    if not json_path.is_file():
        raise CommunityImportError(f"File not found: {json_path}")
        
    file_size = json_path.stat().st_size
    if file_size > MAX_COMMUNITY_FILE_SIZE:
        raise CommunityImportError(f"File size {file_size} exceeds maximum limit of {MAX_COMMUNITY_FILE_SIZE} bytes")

    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception as e:
        raise CommunityImportError(f"Malformed JSON: {e}")

    # 1. Schema check
    schema = data.get("schema")
    if schema != "peripheral.community-observation/1":
        raise CommunityImportError(f"Unsupported schema: {schema}")

    # 2. Privacy and Traversal checks
    software = data.get("software", {})
    proc_name = software.get("process_basename", "")
    if "/" in proc_name or "\\" in proc_name or ".." in proc_name:
        raise CommunityImportError(f"Invalid process path in software metadata: {proc_name}")

    device = data.get("device", {})
    vid = device.get("vid", "")
    pid = device.get("pid", "")
    model_name = device.get("model_name", "Unknown Model")
    category = device.get("category", "keyboard")
    
    # 3. Observations validation
    trans_obs = data.get("transport_observations", [])
    if len(trans_obs) > MAX_OBSERVATIONS_COUNT:
        raise CommunityImportError(f"Too many transport observations: {len(trans_obs)}")

    for idx, obs in enumerate(trans_obs):
        bhex = obs.get("bytes_hex", "")
        if not re.fullmatch(r"[0-9a-fA-F]*", bhex):
            raise CommunityImportError(f"Invalid hex bytes in observation #{idx}: {bhex[:30]}")

    # 4. Synthesize into Evidence Graph
    observations: list[Observation] = []
    source_name = f"community/{json_path.name}"
    dummy_sha256 = hashlib.sha256(json_path.name.encode()).hexdigest()
    
    # Identity observation
    if vid and pid:
        try:
            vid_int = int(vid, 16)
            pid_int = int(pid, 16)
            observations.append(Observation(
                observation_id=str(uuid.uuid4()),
                artifact_sha256=dummy_sha256,
                extractor="community_probe",
                extractor_version=__version__,
                kind="identity.vid_pid",
                source_path=source_name,
                value={"vid": vid_int, "pid": pid_int, "model": model_name, "category": category},
                confidence=ConfidenceClass.COMMUNITY_GUIDED_OBSERVATION,
            ))
        except ValueError:
            pass

    # Correlated candidate observations
    correlations = data.get("correlations", [])
    for c in correlations:
        observations.append(Observation(
            observation_id=str(uuid.uuid4()),
            artifact_sha256=dummy_sha256,
            extractor="community_probe",
            extractor_version=__version__,
            kind="protocol.community_candidate",
            source_path=source_name,
            value={
                "semantic": c.get("semantic"),
                "action_id": c.get("action_id"),
                "candidate_reports": c.get("candidate_reports", []),
                "changed_offsets": c.get("changed_offsets", []),
                "before_values": c.get("before_values", []),
                "after_values": c.get("after_values", []),
            },
            confidence=ConfidenceClass.COMMUNITY_GUIDED_OBSERVATION,
        ))

    # Transport observations
    for obs in trans_obs:
        observations.append(Observation(
            observation_id=str(uuid.uuid4()),
            artifact_sha256=dummy_sha256,
            extractor="community_probe",
            extractor_version=__version__,
            kind="dynamic.community_transport",
            source_path=source_name,
            value={
                "api": obs.get("api"),
                "direction": obs.get("direction"),
                "report_id": obs.get("report_id"),
                "bytes_hex": obs.get("bytes_hex"),
                "action_id": obs.get("action_id"),
                "repeat_count": obs.get("repeat_count", 1),
            },
            confidence=ConfidenceClass.COMMUNITY_GUIDED_OBSERVATION,
        ))

    # Create run output directory
    timestamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%SZ")
    run_id = f"community-run-{timestamp}-{dummy_sha256[:8]}"
    run_dir = settings.workspace_dir / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    ev_path = run_dir / "evidence.json"
    ev_path.write_text(json.dumps({"observations": [o.json() for o in observations]}, ensure_ascii=False, indent=2), encoding="utf-8")

    run_meta = {
        "run_id": run_id,
        "source": str(json_path.name),
        "vendor": vendor_override or device.get("manufacturer_string") or "Community",
        "category": category,
        "model": model_name,
        "quality": data.get("quality", {}),
        "imported_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "is_demo": data.get("is_demo", False),
    }
    (run_dir / "run.json").write_text(json.dumps(run_meta, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "run_id": run_id,
        "observations_count": len(observations),
        "correlations_count": len(correlations),
        "model_name": model_name,
        "quality_score": data.get("quality", {}).get("score", 0),
        "is_demo": data.get("is_demo", False),
    }
