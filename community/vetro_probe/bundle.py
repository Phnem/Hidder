"""Typed Preview Bundle loader. No raw opcodes. Fail-closed."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ProductRef:
    vid: str
    pid: str
    name: str
    uuid: str = ""


@dataclass(frozen=True)
class OperationDef:
    id: str
    kind: str
    reversible: bool
    readback: bool
    requires_reconnect: bool = False
    needs_observable: bool = False
    cadence_ms: int = 120
    rollback_strategy: str = "restore_value"


@dataclass(frozen=True)
class Bundle:
    schema: str
    id: str
    version: int
    hash: str
    product: ProductRef
    family: str
    connection_mode: str
    firmware_branch: str
    capabilities: dict[str, bool]
    bounds: dict[str, dict[str, Any]]
    operations: dict[str, OperationDef]
    raw: dict[str, Any] = field(default_factory=dict, compare=False, hash=False, repr=False)

    def get_operation(self, op_id: str) -> OperationDef | None:
        return self.operations.get(op_id)

    def has_bounds(self, op_id: str) -> bool:
        return op_id in self.bounds


def _norm_hash(data: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def load_bundle(path: Path) -> Bundle:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return parse_bundle(data)


def parse_bundle(data: dict[str, Any]) -> Bundle:
    if data.get("schema") != "vetro.preview-bundle.v1":
        raise ValueError(f"unsupported bundle schema: {data.get('schema')}")
    bundle_id = str(data["id"])
    version = int(data["version"])
    family = str(data["family"])
    product_raw = data["product"]
    product = ProductRef(
        vid=str(product_raw["vid"]),
        pid=str(product_raw["pid"]),
        name=str(product_raw["name"]),
        uuid=str(product_raw.get("uuid", "")),
    )
    connection_mode = str(data.get("connection", {}).get("mode", "any"))
    firmware_branch = str(data.get("firmware", {}).get("branch", "unknown"))

    ops_raw: dict[str, Any] = data.get("operations", {})
    ops: dict[str, OperationDef] = {}
    for op_id, o in ops_raw.items():
        # Forbid raw opcode passthrough
        if any(k in o for k in ("raw_bytes", "opcode", "report_id", "frame")):
            raise ValueError(f"operation {op_id} contains forbidden raw frame field")
        ops[op_id] = OperationDef(
            id=str(o.get("id", op_id)),
            kind=str(o.get("kind", "set")),
            reversible=bool(o.get("reversible", False)),
            readback=bool(o.get("readback", False)),
            requires_reconnect=bool(o.get("requires_reconnect", False)),
            needs_observable=bool(o.get("needs_observable", False)),
            cadence_ms=int(o.get("cadence_ms", 120)),
            rollback_strategy=str(o.get("rollback_strategy", "restore_value")),
        )

    bundle_hash = str(data.get("hash") or _norm_hash({k: v for k, v in data.items() if k != "hash"}))

    return Bundle(
        schema=str(data["schema"]),
        id=bundle_id,
        version=version,
        hash=bundle_hash,
        product=product,
        family=family,
        connection_mode=connection_mode,
        firmware_branch=firmware_branch,
        capabilities=dict(data.get("capabilities", {})),
        bounds=dict(data.get("bounds", {})),
        operations=ops,
        raw=dict(data),
    )


def example_hero84_bundle() -> Bundle:
    """Minimal HERO84 B Preview for vertical slice and tests."""
    data: dict[str, Any] = {
        "schema": "vetro.preview-bundle.v1",
        "id": "aula-hero84-he-v1",
        "version": 1,
        "product": {"vid": "0x372E", "pid": "0x103E", "name": "AULA HERO84 HE", "uuid": "aula-hero84-he"},
        "family": "aula_he_v3",
        "connection": {"mode": "wired"},
        "firmware": {"branch": "1.17"},
        "capabilities": {
            "rgb": True,
            "polling": True,
            "actuation": True,
            "rapid_trigger": True,
            "profiles": True,
        },
        "bounds": {
            "he.actuation": {"min": 0.1, "max": 3.4, "unit": "mm", "safe_values": [0.4, 0.6, 0.8, 1.0]},
            "he.rt.enabled": {"min": 0, "max": 1},
            "keyboard.polling": {"min": 125, "max": 8000, "unit": "hz", "safe_values": [1000, 2000]},
            "light.brightness": {"min": 0, "max": 100, "safe_values": [50]},
        },
        "operations": {
            "he.actuation": {"id": "he.actuation", "kind": "set", "reversible": True, "readback": True, "cadence_ms": 150},
            "he.rt.enabled": {"id": "he.rt.enabled", "kind": "toggle", "reversible": True, "readback": True},
            "keyboard.polling": {"id": "keyboard.polling", "kind": "set", "reversible": True, "readback": True, "requires_reconnect": True},
            "light.brightness": {"id": "light.brightness", "kind": "set", "reversible": True, "readback": True},
            "input.he.analog_w": {"id": "input.he.analog_w", "kind": "observable", "reversible": False, "readback": False, "needs_observable": True},
        },
    }
    return parse_bundle(data)
