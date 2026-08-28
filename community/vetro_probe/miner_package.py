"""Miner-ready export package builder.

Probe exports OBSERVATIONS / EVIDENCE only — never new protocol truths.
Miner does the inference.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import zipfile
from pathlib import Path
from typing import Any

from .version import (
    DEFAULT_KNOWLEDGE_REVISION,
    PROBE_APP_VERSION,
    PROBE_ENGINE_VERSION,
    get_build_commit,
)

PACKAGE_SCHEMA_VERSION = "vetro.run-manifest.v1"


def sanitize_filename_part(text: str) -> str:
    """Sanitize string for safe use in Windows/Unix filenames."""
    cleaned = re.sub(r'[\\/*?:"<>|\s]+', "-", str(text).strip())
    cleaned = re.sub(r"-+", "-", cleaned).strip("-")
    return cleaned or "unknown"


def generate_package_zip_name(
    discovery: dict[str, Any],
    run_id: str,
    terminal: str = "",
) -> str:
    """Generate a unique human-readable package filename:
    VetroProbe_<device>_<firmware>_<run-id>.zip
    or
    VetroProbe_DIAGNOSTIC_<device>_<firmware>_<run-id>.zip
    """
    dev_name = (
        discovery.get("product_string")
        or (discovery.get("device") or {}).get("name")
        or ""
    )
    fw = (
        discovery.get("firmware")
        or (discovery.get("device") or {}).get("firmware")
        or ""
    )

    clean_dev = sanitize_filename_part(dev_name)
    clean_fw = sanitize_filename_part(fw)
    clean_run = sanitize_filename_part(run_id)

    prefix = "VetroProbe"
    if terminal and terminal not in ("COMPLETE_PASS", "SUCCESS_RESTORED", "PASS"):
        prefix = "VetroProbe_DIAGNOSTIC"

    if clean_dev and clean_dev != "unknown" and clean_fw and clean_fw != "unknown":
        return f"{prefix}_{clean_dev}_{clean_fw}_{clean_run}.zip"
    elif clean_dev and clean_dev != "unknown":
        return f"{prefix}_{clean_dev}_{clean_run}.zip"
    else:
        return f"{prefix}_{clean_run}.zip"


# Forbidden personal path patterns
FORBIDDEN_PATH_PATTERNS = [
    re.compile(r"[A-Za-z]:[/\\][Uu]sers[/\\][^\s\"\'<>\\]+", re.IGNORECASE),
    re.compile(r"_MEI\d+", re.IGNORECASE),
    re.compile(r"[A-Za-z]:[/\\][^\s\"\'<>]*AndroidStudioProjects", re.IGNORECASE),
    re.compile(r"[A-Za-z]:[/\\][^\s\"\'<>]*AppData[/\\]Local[/\\]Temp", re.IGNORECASE),
]


def privacy_scrub_string(text: str) -> str:
    """Scrub personal/absolute Windows & repo paths from strings."""
    if not isinstance(text, str):
        return text
    scrubbed = text
    for pattern in FORBIDDEN_PATH_PATTERNS:
        scrubbed = pattern.sub("[REDACTED_PATH]", scrubbed)
    return scrubbed


def privacy_scrub_data(obj: Any) -> Any:
    """Recursively scrub strings and dictionary keys/values."""
    if isinstance(obj, str):
        return privacy_scrub_string(obj)
    elif isinstance(obj, dict):
        return {k: privacy_scrub_data(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [privacy_scrub_data(elem) for elem in obj]
    return obj


def scan_package_for_privacy_violations(package_dir: Path) -> list[str]:
    """Scan all text and json files in the package directory.
    Returns list of violations if any forbidden personal path remains."""
    violations = []
    for file_path in package_dir.rglob("*"):
        if file_path.is_file() and file_path.suffix in (".json", ".txt", ".vetrojson"):
            try:
                content = file_path.read_text(encoding="utf-8", errors="ignore")
                for pattern in FORBIDDEN_PATH_PATTERNS:
                    matches = pattern.findall(content)
                    if matches:
                        for m in matches:
                            violations.append(f"{file_path.name}: {m}")
            except Exception:
                pass
    return violations


def build_package(
    *,
    base_dir: Path,
    run_id: str,
    label: str,
    discovery: dict[str, Any],
    plan: list[dict[str, Any]],
    evidence: list[Any],
    baselines: dict[str, Any],
    final_state: dict[str, Any],
    certificates: list[dict[str, Any]],
    recovery: dict[str, Any],
    terminal: str,
) -> Path:
    package = Path(base_dir)
    package.mkdir(parents=True, exist_ok=True)
    for sub in ("baselines", "captures", "evidence", "certificates", "recovery", "miner_input"):
        (package / sub).mkdir(exist_ok=True)

    # Privacy scrub discovery, recovery and final state
    discovery_clean = privacy_scrub_data(discovery)
    recovery_clean = privacy_scrub_data(recovery)
    final_state_clean = privacy_scrub_data(final_state)
    baselines_clean = privacy_scrub_data(baselines)
    plan_clean = privacy_scrub_data(plan)

    (package / "device_identity.json").write_text(json.dumps(discovery_clean, ensure_ascii=False, indent=2), encoding="utf-8")
    (package / "descriptor.json").write_text(json.dumps({
        "descriptor_hash": discovery_clean.get("descriptor_hash"),
        "connection": discovery_clean.get("connection"),
        "product_string": discovery_clean.get("product_string"),
    }, indent=2), encoding="utf-8")
    (package / "firmware.json").write_text(json.dumps({
        "observed": discovery_clean.get("firmware"),
        "expected": (discovery_clean.get("bundle") or {}).get("firmware_branch"),
    }, indent=2), encoding="utf-8")
    (package / "plan.json").write_text(json.dumps(plan_clean, ensure_ascii=False, indent=2), encoding="utf-8")
    (package / "baselines" / "baselines.json").write_text(json.dumps(baselines_clean, ensure_ascii=False, indent=2), encoding="utf-8")
    (package / "final_state.json").write_text(json.dumps(final_state_clean, ensure_ascii=False, indent=2), encoding="utf-8")
    (package / "recovery" / "runstate.json").write_text(json.dumps(recovery_clean, ensure_ascii=False, indent=2), encoding="utf-8")

    # evidence + per-op certificates + normalized miner observations
    observations = []
    for i, ev in enumerate(evidence):
        d = ev.__dict__ if hasattr(ev, "__dict__") else dict(ev)
        safe = {k: v for k, v in d.items() if k not in ("evidence_strength",)}
        safe["evidence_strength"] = ev.evidence_strength if hasattr(ev, "evidence_strength") else d.get("evidence_strength", [])
        safe_scrubbed = privacy_scrub_data(safe)
        (package / "evidence" / f"{i:03d}_{getattr(ev, 'operation', 'op')}.json").write_text(
            json.dumps(safe_scrubbed, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        observations.append({
            "operation": getattr(ev, "operation", None),
            "kind": "write_readback_rollback",
            "expected": getattr(ev, "temporary_value", None),
            "observed_readback": getattr(ev, "readback", None),
            "readback_matched": getattr(ev, "readback_matched", False),
            "rollback_readback": getattr(ev, "rollback_readback", None),
            "rollback_matched": getattr(ev, "rollback_matched", False),
            "status": getattr(ev, "status", None),
            "evidence_strength": getattr(ev, "evidence_strength", []),
            "timing_ms": getattr(ev, "timing_ms", {}),
        })

    for c in certificates:
        fname = f"{c['operation'].replace('.', '_')}.vetrojson"
        (package / "certificates" / fname).write_text(
            json.dumps(privacy_scrub_data(c["certificate"]), ensure_ascii=False, indent=2), encoding="utf-8")

    (package / "miner_input" / "observations.json").write_text(
        json.dumps({"schema": "vetro.miner-observations.v1", "observations": observations},
                   ensure_ascii=False, indent=2), encoding="utf-8")

    know_rev = discovery_clean.get("knowledge_revision") or "aula_kb_v3_r1"
    b_commit = get_build_commit()

    manifest = {
        "schema": PACKAGE_SCHEMA_VERSION,
        "package_schema_version": PACKAGE_SCHEMA_VERSION,
        "probe_app_version": PROBE_APP_VERSION,
        "probe_engine_version": PROBE_ENGINE_VERSION,
        "build_commit": b_commit,
        "knowledge_revision": know_rev,
        "run_id": run_id,
        "label": label,
        "terminal": terminal,
        "timestamp": time.time(),
        "device": discovery_clean,
        "summary": {
            "planned": len(plan),
            "executed": len(evidence),
            "passed": sum(1 for e in evidence if getattr(e, "status", "") == "PASS"),
            "blocked": sum(1 for p in plan if p.get("classification") == "BLOCKED"),
            "failed": sum(1 for e in evidence if getattr(e, "status", "") == "FAIL"),
            "baseline_restored": final_state_clean.get("restored", False),
        },
    }

    closure_path = package / "external_readonly_closure.json"
    if closure_path.is_file():
        manifest["external_closure"] = json.loads(closure_path.read_text(encoding="utf-8"))
    verdict_path = package / "final_verdict.json"
    if verdict_path.is_file():
        vd = json.loads(verdict_path.read_text(encoding="utf-8"))
        manifest["final_physical_verdict"] = vd.get("verdict")
        manifest["final_verdict"] = vd

    (package / "run_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (package / "summary.json").write_text(json.dumps(manifest["summary"], ensure_ascii=False, indent=2), encoding="utf-8")

    # Generate Human Summary summary.txt
    product_name = discovery_clean.get("product_string") or (discovery_clean.get("device") or {}).get("name") or "Unknown Device"
    fw_str = discovery_clean.get("firmware") or (discovery_clean.get("device") or {}).get("firmware") or "unknown"
    vid_str = discovery_clean.get("vid") or "unknown"
    pid_str = discovery_clean.get("pid") or "unknown"
    fam_str = discovery_clean.get("family") or "unknown"
    restored_str = "Yes (Verified ✓)" if final_state_clean.get("restored", False) else "No"

    human_summary = [
        "=======================================================",
        f"Vetro Probe v{PROBE_APP_VERSION} — Research Summary",
        "=======================================================",
        f"Device: {product_name}",
        f"Firmware: {fw_str}",
        f"VID/PID: {vid_str}:{pid_str}",
        f"Family: {fam_str}",
        f"Run ID: {run_id}",
        f"Build Commit: {b_commit}",
        f"Knowledge Revision: {know_rev}",
        f"Result: {terminal}",
        f"Executed Checks: {manifest['summary']['executed']}",
        f"Passed Checks: {manifest['summary']['passed']}",
        f"Failed Checks: {manifest['summary']['failed']}",
        f"Safely Skipped Checks: {manifest['summary']['blocked']}",
        f"Original Settings Restored: {restored_str}",
        "=======================================================",
    ]
    (package / "summary.txt").write_text("\n".join(human_summary) + "\n", encoding="utf-8")

    # Run Automated Privacy Scanner on all files before ZIP
    violations = scan_package_for_privacy_violations(package)
    if violations:
        for file_path in package.rglob("*"):
            if file_path.is_file() and file_path.suffix in (".json", ".txt", ".vetrojson"):
                text = file_path.read_text(encoding="utf-8", errors="ignore")
                scrubbed_text = privacy_scrub_string(text)
                if scrubbed_text != text:
                    file_path.write_text(scrubbed_text, encoding="utf-8")
        violations = scan_package_for_privacy_violations(package)
        if violations:
            raise ValueError(f"Privacy scan violation: unredacted personal path in {violations[0]}")

    # Build unique ZIP with overwrite protection
    zip_name = generate_package_zip_name(discovery, run_id, terminal)
    parent_dir = package.parent
    zip_target = parent_dir / zip_name

    # Overwrite protection
    if zip_target.exists():
        counter = 1
        base_stem = zip_target.stem
        while (parent_dir / f"{base_stem}_{counter}.zip").exists():
            counter += 1
        zip_target = parent_dir / f"{base_stem}_{counter}.zip"

    with zipfile.ZipFile(zip_target, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for file_path in package.rglob("*"):
            if file_path.is_file() and not file_path.name.endswith(".zip") and not file_path.name.endswith(".sha256"):
                arcname = file_path.relative_to(package)
                zf.write(file_path, arcname)

    # Also make a copy with standard name for fallback compatibility
    try:
        standard_zip = parent_dir / "vetro_probe_results.zip"
        standard_zip.write_bytes(zip_target.read_bytes())
    except Exception:
        pass

    # Compute SHA-256
    zip_bytes = zip_target.read_bytes()
    sha256_hash = hashlib.sha256(zip_bytes).hexdigest()
    sha256_file = zip_target.with_suffix(".zip.sha256")
    sha256_file.write_text(f"{sha256_hash} *{zip_target.name}\n", encoding="utf-8")

    # Record package metadata
    (package / "package_metadata.json").write_text(json.dumps({
        "package_filename": zip_target.name,
        "package_sha256": sha256_hash,
        "run_id": run_id,
        "probe_app_version": PROBE_APP_VERSION,
        "probe_engine_version": PROBE_ENGINE_VERSION,
        "build_commit": b_commit,
        "knowledge_revision": know_rev,
        "terminal": terminal,
    }, indent=2), encoding="utf-8")

    return package
