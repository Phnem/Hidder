"""RecoveryJournal — armed before first write, used by Final Restore Gate."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .baseline import BaselineSnapshot
from .transport import DeviceTransport


@dataclass
class RecoveryEntry:
    operation_id: str
    baseline_value: Any
    attempted: bool = False
    restored: bool = False
    error: str = ""


class RecoveryJournal:
    def __init__(self, snapshot: BaselineSnapshot) -> None:
        self.initial_hash = snapshot.hash
        self.initial_values = dict(snapshot.values)
        self.entries: dict[str, RecoveryEntry] = {
            op: RecoveryEntry(op, val) for op, val in snapshot.values.items()
        }
        self.armed = True

    def record_attempt(self, operation_id: str) -> None:
        if operation_id in self.entries:
            self.entries[operation_id].attempted = True

    def record_restored(self, operation_id: str, success: bool, error: str = "") -> None:
        if operation_id in self.entries:
            self.entries[operation_id].restored = success
            self.entries[operation_id].error = error

    def needs_recovery(self) -> list[str]:
        return [op for op, e in self.entries.items() if e.attempted and not e.restored]

    def recover_all(self, transport: DeviceTransport) -> dict[str, str]:
        """Attempt to restore every attempted op to baseline. Fail-closed: report errors."""
        results: dict[str, str] = {}
        for op, entry in self.entries.items():
            if not entry.attempted:
                continue
            if entry.restored:
                continue
            res = transport.set(op, entry.baseline_value)
            if res.ok:
                # verify readback
                val, gres = transport.get(op)
                if gres.ok and val == entry.baseline_value:
                    entry.restored = True
                    results[op] = "recovered"
                else:
                    results[op] = f"readback mismatch after recovery: {val!r} != {entry.baseline_value!r}"
            else:
                results[op] = res.error
            entry.error = results[op]
        return results

    def final_matches_initial(self, final_snapshot: BaselineSnapshot) -> bool:
        return final_snapshot.hash == self.initial_hash

    def final_diff(self, final_snapshot: BaselineSnapshot) -> dict[str, Any]:
        diff: dict[str, Any] = {}
        for op, init_val in self.initial_values.items():
            final_val = final_snapshot.values.get(op)
            if final_val != init_val:
                diff[op] = {"expected": init_val, "actual": final_val, "error": final_snapshot.errors.get(op, "")}
        return diff
