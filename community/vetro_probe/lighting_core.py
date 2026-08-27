"""Shared lower-level primitives for the global-lighting (register 0x01) path.

Single source of truth consumed BOTH by the diagnostic K14 rollback probe
(lighting_probe.py) and the normal Probe executor (aula_transport.py). Keeps the
proven canonical-echo semantics in exactly one place so the two call paths can
never drift.

Scope discipline: these primitives are for the GLOBAL lighting register 0x01
(full 7-byte state [mode, reserved, R, G, B, brightness, speed]) on
aula_kb_v3_wired. They prove light.brightness (full-state brightness-only RMW).
They do NOT authorize per-key/custom lighting, effect enums, or other firmware.

Echo semantics (physically validated, K14 runs #2/#3):
  - SET builds the canonical 63-byte frame via protocol.build_feature_set_frame.
  - The device echoes that exact canonical frame (header + state + TX checksum).
  - Echo is an ACK. It is NEVER a readback: only a fresh GET is.
"""

from __future__ import annotations

LIGHT_MODE_REG = 0x01
BRIGHTNESS_OFFSET = 5  # in [mode, reserved, R, G, B, brightness, speed]
BRIGHTNESS_MIN, BRIGHTNESS_MAX = 0, 20
REGISTER_WIDTH = 7


def plan_brightness_temporary(A: bytes) -> tuple[bytes, list[int]]:
    """Full 7-byte state with ONLY brightness changed (25% if not already 5, else 50%).

    Preserves mode/reserved/R/G/B/speed byte-for-byte from A. Never uses 0 for the
    automated research experiment. Returns (B, changed_offsets)."""
    if len(A) != REGISTER_WIDTH:
        raise ValueError(f"light_mode register must be {REGISTER_WIDTH} bytes, got {len(A)}")
    B = bytearray(A)
    new = 5 if A[BRIGHTNESS_OFFSET] != 5 else 10
    B[BRIGHTNESS_OFFSET] = new
    return bytes(B), ([BRIGHTNESS_OFFSET] if new != A[BRIGHTNESS_OFFSET] else [])


def is_valid_baseline(A: bytes) -> bool:
    return len(A) == REGISTER_WIDTH and BRIGHTNESS_MIN <= A[BRIGHTNESS_OFFSET] <= BRIGHTNESS_MAX


def canonical_set_frame(reg7: bytes) -> bytes:
    """The canonical 63-byte serialized frame for a register-0x01 SET —
    the single source of truth for echo verification (header + state + checksum)."""
    import aula_kb_v3.protocol as prot  # type: ignore
    return bytes(prot.build_feature_set_frame(LIGHT_MODE_REG, reg7))


def normalize_echo(echo: bytes | None) -> bytes | None:
    """Strip the optional HID report-id prefix (64B -> 63B payload form)."""
    if echo is None:
        return None
    b = bytes(echo)
    if len(b) == 64 and b[0] == 9:
        b = b[1:]
    return b


def decode_echo(written: bytes, echo: bytes | None) -> dict:
    """Canonical ACK verification for a register-0x01 SET.

    The device echoes the exact canonical serialized frame
    ([0x04, reg, 0x00, 0x01, 0x00, len, state..., checksum]); NEVER compare the
    7-byte register state directly against the 63-byte echo. Validates the whole
    frame: report family/header, state payload AND checksum. A frame is only an
    ACK when it equals the canonical expected frame byte-for-byte.
    """
    out = {"echo_observed": echo is not None, "frame_valid": False, "length_valid": False,
           "report_header_valid": False, "state_match": False, "checksum_valid": False,
           "ack": False, "decoded_state": None, "expected_frame": None, "echo_frame": None}
    b = normalize_echo(echo)
    if b is None:
        return out
    out["echo_frame"] = b.hex()
    expected = canonical_set_frame(written)
    out["expected_frame"] = expected.hex()
    if len(b) != 63:
        return out
    out["length_valid"] = True
    import aula_kb_v3.protocol as prot  # type: ignore
    out["checksum_valid"] = prot.checksum(b[:62]) == b[62]
    out["report_header_valid"] = (b[0] == expected[0] and b[1] == expected[1]
                                  and b[2:6] == expected[2:6])
    out["state_match"] = b[6:6 + REGISTER_WIDTH] == bytes(written)
    out["decoded_state"] = b[6:6 + REGISTER_WIDTH].hex()
    out["frame_valid"] = out["length_valid"] and out["checksum_valid"] and out["report_header_valid"]
    out["ack"] = b == expected  # full canonical frame equality (header+state+checksum)
    return out


def verify_echo(written: bytes, echo: bytes | None) -> bool:
    """Echo is the ACK. PASS only when the received frame equals the canonical
    serialized frame for `written` (header + state payload + valid checksum).
    A bare 7-byte register state is never a valid echo of itself."""
    return decode_echo(written, echo)["ack"]


def _final_verified(rec: dict) -> bool:
    """K14 final-restore hard invariant: a real, well-formed final GET must have
    been observed AND be byte-for-byte equal to immutable A. Echo, visual state
    and SET completion are NOT substitutes."""
    return bool(rec.get("fresh_get_observed")) and bool(rec.get("fresh_get_equals_A"))


def _rollback(A: bytes, make_session) -> dict:
    """Restore immutable baseline A with per-step diagnostics.

    Separates 'restore write may have succeeded' from 'restore verified':
    restore_write_issued / restore_write_completed / echo_observed / echo_ack /
    fresh_get_observed / fresh_get / fresh_get_equals_A (aliased as final_get /
    final_get_equals_A for the report). Never reconstructs A from B.
    Fail-closed on final GET: None or wrong length -> GET_A_FAILED; value differs
    from A -> FINAL_STATE_MISMATCH. Sets error_code among: OPEN_FAILED,
    SET_A_FAILED, ECHO_A_MISMATCH, GET_A_FAILED, FINAL_STATE_MISMATCH (or "").
    """
    out = {"stage": "rollback_A", "written": A.hex(), "expected": A.hex(),
           "restore_write_issued": False, "restore_write_completed": False,
           "set_A_issued": False, "set_A_completed": False,
           "echo_observed": False, "echo_ack": False,
           "echo_frame_valid": False, "echo_checksum_valid": False, "echo_state_match": False,
           "decoded_state": None, "expected_frame": None,
           "fresh_get_observed": False, "fresh_get": None, "fresh_get_equals_A": False,
           "final_get": None, "final_get_equals_A": False,
           "ok": False, "error_code": "", "error": ""}
    try:
        s = make_session()
    except Exception as exc:
        out["error_code"] = "OPEN_FAILED"
        out["error"] = f"open: {exc!r}"
        return out
    try:
        echo = s.set_light(A)
        out["restore_write_issued"] = True
        out["restore_write_completed"] = True
        out["set_A_issued"] = True
        out["set_A_completed"] = echo is not None
    except Exception as exc:
        out["error_code"] = "SET_A_FAILED"
        out["error"] = f"set_light: {exc!r}"
        try:
            s.close()
        except Exception:
            pass
        return out
    st = decode_echo(A, echo)
    out["echo_observed"] = st["echo_observed"]
    out["echo_ack"] = st["ack"]
    out["echo_frame_valid"] = st["frame_valid"]
    out["echo_checksum_valid"] = st["checksum_valid"]
    out["echo_state_match"] = st["state_match"]
    out["decoded_state"] = st["decoded_state"]
    out["expected_frame"] = st["expected_frame"]
    out["echo"] = st["echo_frame"] or ""
    try:
        s.close()
    except Exception:
        pass
    try:
        s2 = make_session()
    except Exception as exc:
        out["error_code"] = "OPEN_FAILED"
        out["error"] = f"reopen for GET: {exc!r}"
        return out
    try:
        fa = s2.get_light()
    except Exception as exc:
        out["error_code"] = "GET_A_FAILED"
        out["error"] = f"get_light: {exc!r}"
        try:
            s2.close()
        except Exception:
            pass
        return out
    out["fresh_get_observed"] = fa is not None and len(fa) == REGISTER_WIDTH
    out["fresh_get"] = fa.hex() if fa is not None and len(fa) == REGISTER_WIDTH else None
    out["final_get"] = out["fresh_get"]
    try:
        s2.close()
    except Exception:
        pass
    if not out["fresh_get_observed"]:
        out["error_code"] = "GET_A_FAILED"
        out["error"] = (f"final GET missing/malformed: "
                        f"{None if fa is None else f'{len(fa)} bytes (expected {REGISTER_WIDTH})'}")
        out["fresh_get_equals_A"] = False
        out["final_get_equals_A"] = False
        out["ok"] = False
        return out
    out["fresh_get_equals_A"] = fa == A
    out["final_get_equals_A"] = fa == A
    out["ok"] = out["echo_ack"] and _final_verified(out)
    if not out["ok"]:
        out["error_code"] = "ECHO_A_MISMATCH" if not out["echo_ack"] else "FINAL_STATE_MISMATCH"
    return out


# ---------------------------------------------------------------------------
# Low-level transport primitives (used by aula_transport for light.brightness)
# ---------------------------------------------------------------------------


def read_light_state(raw, product) -> bytes:
    """Read the full 7-byte register-0x01 current state (fresh GET)."""
    import aula_kb_v3.operations as ops  # type: ignore
    data = ops.get_feature_register(raw, product, LIGHT_MODE_REG)  # type: ignore
    if data is None or len(data) != REGISTER_WIDTH:
        raise RuntimeError(f"light register 0x01 GET must be {REGISTER_WIDTH} bytes, got {None if data is None else len(data)}")
    return bytes(data)


def build_light_state_frame(reg7: bytes) -> bytes:
    """Canonical 63-byte SET frame for the full register-0x01 state."""
    if len(reg7) != REGISTER_WIDTH:
        raise ValueError(f"set_light requires exactly {REGISTER_WIDTH} bytes")
    return canonical_set_frame(reg7)


def set_light_state_with_echo(raw, reg7: bytes) -> bytes:
    """Send the canonical register-0x01 SET and return the device echo frame."""
    frame = build_light_state_frame(reg7)
    raw.send(frame)
    return bytes(raw.recv(timeout_ms=1000))
