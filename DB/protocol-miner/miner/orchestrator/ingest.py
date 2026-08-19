"""Safe local ingestion and reproducible foundation artifacts."""

from __future__ import annotations

import json
import platform
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

from miner import __version__
from miner.config import Settings
from miner.detect.file_type import detect
from miner.schemas.models import ArtifactRecord, ProtocolCandidate
from miner.storage.cas import ContentAddressedStore
from miner.unpack.safe import SafeUnpacker


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def ingest_file(settings: Settings, input_path: Path, vendor: str | None = None) -> dict[str, str]:
    settings.ensure_directories()
    source = input_path.resolve()
    if not source.is_file():
        raise ValueError(f"Input is not a readable file: {input_path}")
    if source.stat().st_size > settings.max_artifact_size:
        raise ValueError(f"Artifact exceeds configured size limit: {settings.max_artifact_size} bytes")

    sha256, cas_path, was_added = ContentAddressedStore(settings.cas_root).put_file(source)
    artifact_id = f"sha256:{sha256}"
    artifact = ArtifactRecord(
        artifact_id=artifact_id,
        original_filename=source.name,
        source_type="user_supplied_vendor_artifact",
        sha256=sha256,
        size=source.stat().st_size,
        detected_type=detect(source, source.name),
        retrieved_at=_now(),
    )
    artifact_dir = settings.workspace_dir / "artifacts" / sha256
    _write_json(artifact_dir / "provenance.json", artifact.json())
    unpacked = SafeUnpacker(settings).unpack(cas_path, sha256)
    _write_json(artifact_dir / "artifact_tree.json", {
        "schema": "peripheral.artifact-tree/1", "root": sha256, "children": [],
        "unpack": {"status": unpacked.status, "file_count": unpacked.file_count, "total_bytes": unpacked.total_bytes, "error": unpacked.error},
    })

    run_id = f"run-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
    candidate = ProtocolCandidate(unknowns=["Static analyzers have not run yet."])
    run = {
        "schema": "peripheral.run/1", "run_id": run_id, "status": "INGESTED",
        "started_at": _now(), "tool_version": __version__, "python": sys.version.split()[0],
        "platform": platform.platform(), "vendor": vendor, "input_sha256": sha256,
        "cas_path": str(cas_path), "cas_added": was_added, "unpack_status": unpacked.status,
        "config": {"max_artifact_size": settings.max_artifact_size, "max_expanded_size": settings.max_expanded_size},
    }
    run_dir = settings.workspace_dir / "runs" / run_id
    _write_json(run_dir / "run.json", run)
    _write_json(run_dir / "evidence.json", {"schema": "peripheral.evidence/1", "observations": []})
    _write_json(run_dir / "protocol_candidate.json", candidate.json())
    _write_json(settings.candidates_dir / f"{run_id}.json", candidate.json())
    _write_json(settings.reports_dir / run_id / "registry_patch.json", {
        "schema": "peripheral.registry-staging-patch/1", "artifact_sha256": sha256, "facts": [], "status": "review_required"
    })
    (settings.reports_dir / run_id / "summary.md").write_text(
        f"# Protocol Miner: {source.name}\n\nStatus: `IDENTITY_ONLY`\n\n- SHA256: `{sha256}`\n- Type: `{artifact.detected_type}`\n- Source: user supplied; official provenance is unverified\n- CAS: `{cas_path}`\n\nNo static analyzer has run in this foundation stage.\n",
        encoding="utf-8",
    )
    (settings.reports_dir / run_id / "unknowns.md").write_text(
        "# Unknowns\n\n- Identity, topology, command map, capabilities, and persistence semantics require static analysis.\n- No real HID interaction is performed by Protocol Miner.\n",
        encoding="utf-8",
    )
    return {"run_id": run_id, "sha256": sha256, "report": str(settings.reports_dir / run_id / "summary.md")}
