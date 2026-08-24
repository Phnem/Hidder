"""BaselineSnapshot: normalized state before any writes.

If baseline for rollback-required operation cannot be obtained -> SKIP/BLOCKED, do not experiment.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from .transport import DeviceTransport


@dataclass
class BaselineSnapshot:
    values: dict[str, Any] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    hash: str = ""

    def compute_hash(self) -> str:
        return hashlib.sha256(json.dumps(self.values, sort_keys=True).encode()).hexdigest()[:16]


class BaselineCollector:
    def __init__(self, transport: DeviceTransport) -> None:
        self.transport = transport

    def collect(self, operation_ids: list[str]) -> BaselineSnapshot:
        snap = BaselineSnapshot()
        for op_id in operation_ids:
            val, res = self.transport.get(op_id)
            if res.ok:
                snap.values[op_id] = val
            else:
                snap.errors[op_id] = res.error
        snap.hash = snap.compute_hash()
        return snap

    def can_rollback(self, snap: BaselineSnapshot, operation_id: str) -> bool:
        return operation_id in snap.values and operation_id not in snap.errors

    def passive_safe_verification(self, operation_ids: list[str]) -> tuple[bool, dict[str, Any]]:
        """Requirement: before first write, safe GETs must succeed. Returns (ok, details)."""
        snap = self.collect(operation_ids)
        # at least one passive read must succeed to prove READ_PATH
        if not snap.values:
            return False, {"errors": snap.errors, "snapshot": snap}
        return True, {"snapshot": snap}
