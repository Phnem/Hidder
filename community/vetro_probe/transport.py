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


class FakeTransport(DeviceTransport):
    """Deterministic fake for tests and headless vertical slice.

    Stores a dict of semantic values. Optionally simulates
    reconnect-required ops by marking session invalid after set.
    """

    def __init__(self, initial_state: dict[str, Any] | None = None, reconnect_ops: set[str] | None = None) -> None:
        self.state: dict[str, Any] = dict(initial_state or {})
        self.reconnect_ops = set(reconnect_ops or set())
        self._session_id = 1
        self._valid = True
        self._fail_next_get: dict[str, str] = {}
        self._fail_next_set: dict[str, str] = {}
        self._readback_mismatch: dict[str, Any] = {}

    def get(self, operation_id: str) -> tuple[Any, TransportResult]:
        if not self._valid:
            return None, TransportResult(False, error="stale session")
        if operation_id in self._fail_next_get:
            err = self._fail_next_get.pop(operation_id)
            return None, TransportResult(False, error=err)
        if operation_id not in self.state:
            return None, TransportResult(False, error="no baseline: key missing")
        val = self.state.get(operation_id)
        return val, TransportResult(True, latency_ms=5)

    def set(self, operation_id: str, semantic_value: Any) -> TransportResult:
        if not self._valid:
            return TransportResult(False, error="stale session")
        if operation_id in self._fail_next_set:
            err = self._fail_next_set.pop(operation_id)
            return TransportResult(False, error=err)
        # store semantic value; for readback mismatch tests, store mismatch value for next get
        if operation_id in self._readback_mismatch:
            self.state[operation_id] = self._readback_mismatch[operation_id]
        else:
            self.state[operation_id] = semantic_value
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
        self._session_id += 1
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
    """Placeholder for real HID transport. Not used in headless slice.

    When implemented, it will:
    - resolve typed operation -> serializer -> frame via bundle
    - send via hidapi
    - never accept raw bytes from caller
    """

    def __init__(self, instance: Any | None = None) -> None:
        self._session_id = 1
        self._valid = True
        self._instance = instance

    def get(self, operation_id: str) -> tuple[Any, TransportResult]:
        return None, TransportResult(False, error="HID transport not connected (headless)")

    def set(self, operation_id: str, semantic_value: Any) -> TransportResult:
        return TransportResult(False, error="HID transport not connected (headless)")

    def is_connected(self) -> bool:
        return False

    def invalidate(self) -> None:
        self._valid = False

    def current_session_id(self) -> int:
        return self._session_id
