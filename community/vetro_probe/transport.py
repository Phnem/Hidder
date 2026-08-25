"""Transport abstraction. Local serializer owns frame encoding; plan never supplies raw bytes."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class TransportResult:
    ok: bool
    latency_ms: int = 0
    error: str = ""
    raw_echo: bytes | None = None  # only for evidence, not for decision (ACK != PASS)


class DeviceTransport(ABC):
    @abstractmethod
    def get(self, operation_id: str) -> tuple[Any, TransportResult]:
        ...

    @abstractmethod
    def set(self, operation_id: str, semantic_value: Any) -> TransportResult:
        ...

    @abstractmethod
    def is_connected(self) -> bool:
        ...

    @abstractmethod
    def invalidate(self) -> None:
        """Invalidate session immediately (for reconnect ops)."""

    @abstractmethod
    def current_session_id(self) -> int:
        ...

    def fresh_session(self) -> "DeviceTransport":
        """Return a NEW session/handle to the same physical device.

        The current session is invalidated and must never be used again.
        Default: not supported -> raises NotImplementedError.
        """
        raise NotImplementedError(f"{self.__class__.__name__} does not support fresh_session()")


class _FakeDevice:
    """Shared physical-device state across FakeTransport sessions (A/B/C/D)."""

    def __init__(self, initial_state: dict[str, Any] | None = None, reconnect_ops: set[str] | None = None) -> None:
        self.state: dict[str, Any] = dict(initial_state or {})
        self.reconnect_ops: set[str] = set(reconnect_ops or set())
        self.next_session: int = 1
        self.write_count: int = 0
        self.get_count: int = 0
        self.session_write_counts: dict[int, int] = {}
        self.session_get_counts: dict[int, int] = {}
        self.stale_get_count: int = 0
        self.stale_set_count: int = 0
        # safety-gate knobs for negative tests
        self.identity_ok: bool = True
        self.firmware_value: str = "0216"
        self.ambiguous: bool = False
        self.readback_fail_sessions: set[int] = set()
        self.get_fail_sessions: set[int] = set()
        self.get_fail_error: str = "injected get failure"


class FakeTransport(DeviceTransport):
    """Deterministic fake for tests and headless vertical slice.

    A FakeTransport is one SESSION to a shared _FakeDevice. `fresh_session()`
    invalidates the current session and returns a new one against the same
    physical device state — this is how A/B/C/D are modelled.
    """

    def __init__(self, initial_state: dict[str, Any] | None = None, reconnect_ops: set[str] | None = None) -> None:
        self._device = _FakeDevice(initial_state, reconnect_ops)
        self._init_session()

    def _init_session(self) -> None:
        self._session_id = self._device.next_session
        self._device.next_session += 1
        self._valid = True
        self._fail_next_get: dict[str, str] = {}
        self._fail_next_set: dict[str, str] = {}
        self._readback_mismatch: dict[str, Any] = {}

    @classmethod
    def _new_on(cls, device: _FakeDevice) -> "FakeTransport":
        t = cls.__new__(cls)
        t._device = device
        t._init_session()
        return t

    # --- shared physical device accessors (kept for existing tests) ---
    @property
    def state(self) -> dict[str, Any]:
        return self._device.state

    @property
    def reconnect_ops(self) -> set[str]:
        return self._device.reconnect_ops

    @property
    def device(self) -> _FakeDevice:
        return self._device

    def fresh_session(self) -> "FakeTransport":
        self.invalidate()
        return FakeTransport._new_on(self._device)

    def get(self, operation_id: str) -> tuple[Any, TransportResult]:
        if not self._valid:
            self._device.stale_get_count += 1
            return None, TransportResult(False, error="stale session")
        if self._session_id in self._device.readback_fail_sessions:
            self._device.session_get_counts[self._session_id] = self._device.session_get_counts.get(self._session_id, 0) + 1
            return None, TransportResult(False, error="simulated readback failure (null)")
        if self._session_id in self._device.get_fail_sessions:
            self._device.session_get_counts[self._session_id] = self._device.session_get_counts.get(self._session_id, 0) + 1
            return None, TransportResult(False, error=self._device.get_fail_error)
        if operation_id in self._fail_next_get:
            err = self._fail_next_get.pop(operation_id)
            return None, TransportResult(False, error=err)
        if operation_id not in self.state:
            return None, TransportResult(False, error="no baseline: key missing")
        self._device.session_get_counts[self._session_id] = self._device.session_get_counts.get(self._session_id, 0) + 1
        return self.state.get(operation_id), TransportResult(True, latency_ms=5)

    def set(self, operation_id: str, semantic_value: Any) -> TransportResult:
        if not self._valid:
            self._device.stale_set_count += 1
            return TransportResult(False, error="stale session")
        if operation_id in self._fail_next_set:
            err = self._fail_next_set.pop(operation_id)
            return TransportResult(False, error=err)
        # store semantic value; for readback mismatch tests, store mismatch value for next get
        if operation_id in self._readback_mismatch:
            self.state[operation_id] = self._readback_mismatch[operation_id]
        else:
            self.state[operation_id] = semantic_value
        self._device.write_count += 1
        self._device.session_write_counts[self._session_id] = self._device.session_write_counts.get(self._session_id, 0) + 1
        res = TransportResult(True, latency_ms=12)
        if operation_id in self.reconnect_ops:
            # simulate device disappearing right after write; invalidate immediately
            self._valid = False
        return res

    def is_connected(self) -> bool:
        return self._valid

    def invalidate(self) -> None:
        self._valid = False

    def current_session_id(self) -> int:
        return self._session_id

    # --- test helpers ---
    def simulate_reconnect(self, new_state: dict[str, Any] | None = None) -> None:
        # Legacy: bump session on the same object. New code uses fresh_session().
        self._session_id = self._device.next_session
        self._device.next_session += 1
        self._valid = True
        if new_state is not None:
            self.state.update(new_state)

    def inject_get_failure(self, op_id: str, error: str) -> None:
        self._fail_next_get[op_id] = error

    def inject_set_failure(self, op_id: str, error: str) -> None:
        self._fail_next_set[op_id] = error

    def inject_readback_mismatch(self, op_id: str, wrong_value: Any) -> None:
        self._readback_mismatch[op_id] = wrong_value

    def clear_mismatch(self, op_id: str) -> None:
        self._readback_mismatch.pop(op_id, None)


class HidTransport(DeviceTransport):
    """Typed production HidTransport placeholder.

    Real implementation is AulaHidTransport in aula_transport.py which wraps
    HidRawTransport (hidapi) or simulator and dispatches typed operation_ids
    through aula_kb_v3.operations + SafetyGate. Never accepts raw bytes.
    This stub is kept for backward imports; use AulaHidTransport directly.
    """

    def __init__(self, instance: Any | None = None) -> None:
        self._session_id = 1
        self._valid = False
        self._instance = instance

    def get(self, operation_id: str) -> tuple[Any, TransportResult]:
        return None, TransportResult(False, error="HidTransport stub: use AulaHidTransport.open_real() or FakeTransport")

    def set(self, operation_id: str, semantic_value: Any) -> TransportResult:
        return TransportResult(False, error="HidTransport stub: use AulaHidTransport.open_real()")

    def is_connected(self) -> bool:
        return False

    def invalidate(self) -> None:
        self._valid = False

    def current_session_id(self) -> int:
        return self._session_id


# Re-export production transport for convenience
try:
    from .aula_transport import AulaHidTransport  # noqa: F401
except Exception:
    AulaHidTransport = HidTransport  # type: ignore
