"""Miner-ready export package builder.

Probe exports OBSERVATIONS / EVIDENCE only — never new protocol truths.
Miner does the inference.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


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

    (package / "device_identity.json").write_text(json.dumps(discovery, ensure_ascii=False, indent=2), encoding="utf-8")
    (package / "descriptor.json").write_text(json.dumps({
        "descriptor_hash": discovery.get("descriptor_hash"),
        "connection": discovery.get("connection"),
        "product_string": discovery.get("product_string"),
    }, indent=2), encoding="utf-8")
    (package / "firmware.json").write_text(json.dumps({
        "observed": discovery.get("firmware"),
        "expected": discovery.get("bundle", {}).get("firmware_branch"),
    }, indent=2), encoding="utf-8")
    (package / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    (package / "baselines" / "baselines.json").write_text(json.dumps(baselines, ensure_ascii=False, indent=2), encoding="utf-8")
    (package / "final_state.json").write_text(json.dumps(final_state, ensure_ascii=False, indent=2), encoding="utf-8")
    (package / "recovery" / "runstate.json").write_text(json.dumps(recovery, ensure_ascii=False, indent=2), encoding="utf-8")

    # evidence + per-op certificates + normalized miner observations
    observations = []
    for i, ev in enumerate(evidence):
        d = ev.__dict__ if hasattr(ev, "__dict__") else dict(ev)
        safe = {k: v for k, v in d.items() if k not in ("evidence_strength",)}
        safe["evidence_strength"] = ev.evidence_strength if hasattr(ev, "evidence_strength") else d.get("evidence_strength", [])
        (package / "evidence" / f"{i:03d}_{getattr(ev, 'operation', 'op')}.json").write_text(
            json.dumps(safe, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
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
            json.dumps(c["certificate"], ensure_ascii=False, indent=2), encoding="utf-8")

    (package / "miner_input" / "observations.json").write_text(
        json.dumps({"schema": "vetro.miner-observations.v1", "observations": observations},
                   ensure_ascii=False, indent=2), encoding="utf-8")

    manifest = {
        "schema": "vetro.run-manifest.v1",
        "run_id": run_id,
        "label": label,
        "terminal": terminal,
        "timestamp": time.time(),
        "device": discovery,
        "summary": {
            "planned": len(plan),
            "executed": len(evidence),
            "passed": sum(1 for e in evidence if getattr(e, "status", "") == "PASS"),
            "blocked": sum(1 for p in plan if p.get("classification") == "BLOCKED"),
            "failed": sum(1 for e in evidence if getattr(e, "status", "") == "FAIL"),
            "baseline_restored": final_state.get("restored", False),
        },
    }
    # Additive external/read-only closure (if present): Miner sees the distinction
    # between the initial in-run aggregate read (possibly UNRELIABLE_DESYNC) and
    # the authoritative follow-up zero-write verification + final physical verdict.
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

    # Build single-file tester handoff archives (vetro_probe_results.zip)
    try:
        import zipfile
        zip_targets = [
            package / "vetro_probe_results.zip",
            package.parent / "vetro_probe_results.zip",
        ]
        for zip_target in zip_targets:
            with zipfile.ZipFile(zip_target, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for file_path in package.rglob("*"):
                    if file_path.is_file() and file_path.name != "vetro_probe_results.zip":
                        arcname = file_path.relative_to(package)
                        zf.write(file_path, arcname)
    except Exception:
        pass

    return package
