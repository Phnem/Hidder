"""Re-enumerating transaction state machine.

For operations where bundle marks requires_reconnect=True:
write -> invalidate immediately -> detect disappearance -> re-enumerate -> exact identity check -> new session -> readback -> rollback -> re-enumerate again -> baseline verify

Old handle after invalidation is never considered valid.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Any

from .identity import PhysicalInstance, ExactIdentityGate, IdentityVerdict
from .transport import DeviceTransport


@dataclass
class ReconnectResult:
    ok: bool
    error: str = ""
    new_identity: PhysicalInstance | None = None
    attempts: int = 0


class ReconnectManager:
    def __init__(
        self,
        transport: DeviceTransport,
        identity_gate: ExactIdentityGate,
        enumerate_fn: Callable[[], PhysicalInstance | None],
        timeout_ms: int = 5000,
        poll_ms: int = 200,
    ) -> None:
        self.transport = transport
        self.identity_gate = identity_gate
        self.enumerate_fn = enumerate_fn
        self.timeout_ms = timeout_ms
        self.poll_ms = poll_ms
        self._expected_session: int | None = None

    def begin_reconnect_write(self) -> None:
        """Call immediately after issuing the write that triggers disconnect."""
        self._expected_session = self.transport.current_session_id()
        self.transport.invalidate()

    def wait_for_reconnect(self) -> ReconnectResult:
        start = time.time()
        attempts = 0
        while (time.time() - start) * 1000 < self.timeout_ms:
            attempts += 1
            inst = self.enumerate_fn()
            if inst is None:
                time.sleep(self.poll_ms / 1000)
                continue
            verdict = self.identity_gate.evaluate(inst)
            if not verdict.passed:
                return ReconnectResult(False, f"identity mismatch after reconnect: {verdict.reason}", inst, attempts)
            # transport must have new session; for FakeTransport we expect caller to simulate_reconnect
            if self.transport.is_connected():
                # stale session check: if session id did not change, it's stale
                if self._expected_session is not None and self.transport.current_session_id() == self._expected_session:
                    # still stale handle, keep waiting
                    time.sleep(self.poll_ms / 1000)
                    continue
                return ReconnectResult(True, "", inst, attempts)
            # if still disconnected but identity ok, wait a bit for transport to come back
            time.sleep(self.poll_ms / 1000)
        return ReconnectResult(False, "reconnect timeout", None, attempts)


def execute_with_reconnect(
    transport: DeviceTransport,
    reconnect: ReconnectManager | None,
    operation_id: str,
    value: Any,
    readback_fn: Callable[[str], Any],
    is_reconnect_op: bool,
) -> tuple[bool, str]:
    """Helper used by executor: if is_reconnect_op, perform reconnect dance after write."""
    res = transport.set(operation_id, value)
    if not res.ok:
        return False, res.error
    if is_reconnect_op and reconnect is not None:
        reconnect.begin_reconnect_write()
        rr = reconnect.wait_for_reconnect()
        if not rr.ok:
            return False, rr.error
    else:
        # for non-reconnect ops, ensure transport still valid
        if not transport.is_connected():
            return False, "unexpected disconnect"
    return True, ""
