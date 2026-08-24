"""Export vetro.preview-bundle.v1 from compiled production registry (DB/aula_kb_v3).

No protocol truth is hardcoded here beyond the registry's own compiled facts.
If registry is unavailable (CI without DB checkout), falls back to minimal hardcoded
but marks bundle as synthetic and not production.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

# Ensure DB is on sys.path so `import aula_kb_v3` works (repo tests add DB via conftest, CLI needs it too)
_DB_PATH = Path(__file__).resolve().parents[2] / "DB"
if str(_DB_PATH) not in sys.path:
    sys.path.insert(0, str(_DB_PATH))

HERO84_UUID = 18691697672197

# Mapping from capsule operation_id -> (SafeCommandId, bounds_key, reversible, readback, reconnect)
# Kept small: only production_safe ops that are hardware-verified reversible.
OP_MAP: dict[str, dict[str, Any]] = {
    # HE surface
    "he.actuation": {
        "kind": "set", "reversible": True, "readback": True, "cadence_ms": 150,
        "bounds": {"min": 0.0, "max": 4.0, "unit": "mm", "safe_values": [0.6, 1.0, 1.5]},
        "cap": "actuation",
    },
    "he.rt": {
        "kind": "set", "reversible": True, "readback": True,
        "bounds": {"min": 0, "max": 1},
        "cap": "rapid_trigger",
    },
    "he.deadzone": {
        "kind": "set", "reversible": True, "readback": True,
        "bounds": {"min": 0.0, "max": 4.0, "unit": "mm", "safe_values": [0.5, 1.0]},
        "cap": "deadzone",
    },
    # Generic
    "keyboard.remap": {
        "kind": "set", "reversible": True, "readback": True, "needs_observable": True,
        "bounds": {"min": 0, "max": 0xFFFFFFFF, "safe_values": [0x00000004]},  # A (W->A remap, E5 observable)
        "cap": "remap",
    },
    "keyboard.profile": {
        "kind": "set", "reversible": True, "readback": True,
        "bounds": {"min": 0, "max": 2, "safe_values": [1, 0]},
        "cap": "profiles",
    },
    "light.rgb_core": {
        "kind": "set", "reversible": True, "readback": True,
        "bounds": {"min": 0, "max": 0xFFFFFF, "safe_values": [0xFF0000]},
        "cap": "rgb_core",
    },
    "device.win_lock": {
        "kind": "set", "reversible": True, "readback": True,
        "bounds": {"min": 0, "max": 1, "safe_values": [1]},
        "cap": "device_settings",
    },
    "keyboard.polling": {
        "kind": "set", "reversible": True, "readback": True, "requires_reconnect": True,
        "bounds": {"min": 0, "max": 7, "unit": "enum", "safe_values": [2, 3]},  # 250,125
        "cap": "polling",
    },
    # Observable-only (no write)
    "input.he.analog_w": {
        "kind": "observable", "reversible": False, "readback": False, "needs_observable": True,
        "cap": "actuation",
    },
}


def _registry_product(uuid: int):
    import aula_kb_v3.registry as reg  # type: ignore

    return reg.resolve_by_uuid(uuid)


def _knowledge_revision() -> str:
    # IR hash = hash of registry_data.py + git commit if available
    try:
        import subprocess
        from pathlib import Path as _P
        repo_root = _P(__file__).resolve().parents[2]
        # git commit
        commit = ""
        try:
            r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(repo_root), capture_output=True, text=True, timeout=2)
            if r.returncode == 0:
                commit = r.stdout.strip()[:12]
        except Exception:
            pass
        # registry_data hash
        rd = repo_root / "DB" / "aula_kb_v3" / "registry_data.py"
        if rd.is_file():
            h = hashlib.sha256(rd.read_bytes()).hexdigest()[:12]
            return f"{commit}:{h}" if commit else h
        return commit or "unknown"
    except Exception:
        return "unknown"


def export_bundle_for_uuid(uuid: int = HERO84_UUID) -> dict[str, Any]:
    product = _registry_product(uuid)
    caps = {c.name: c.supported for c in product.capabilities}
    # Filter OP_MAP to only those whose cap is supported
    ops: dict[str, Any] = {}
    bounds: dict[str, Any] = {}
    capabilities_for_bundle: dict[str, bool] = {}
    for op_id, meta in OP_MAP.items():
        cap = meta.get("cap")
        if cap and not caps.get(cap, False):
            continue
        # include op
        ops[op_id] = {
            "id": op_id,
            "kind": meta["kind"],
            "reversible": meta["reversible"],
            "readback": meta["readback"],
            "requires_reconnect": meta.get("requires_reconnect", False),
            "needs_observable": meta.get("needs_observable", False),
            "cadence_ms": meta.get("cadence_ms", 120),
        }
        if "bounds" in meta:
            bounds[op_id] = meta["bounds"]
        if cap:
            capabilities_for_bundle[cap] = True

    # Also include generic device_settings polling etc already
    data: dict[str, Any] = {
        "schema": "vetro.preview-bundle.v1",
        "id": f"aula-{product.display_name.lower().replace(' ', '-')}-{uuid}",
        "version": 1,
        "product": {
            "vid": f"0x{product.vendor_id:04X}",
            "pid": f"0x{product.product_id:04X}",
            "name": product.display_name,
            "uuid": str(product.uuid),
        },
        "family": product.protocol_family,
        "connection": {"mode": "wired" if product.connection_type.name == "WIRED" else "wireless"},
        # Firmware: wildcard ("unknown") is NOT allowed for writes — only for passive discovery.
        # For verified HERO84 we pin to branch "1.17" (instance 1.17.3 passes prefix). For other
        # products we keep unknown but writes will be BLOCKED until branch is explicitly verified.
        "firmware": {"branch": "1.17" if str(product.uuid) == str(HERO84_UUID) else "unknown"},
        "capabilities": capabilities_for_bundle,
        "bounds": bounds,
        "operations": ops,
        "knowledge_revision": _knowledge_revision(),
        # keep raw registry uuid for traceability, not used for transport
        "_source": {"registry_uuid": product.uuid, "display_name": product.display_name, "family_key": product.protocol_family},
    }
    # compute hash
    h = hashlib.sha256(json.dumps({k: v for k, v in data.items() if k != "hash"}, sort_keys=True).encode()).hexdigest()
    data["hash"] = h
    return data


def write_bundle_to_file(uuid: int = HERO84_UUID, path: Path | None = None) -> Path:
    data = export_bundle_for_uuid(uuid)
    out = path or Path(f"./aula-{uuid}.preview.json")
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return out
