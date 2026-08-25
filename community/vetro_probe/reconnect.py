"""Re-enumerating transaction state machine.

For operations where bundle marks requires_reconnect=True:
write -> invalidate immediately -> detect disappearance -> re-enumerate -> exact identity check -> new session -> readback -> rollback -> re-enumerate again -> baseline verify

Old handle after invalidation is never considered valid.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

from .identity import PhysicalInstance, ExactIdentityGate, IdentityVerdict
from .transport import DeviceTransport

# Sentinel returned by enumerate_fn to model "multiple ambiguous candidates".
AMBIGUOUS = "AMBIGUOUS"


@dataclass
class ReconnectResult:
    ok: bool
    error: str = ""
    new_identity: PhysicalInstance | None = None
    attempts: int = 0
    session: DeviceTransport | None = None  # fresh session handle (B/C/D)


class ReconnectManager:
    def __init__(
        self,
        transport: DeviceTransport,
        identity_gate: ExactIdentityGate,
        enumerate_fn: Callable[[], Any],
        timeout_ms: int = 5000,
        poll_ms: int = 200,
        firmware_check: Callable[[PhysicalInstance], tuple[bool, str]] | None = None,
    ) -> None:
        self.transport = transport
        self.identity_gate = identity_gate
        self.enumerate_fn = enumerate_fn
        self.timeout_ms = timeout_ms
        self.poll_ms = poll_ms
        self.firmware_check = firmware_check
        self._current_transport: DeviceTransport = transport
        self._expected_session: int | None = None

    def begin_reconnect_write(self) -> None:
        """Call immediately after issuing the write that triggers disconnect."""
        self._expected_session = self._current_transport.current_session_id()
        self._current_transport.invalidate()

    def acquire_fresh(self) -> ReconnectResult:
        """Wait for re-enumeration, verify exact identity + firmware, then return a FRESH session.

        The previous session (even one that gave a bad readback) is NEVER reused.
        Returns ReconnectResult(session=<new DeviceTransport>) on success.
        """
        start = time.time()
        attempts = 0
        last_error = ""
        while (time.time() - start) * 1000 < self.timeout_ms:
            attempts += 1
            inst = self.enumerate_fn()
            if inst == AMBIGUOUS:
                return ReconnectResult(False, "ambiguous reacquire: multiple candidate identities", None, attempts, None)
            if inst is None:
                time.sleep(self.poll_ms / 1000)
                continue
            verdict = self.identity_gate.evaluate(inst)
            if not verdict.passed:
                return ReconnectResult(False, f"identity mismatch after reconnect: {verdict.reason}", inst, attempts, None)
            if self.firmware_check is not None:
                fw_ok, fw_reason = self.firmware_check(inst)
                if not fw_ok:
                    return ReconnectResult(False, f"firmware check failed: {fw_reason}", inst, attempts, None)
            # identity + firmware OK -> acquire a brand-new session from the CURRENT session base.
            # A transient open/enumerate failure during USB re-enumeration is NOT a hard stop:
            # keep retrying within the timeout window, like the physical device re-appears.
            try:
                base = self._current_transport
                new_transport = base.fresh_session()
            except NotImplementedError:
                return ReconnectResult(False, "transport does not support fresh_session", inst, attempts, None)
            except Exception as exc:
                last_error = f"reacquire failed: {exc}"
                time.sleep(self.poll_ms / 1000)
                continue
            self._current_transport = new_transport
            if new_transport is not None and new_transport.is_connected():
                return ReconnectResult(True, "", inst, attempts, new_transport)
            time.sleep(self.poll_ms / 1000)
        detail = f" ({last_error})" if last_error else ""
        return ReconnectResult(False, f"reconnect timeout{detail}", None, attempts, None)

    def wait_for_reconnect(self) -> ReconnectResult:
        # Backward-compat shim: delegate to fresh acquisition.
        return self.acquire_fresh()


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
