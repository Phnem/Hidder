"""Versioned .pevidence bundle packager, importer, and integrity verifier."""

from __future__ import annotations

import hashlib
import json
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from miner import __version__
from miner.dynamic.input_learning import PrivacyScrubber
from miner.schemas.models import ConfidenceClass, Observation
from miner.unpack.safe_paths import safe_relative


class PevidenceIntegrityError(ValueError):
    """Raised when .pevidence file hash does not match integrity manifest."""


class PevidenceSecurityError(ValueError):
    """Raised on path traversal or malformed bundle structure."""


def _compute_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def export_pevidence_bundle(
    output_path: Path,
    device_info: dict[str, Any],
    software_info: dict[str, Any],
    traces: list[dict[str, Any]],
    actions: list[dict[str, Any]] | None = None,
    derived_commands: dict[str, Any] | None = None,
    descriptors: bytes | None = None,
    research_mode: str = "fake_webhid",
    restore_status: str = "RESTORE_CONFIRMED",
    submission_id: str | None = None,
) -> Path:
    """Create a tamper-evident, versioned .pevidence ZIP bundle."""
    sub_id = submission_id or f"sub-{uuid.uuid4().hex[:12]}"
    created_at = datetime.now(UTC).isoformat()
    software_sha = software_info.get("sha256") or hashlib.sha256(json.dumps(software_info).encode()).hexdigest()

    manifest = {
        "schema_version": "peripheral.pevidence/1",
        "submission_id": sub_id,
        "peripheral_miner_version": __version__,
        "created_at": created_at,
        "device_observation_id": f"dev-{hashlib.sha256(json.dumps(device_info, sort_keys=True).encode()).hexdigest()[:16]}",
        "research_mode": research_mode,
        "research_status": "COMPLETE" if restore_status == "RESTORE_CONFIRMED" else "PARTIAL",
        "restore_status": restore_status,
        "software_artifact_sha256": software_sha,
    }

    # Privacy scrub on device & software info
    device_clean = PrivacyScrubber.scrub_structure(device_info)
    software_clean = PrivacyScrubber.scrub_structure(software_info)
    actions_clean = PrivacyScrubber.scrub_structure(actions or [])

    bundle_files: dict[str, bytes] = {
        "manifest.json": json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8"),
        "device.json": json.dumps(device_clean, indent=2, ensure_ascii=False).encode("utf-8"),
        "software.json": json.dumps(software_clean, indent=2, ensure_ascii=False).encode("utf-8"),
        "actions.json": json.dumps(actions_clean, indent=2, ensure_ascii=False).encode("utf-8"),
        "traces/trace_01.jsonl": "\n".join(json.dumps(t, ensure_ascii=False) for t in traces).encode("utf-8") + b"\n",
        "derived/commands.json": json.dumps(derived_commands or {}, indent=2, ensure_ascii=False).encode("utf-8"),
        "restore.json": json.dumps({"restore_status": restore_status, "verified_at": created_at}, indent=2).encode("utf-8"),
    }

    if descriptors:
        bundle_files["descriptors/hid.bin"] = descriptors

    # Compute integrity map
    integrity_map = {name: _compute_sha256(content) for name, content in bundle_files.items()}
    bundle_files["integrity.json"] = json.dumps(integrity_map, indent=2, sort_keys=True).encode("utf-8")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for arcname, content in bundle_files.items():
            zf.writestr(arcname, content)

    return output_path


def validate_pevidence_bundle(bundle_path: Path) -> dict[str, Any]:
    """Inspect .pevidence archive without writing to disk, verifying structure and hashes."""
    if not bundle_path.is_file():
        raise FileNotFoundError(f"File not found: {bundle_path}")

    errors: list[str] = []
    manifest: dict[str, Any] = {}

    with zipfile.ZipFile(bundle_path, "r") as zf:
        namelist = zf.namelist()

        # Check path traversal in archive
        for name in namelist:
            if safe_relative(name) is None:
                raise PevidenceSecurityError(f"Path traversal detected in bundle member '{name}'")

        if "manifest.json" not in namelist:
            errors.append("Missing manifest.json")
        else:
            try:
                manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
                if manifest.get("schema_version") != "peripheral.pevidence/1":
                    errors.append(f"Unsupported schema_version: {manifest.get('schema_version')}")
            except Exception as exc:
                errors.append(f"Invalid manifest.json: {exc}")

        if "integrity.json" not in namelist:
            errors.append("Missing integrity.json")
        else:
            try:
                integrity_map = json.loads(zf.read("integrity.json").decode("utf-8"))
                for file_name, expected_hash in integrity_map.items():
                    if file_name not in namelist:
                        errors.append(f"File '{file_name}' declared in integrity.json is missing in archive")
                        continue
                    actual_hash = _compute_sha256(zf.read(file_name))
                    if actual_hash != expected_hash:
                        errors.append(f"Integrity mismatch for '{file_name}': expected {expected_hash}, got {actual_hash}")
            except Exception as exc:
                errors.append(f"Invalid integrity.json: {exc}")

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "manifest": manifest,
        "file_count": len(namelist),
    }


def import_pevidence_bundle(bundle_path: Path, target_dir: Path) -> list[Observation]:
    """Validate, unpack, and convert .pevidence bundle into evidence graph Observations."""
    validation = validate_pevidence_bundle(bundle_path)
    if not validation["valid"]:
        raise PevidenceIntegrityError(f"Corrupted or invalid .pevidence bundle: {validation['errors']}")

    manifest = validation["manifest"]
    artifact_sha = manifest["software_artifact_sha256"]

    target_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(bundle_path, "r") as zf:
        for member in zf.infolist():
            safe_rel = safe_relative(member.filename)
            if safe_rel is None:
                raise PevidenceSecurityError(f"Path traversal detected in member '{member.filename}'")
            dest = target_dir / safe_rel
            if member.is_dir():
                dest.mkdir(parents=True, exist_ok=True)
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(zf.read(member.filename))

    observations: list[Observation] = []

    # Ingest device metadata
    device_json = target_dir / "device.json"
    if device_json.is_file():
        dev_data = json.loads(device_json.read_text(encoding="utf-8"))
        if dev_data.get("vendorId") and dev_data.get("productId"):
            vid_hex = hex(dev_data["vendorId"]) if isinstance(dev_data["vendorId"], int) else str(dev_data["vendorId"])
            pid_hex = hex(dev_data["productId"]) if isinstance(dev_data["productId"], int) else str(dev_data["productId"])
            obs_id = f"obs-pevid-id-{hashlib.sha256(f'{vid_hex}:{pid_hex}'.encode()).hexdigest()[:16]}"
            observations.append(
                Observation(
                    obs_id,
                    artifact_sha,
                    "storage.pevidence_importer",
                    __version__,
                    "identity.vid_pid",
                    {"vendor_id": vid_hex, "product_id": pid_hex, "name": dev_data.get("productName", "")},
                    "pevidence/device.json",
                    ConfidenceClass.VERIFIED_VENDOR_ARTIFACT,
                )
            )

    # Ingest traces
    trace_dir = target_dir / "traces"
    if trace_dir.is_dir():
        for tf in trace_dir.glob("*.jsonl"):
            for line_no, line in enumerate(tf.read_text(encoding="utf-8").splitlines(), 1):
                if not line.strip():
                    continue
                item = json.loads(line)
                obs_id = f"obs-pevid-tr-{hashlib.sha256(f'{artifact_sha}|{tf.name}|{line_no}|{line}'.encode()).hexdigest()[:16]}"
                observations.append(
                    Observation(
                        obs_id,
                        artifact_sha,
                        "storage.pevidence_importer",
                        __version__,
                        "dynamic.webhid_call",
                        item,
                        f"pevidence/traces/{tf.name}:line={line_no}",
                        ConfidenceClass.VERIFIED_DYNAMIC_VENDOR_SOFTWARE,
                    )
                )

    return observations
