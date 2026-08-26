"""Persisted run-state checkpointing + crash-safe recovery.

Minimal production variant of persisted crash recovery:
- after every critical stage the auto flow writes a checkpoint JSON to disk;
- on next launch, if an open checkpoint has write_may_have_applied=true,
  Probe does NOT start a new auto run; it enters recovery-first (fresh reacquire,
  exact identity, firmware gate, fresh GET current, safe baseline restore, final GET,
  close journal) before anything else.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

SCHEMA = "vetro.run-state.v1"


@dataclass
class RunCheckpoint:
    run_id: str = ""
    phase: str = "INIT"
    device: dict[str, Any] = field(default_factory=dict)
    operation: str = ""
    baseline: Any = None
    attempted: Any = None
    write_may_have_applied: bool = False
    reconnect_occurred: bool = False
    observed_current: Any = None
    rollback_attempted: bool = False
    final_verified: bool = False
    recovery_required: bool = False
    closed: bool = False
    error: str = ""
    timestamp: float = field(default_factory=time.time)
    transitions: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["schema"] = SCHEMA
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RunCheckpoint":
        allowed = {f for f in cls.__dataclass_fields__}  # type: ignore
        cp = cls(**{k: v for k, v in data.items() if k in allowed})
        cp.transitions = list(data.get("transitions", []))
        return cp

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")


class RunStateStore:
    """Persists the latest run checkpoint under a run directory."""

    def __init__(self, run_dir: Path) -> None:
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.checkpoint_path = self.run_dir / "runstate.json"
        self.journal_path = self.run_dir / "journal.json"

    def new_run(self) -> RunCheckpoint:
        cp = RunCheckpoint(run_id=f"run-{uuid.uuid4().hex[:12]}")
        cp.save(self.checkpoint_path)
        return cp

    def save(self, cp: RunCheckpoint) -> None:
        cp.timestamp = time.time()
        cp.save(self.checkpoint_path)
        self._append_journal(cp)

    def load(self) -> RunCheckpoint | None:
        if not self.checkpoint_path.is_file():
            return None
        try:
            return RunCheckpoint.from_dict(json.loads(self.checkpoint_path.read_text(encoding="utf-8")))
        except Exception:
            return None

    def _append_journal(self, cp: RunCheckpoint) -> None:
        try:
            rows: list[dict[str, Any]] = []
            if self.journal_path.is_file():
                rows = json.loads(self.journal_path.read_text(encoding="utf-8"))
            rows.append({"ts": cp.timestamp, "phase": cp.phase, "run_id": cp.run_id,
                         "operation": cp.operation, "write_may_have_applied": cp.write_may_have_applied,
                         "closed": cp.closed, "error": cp.error})
            self.journal_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    @staticmethod
    def open_write_pending(cp: RunCheckpoint | None) -> bool:
        return bool(cp and cp.write_may_have_applied and not cp.closed)


def recover_interrupted_run(cp: RunCheckpoint, make_transport, gate, enumerate_fn,
                            firmware_check=None, timeout_ms: int = 15000,
                            get_current=None, set_baseline=None) -> RunCheckpoint:
    """Crash-safe recovery: fresh reacquire, identity+firmware gate, fresh GET current,
    safe baseline restore, final GET. Sets closed=true only after final GET == baseline.
    If recovery cannot proceed safely, marks recovery_required + manual restore.
    """
    from .reconnect import ReconnectManager

    cp.phase = "RECOVERING"
    cp.recovery_required = True
    transport = make_transport()
    rm = ReconnectManager(transport, gate, enumerate_fn, timeout_ms=timeout_ms, poll_ms=200,
                          firmware_check=firmware_check)
    rm.begin_reconnect_write()  # invalidate stale handle from previous process
    rr = rm.acquire_fresh()
    if not rr.ok:
        cp.error = f"recovery reacquire failed: {rr.error}"
        cp.closed = False
        return cp
    fresh = rr.session
    try:
        cur = get_current(fresh, cp.operation)
    except Exception as exc:
        cp.error = f"recovery GET current failed: {exc}"
        cp.observed_current = None
        cp.closed = False
        return cp
    cp.observed_current = cur
    if cur is None or cur == cp.attempted or cur != cp.baseline:
        # If observed != baseline and != attempted, state is unexpected; do NOT blind-write.
        if cur != cp.baseline and cur == cp.attempted:
            # write had applied and is still applied -> safe typed restore to baseline
            fresh.invalidate()
            rr2 = rm.acquire_fresh()
            if not rr2.ok:
                cp.error = f"recovery reacquire (restore) failed: {rr2.error}"
                return cp
            fresh2 = rr2.session
            set_baseline(fresh2, cp.operation, cp.baseline)
            fresh2.invalidate()
            rr3 = rm.acquire_fresh()
            if not rr3.ok:
                cp.error = f"recovery final reacquire failed: {rr3.error}"
                return cp
            fresh3 = rr3.session
            final = get_current(fresh3, cp.operation)
            cp.final_verified = (final == cp.baseline)
            if cp.final_verified:
                cp.closed = True
                cp.phase = "COMPLETE"
            else:
                cp.error = f"recovery final GET {final!r} != baseline {cp.baseline!r}"
            return cp
        else:
            cp.error = f"recovery GET current {cur!r} unexpected (baseline {cp.baseline!r}, attempted {cp.attempted!r}) — manual restore"
            return cp
    # Already at baseline
    cp.final_verified = True
    cp.closed = True
    cp.phase = "COMPLETE"
    return cp
