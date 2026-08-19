"""Static run orchestration, initial evidence graph, and review-only output generation."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

from miner import __version__
from miner.config import Settings
from miner.orchestrator.ingest import _now, _write_json
from miner.schemas.models import ProtocolCandidate
from miner.static.extract import scan_file


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _status(identity: list[dict], topology: list[dict]) -> str:
    if topology:
        return "TOPOLOGY_CANDIDATE"
    if identity:
        return "IDENTITY_ONLY"
    return "NO_TECHNICAL_EVIDENCE"


def analyze_artifact(settings: Settings, artifact_ref: str) -> dict[str, str]:
    settings.ensure_directories()
    sha256 = artifact_ref.removeprefix("sha256:").lower()
    if len(sha256) != 64 or any(char not in "0123456789abcdef" for char in sha256):
        raise ValueError("analyze expects a SHA-256 or sha256:<digest>")
    provenance_path = settings.workspace_dir / "artifacts" / sha256 / "provenance.json"
    if not provenance_path.exists():
        raise ValueError(f"No Protocol Miner provenance for {sha256}")
    cas_path = settings.cas_root / sha256[:2] / sha256
    if not cas_path.is_file():
        raise ValueError(f"CAS blob is missing for {sha256}")
    provenance = _load_json(provenance_path)
    files: list[tuple[str, Path]] = [("cas/" + provenance["original_filename"], cas_path)]
    artifact_tree = _load_json(settings.workspace_dir / "artifacts" / sha256 / "artifact_tree.json")
    unpacked = settings.workspace_dir / "unpacked" / sha256
    if unpacked.is_dir():
        files.extend((f"unpacked/{path.relative_to(unpacked).as_posix()}", path) for path in sorted(unpacked.rglob("*")) if path.is_file())
    for child in artifact_tree.get("children", []):
        child_sha256 = child.get("sha256")
        if child.get("status") not in {"success", "already_unpacked"} or not isinstance(child_sha256, str):
            continue
        child_dir = settings.workspace_dir / "unpacked" / child_sha256
        if child_dir.is_dir():
            files.extend((f"nested/{child_sha256}/{path.relative_to(child_dir).as_posix()}", path) for path in sorted(child_dir.rglob("*")) if path.is_file())
    observations = []
    for source_path, path in files:
        observations.extend(scan_file(sha256, source_path, path))
    observations = sorted({item.observation_id: item for item in observations}.values(), key=lambda item: item.observation_id)
    identity = [{**item.value, "evidence_refs": [item.observation_id], "confidence": item.confidence.value} for item in observations if item.kind == "identity.vid_pid"]
    topology = [{**item.value, "evidence_refs": [item.observation_id], "confidence": item.confidence.value} for item in observations if item.kind.startswith("topology.")]
    packets = [item for item in observations if item.kind == "protocol.direct_packet_literal"]
    dangerous = [{**item.value, "evidence_refs": [item.observation_id], "dangerous_candidate": True} for item in observations if item.kind == "protocol.dangerous_hint"]
    commands = {f"packet_{index + 1}": {**item.value, "evidence_refs": [item.observation_id], "safe_for_production": False} for index, item in enumerate(packets)}
    ecosystem = next((item for item in observations if item.kind == "ecosystem.via_qmk"), None)
    candidate = ProtocolCandidate(
        family_candidate="via-qmk" if ecosystem else None,
        identity=identity, topology=topology, commands=commands, dangerous_commands=dangerous,
        evidence_refs=[item.observation_id for item in observations],
    )
    candidate.unknowns = [
        "Command framing, value encoding, persistence semantics, cadence, and safe write behavior are unknown.",
        "No observation is hardware-verified; Protocol Miner does not access real HID devices.",
    ]
    run_id = f"run-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
    run_dir, report_dir = settings.workspace_dir / "runs" / run_id, settings.reports_dir / run_id
    _write_json(run_dir / "run.json", {"schema": "peripheral.run/1", "run_id": run_id, "status": _status(identity, topology), "started_at": _now(), "tool_version": __version__, "input_sha256": sha256, "mode": "static"})
    _write_json(run_dir / "evidence.json", {"schema": "peripheral.evidence/1", "observations": [item.json() for item in observations]})
    _write_json(run_dir / "protocol_candidate.json", candidate.json())
    _write_json(report_dir / "artifact_tree.json", artifact_tree)
    _write_json(report_dir / "identity.json", {"schema": "peripheral.identity/1", "identity": identity})
    _write_json(report_dir / "topology.json", {"schema": "peripheral.topology/1", "topology": topology})
    _write_json(report_dir / "capabilities.json", {"schema": "peripheral.capabilities/1", "capabilities": {}})
    _write_json(report_dir / "commands.json", {"schema": "peripheral.commands/1", "commands": commands})
    _write_json(report_dir / "protocol_candidate.json", candidate.json())
    _write_json(report_dir / "evidence.json", {"schema": "peripheral.evidence/1", "observations": [item.json() for item in observations]})
    _write_json(report_dir / "contradictions.json", {"schema": "peripheral.contradictions/1", "contradictions": []})
    _write_json(report_dir / "dangerous_commands.json", {"schema": "peripheral.dangerous-commands/1", "commands": dangerous})
    _write_json(report_dir / "registry_patch.json", {"schema": "peripheral.registry-staging-patch/1", "artifact_sha256": sha256, "identity_candidates": identity, "status": "review_required"})
    _write_json(report_dir / "run.json", _load_json(run_dir / "run.json"))
    (report_dir / "unknowns.md").write_text("# Unknowns\n\n" + "\n".join(f"- {item}" for item in candidate.unknowns) + "\n", encoding="utf-8")
    (report_dir / "summary.md").write_text(
        f"# Protocol Miner summary\n\nStatus: `{_status(identity, topology)}`\n\n- Artifact: `{sha256}`\n- Identity candidates: {len(identity)}\n- Topology observations: {len(topology)}\n- Literal packet candidates: {len(commands)}\n- Dangerous command hints: {len(dangerous)}\n\nNo real HID device was accessed.\n", encoding="utf-8",
    )
    _write_json(settings.candidates_dir / f"{run_id}.json", candidate.json())
    return {"run_id": run_id, "sha256": sha256, "report": str(report_dir / "summary.md")}
