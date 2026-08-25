"""RecoveryJournal — armed before first write, used by Final Restore Gate."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
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


@dataclass
class RecoveryRecord:
    """Runtime-filled journal for a reconnect-triggering write + recovery.

    baseline_restored=true is set ONLY after a final GET through a fresh session.
    """

    operation: str = ""
    baseline: Any = None
    attempted: Any = None
    expected_firmware: str = "0216"
    observed_firmware: str = ""
    session_a: int | None = None
    session_b: int | None = None
    session_c: int | None = None
    session_d: int | None = None
    reconnect_occurred: bool = False
    identity_result: str = ""
    firmware_result: str = ""
    initial_readback_result: str = ""
    recovery_started: bool = False
    observed_current: Any = None
    recovery_write_attempted: bool = False
    recovery_write_result: str = ""
    second_reconnect: bool = False
    final_session: int | None = None
    final_get: Any = None
    baseline_restored: bool = False
    recovery_blocked: bool = False
    recovery_block_reason: str = ""
    last_known_state: Any = None
    final_state: Any = None  # UNKNOWN if not independently observed
    manual_restore_required: bool = False
    initial_write_may_have_applied: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RecoveryJournal:
    def __init__(self, snapshot: BaselineSnapshot) -> None:
        self.initial_hash = snapshot.hash
        self.initial_values = dict(snapshot.values)
        self.entries: dict[str, RecoveryEntry] = {
            op: RecoveryEntry(op, val) for op, val in snapshot.values.items()
        }
        self.armed = True
        self.recovery_record = RecoveryRecord()

    def start_recovery(self, operation: str, baseline: Any, attempted: Any, session_a: int | None) -> RecoveryRecord:
        r = self.recovery_record
        r.operation = operation
        r.baseline = baseline
        r.attempted = attempted
        r.session_a = session_a
        r.initial_write_may_have_applied = True
        r.recovery_started = True
        return r

    def block_recovery(self, reason: str) -> None:
        r = self.recovery_record
        r.recovery_blocked = True
        r.recovery_block_reason = reason
        r.baseline_restored = False
        r.manual_restore_required = True
        if r.final_state is None:
            r.final_state = "UNKNOWN"

    def complete_recovery(self, final_session: int | None, final_get: Any, restored: bool) -> None:
        r = self.recovery_record
        r.final_session = final_session
        r.final_get = final_get
        r.final_state = final_get if final_get is not None else "UNKNOWN"
        r.baseline_restored = restored
        if not restored:
            r.manual_restore_required = True
            r.recovery_blocked = True
            r.recovery_block_reason = "final GET did not match baseline"
            r.final_state = "UNKNOWN" if final_get is None else final_get

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
