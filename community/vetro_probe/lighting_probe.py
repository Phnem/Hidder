"""Passive lighting protocol probe for AULA HERO 84 HE.

READ-ONLY ONLY — never writes to the device. All state changes come from the
official AULA vendor app. The probe reads state before/after each user action
and records a differential evidence log (lighting_sweep.jsonl).

Purpose: prove the real semantic mapping of the lighting protocol
(groups 0x06/0x86 sync/fetch_custom_light, registers 0x01/0x06), and REJECT the
old light.rgb_core=register-0x01 mapping if evidence contradicts it.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "DB"))

from community.vetro_probe.hid_raw import HidRawTransport
from community.vetro_probe.firmware_identity import read_firmware_via_raw, HERO84_FIRMWARE_BRANCH
from community.vetro_probe.runstate import RunStateStore, RunCheckpoint
import aula_kb_v3.operations as ops  # type: ignore


# ---------------------------------------------------------------------------
# Guarded K14 lighting rollback test (brightness-only full 7-byte RMW)
# ---------------------------------------------------------------------------

LIGHT_MODE_REG = 0x01
BRIGHTNESS_OFFSET = 5  # in [mode, reserved, R, G, B, brightness, speed]
BRIGHTNESS_MIN, BRIGHTNESS_MAX = 0, 20


def plan_brightness_temporary(A: bytes) -> tuple[bytes, list[int]]:
    """Full 7-byte state with ONLY brightness changed (25% if not already 5, else 50%)."""
    if len(A) != 7:
        raise ValueError(f"light_mode register must be 7 bytes, got {len(A)}")
    B = bytearray(A)
    new = 5 if A[BRIGHTNESS_OFFSET] != 5 else 10
    B[BRIGHTNESS_OFFSET] = new
    return bytes(B), ([BRIGHTNESS_OFFSET] if new != A[BRIGHTNESS_OFFSET] else [])


def is_valid_baseline(A: bytes) -> bool:
    return len(A) == 7 and BRIGHTNESS_MIN <= A[BRIGHTNESS_OFFSET] <= BRIGHTNESS_MAX


def _canonical_set_frame(reg7: bytes) -> bytes:
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
    expected = _canonical_set_frame(written)
    out["expected_frame"] = expected.hex()
    if len(b) != 63:
        return out
    out["length_valid"] = True
    import aula_kb_v3.protocol as prot  # type: ignore
    out["checksum_valid"] = prot.checksum(b[:62]) == b[62]
    out["report_header_valid"] = (b[0] == expected[0] and b[1] == expected[1]
                                  and b[2:6] == expected[2:6])
    out["state_match"] = b[6:6 + 7] == bytes(written)
    out["decoded_state"] = b[6:6 + 7].hex()
    out["frame_valid"] = out["length_valid"] and out["checksum_valid"] and out["report_header_valid"]
    out["ack"] = b == expected  # full canonical frame equality (header+state+checksum)
    return out


def verify_echo(written: bytes, echo: bytes | None) -> bool:
    """Echo is the ACK. PASS only when the received frame equals the canonical
    serialized frame for `written` (header + state payload + valid checksum).
    A bare 7-byte register state is never a valid echo of itself."""
    return decode_echo(written, echo)["ack"]


def run_rollback_flow(make_session, A: bytes, B: bytes, echo_fn=None, delay_s: float = 2.5,
                      on_phase=None) -> dict:
    """A -> temp write (echo ACK) -> fresh GET B == B -> rollback write A (echo) ->
    fresh final GET A == A. Write-ahead: on_phase('TEMP_WRITE_INTENT') fires BEFORE the
    first physical SET B, on_phase('RESTORE_INTENT') BEFORE rollback SET A.
    Returns {ok, stages, recovered}."""
    stages: list[dict] = []
    res: dict = {"ok": False, "stages": stages, "recovered": False}

    if on_phase:
        on_phase("BASELINE_SAVED")

    # B: temporary write session (echo ACK). Write-ahead intent persisted BEFORE SET B.
    if on_phase:
        on_phase("TEMP_WRITE_INTENT")
    s1 = make_session()
    echo1 = s1.set_light(B)
    s1.close()
    st = decode_echo(B, echo1)
    ack = st["ack"]
    stages.append({
        "stage": "write_B", "written": B.hex(),
        "echo": (normalize_echo(echo1) or b"").hex(),
        "set_B_issued": True, "set_B_completed": echo1 is not None,
        "echo_B_observed": st["echo_observed"],
        "echo_B_frame_valid": st["frame_valid"],
        "echo_B_checksum_valid": st["checksum_valid"],
        "echo_B_state_match": st["state_match"],
        "ack": ack, "expected": st["expected_frame"], "decoded_state": st["decoded_state"],
    })
    if on_phase:
        on_phase("TEMP_WRITE_APPLIED")
    if not ack:
        res["error_code"] = "ECHO_B_MISMATCH"
        res["error"] = "echo ACK for temporary write missing/mismatch"
        return res

    # fresh GET B (readback, NOT echo)
    try:
        s2 = make_session()
        rb = s2.get_light()
        s2.close()
    except Exception as exc:
        res["error_code"] = "GET_B_FAILED"
        res["error"] = f"fresh GET B failed: {exc!r}"
        if on_phase:
            on_phase("RESTORE_INTENT")
        rec = _rollback(A, make_session)
        stages.append(rec)
        res["recovered"] = rec["ok"]
        if on_phase:
            on_phase("RESTORED" if rec["ok"] else "TEMP_WRITE_APPLIED")
        return res
    stages.append({"stage": "readback_B", "got": (rb or b"").hex(), "expected": B.hex(),
                   "match": rb == B})
    if rb != B:
        res["error_code"] = "READBACK_B_MISMATCH"
        res["error"] = f"fresh GET != temporary state: {bytes(rb or b'').hex()} != {B.hex()}"
        # immediate recovery from immutable baseline A (write-ahead intent before SET A)
        if on_phase:
            on_phase("RESTORE_INTENT")
        rec = _rollback(A, make_session)
        stages.append(rec)
        res["recovered"] = rec["ok"]
        res["recovery_code"] = rec.get("error_code", "")
        if on_phase:
            on_phase("RESTORED" if rec["ok"] else "TEMP_WRITE_APPLIED")
        return res

    # observable pause
    time.sleep(delay_s)

    # rollback: SET original A (immutable baseline); write-ahead intent before SET A
    if on_phase:
        on_phase("RESTORE_INTENT")
    rec = _rollback(A, make_session)
    stages.append(rec)
    res["recovered"] = rec["ok"]
    # Hard-pass invariant: final GET must be a real observed value equal to A.
    # Echo/visual/transport-completion alone can never substitute.
    res["ok"] = _final_verified(rec) and rb == B
    if on_phase:
        on_phase("RESTORED" if res["ok"] else "RESTORE_INTENT")
    return res


UNCERTAIN_STATES = ("TEMP_WRITE_INTENT", "TEMP_WRITE_APPLIED", "RESTORE_INTENT")


def recovery_action(cp) -> str:
    """Write-ahead recovery policy: only states where a write MAY have reached hardware
    require restoring the immutable baseline A. BASELINE_SAVED/RESTORED need none."""
    if cp is None or getattr(cp, "operation", "") != "lighting.rollback_validation":
        return "NONE"
    if cp.phase in UNCERTAIN_STATES:
        return "RESTORE_A"
    return "NONE"


def recover_pending_checkpoint(cp, make_session) -> dict:
    action = recovery_action(cp)
    if action == "NONE":
        return {"ok": True, "writes": 0, "action": "NONE"}
    try:
        A = bytes.fromhex(cp.baseline)
    except Exception as exc:
        return {"ok": False, "writes": 0, "action": "RESTORE_A",
                "error_code": "CHECKPOINT_INVALID", "error": f"baseline parse: {exc!r}"}
    rec = _rollback(A, make_session)
    out = {"ok": rec["ok"], "writes": 1 if rec["ok"] else 0, "action": "RESTORE_A", "result": rec}
    if not rec["ok"]:
        out["error_code"] = rec.get("error_code", "RECOVERY_FAILED")
        out["error"] = rec.get("error", "")
        # separate 'write may have reached hardware' from 'verified'
        out["restore_write_may_have_succeeded"] = rec.get("restore_write_issued", False)
        out["restore_verified"] = rec.get("fresh_get_equals_A", False)
    return out


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
    out["fresh_get_observed"] = fa is not None and len(fa) == 7
    out["fresh_get"] = fa.hex() if fa is not None and len(fa) == 7 else None
    out["final_get"] = out["fresh_get"]
    try:
        s2.close()
    except Exception:
        pass
    if not out["fresh_get_observed"]:
        out["error_code"] = "GET_A_FAILED"
        out["error"] = (f"final GET missing/malformed: "
                        f"{None if fa is None else f'{len(fa)} bytes (expected 7)'}")
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


def _real_session_factory() -> callable:
    def make():
        raw = HidRawTransport(path=None)
        p = ops.connect(raw)  # type: ignore

        def get_light() -> bytes:
            return bytes(ops.get_feature_register(raw, p, LIGHT_MODE_REG))  # type: ignore

        def set_light(reg7: bytes) -> bytes:
            if len(reg7) != 7:
                raise ValueError("set_light requires exactly 7 bytes")
            import aula_kb_v3.protocol as prot  # type: ignore
            frame = bytes(prot.build_feature_set_frame(LIGHT_MODE_REG, reg7))  # type: ignore
            raw.send(frame)
            return bytes(raw.recv(timeout_ms=1000))  # echo

        class _S:
            def __init__(self):
                self.raw = raw
                self.product = p

            def get_light(self):
                return get_light()

            def set_light(self, reg7):
                return set_light(reg7)

            def close(self):
                raw.close()

        return _S()

    return make


class LightingProbe:
    def __init__(self) -> None:
        self.raw = HidRawTransport(path=None)
        self.product = ops.connect(self.raw)  # type: ignore
        self.fw = read_firmware_via_raw(self.raw)

    def read_register(self, reg: int) -> bytes:
        time.sleep(0.05)
        return bytes(ops.get_feature_register(self.raw, self.product, reg))  # type: ignore

    def read_group(self, group: int, sub: int = 0, data: bytes = b"") -> bytes | None:
        """Send a GET on an arbitrary group and return the reply payload (read-only)."""
        try:
            import aula_kb_v3.protocol as prot  # type: ignore
        except ImportError:
            import DB.aula_kb_v3.protocol as prot  # type: ignore
        frame = bytes(prot.build_frame(group, sub=sub, data=data))
        self.raw.send(frame)
        reply = self.raw.recv(timeout_ms=1000)
        return bytes(reply)

    def snapshot(self) -> dict:
        state = {
            "fw": self.fw,
            "identity": {"uuid": self.product.uuid, "name": self.product.display_name,
                         "vid": self.product.vendor_id, "pid": self.product.product_id,
                         "family": self.product.protocol_family},
        }
        for reg in (0x01, 0x06):
            try:
                b = self.read_register(reg)
                state[f"reg_{reg:02x}"] = {"hex": b.hex(), "bytes": list(b)}
            except Exception as e:
                state[f"reg_{reg:02x}"] = {"error": str(e)}
        for sub in (0,):
            try:
                b = self.read_group(0x86, sub=sub)
                state[f"group_86_sub_{sub}"] = {"hex": b.hex(), "bytes": list(b)} if b else None
            except Exception as e:
                state[f"group_86_sub_{sub}"] = {"error": str(e)}
        return state

    def close(self) -> None:
        self.raw.close()


def run_sweep(log_path: Path) -> None:
    """Interactive differential sweep: user changes ONE parameter in the official app,
    the probe records state before/after. Appends to lighting_sweep.jsonl."""
    probe = LightingProbe()
    steps = [
        ("idle_baseline", "leave settings unchanged (idle)"),
        ("on_to_off", "LIGHTING ON -> OFF (enable toggle)"),
        ("off_to_on", "LIGHTING OFF -> ON"),
        ("brightness_low", "BRIGHTNESS -> LOW (keep effect/color)"),
        ("brightness_med", "BRIGHTNESS -> MEDIUM"),
        ("brightness_high", "BRIGHTNESS -> HIGH"),
        ("color_red", "STATIC single color -> RED"),
        ("color_green", "STATIC single color -> GREEN"),
        ("color_blue", "STATIC single color -> BLUE"),
        ("effect_static", "EFFECT -> STATIC"),
        ("effect_breathing", "EFFECT -> BREATHING"),
        ("effect_animated", "EFFECT -> one animated mode"),
        ("speed_direction", "SPEED/DIRECTION change (if UI exposes it)"),
    ]
    print(f"firmware {probe.fw} identity {probe.product.display_name}")
    print("READ-ONLY sweep. Change ONLY the listed parameter in the AULA app, then press Enter.\n")
    for label, instruction in steps:
        before = probe.snapshot()
        print(f"\n>>> [{label}] {instruction}")
        input("Press Enter AFTER the change is applied (or 'skip' to skip): ")
        if "skip" in input and False:
            continue
        time.sleep(0.4)
        after = probe.snapshot()
        rec = {"step": label, "instruction": instruction, "before": before, "after": after}
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"[recorded] {label}")
    probe.close()
    print(f"\nsweep log: {log_path}")


def _recovery_required(out_dir: Path) -> RunCheckpoint | None:
    store = RunStateStore(out_dir)
    cp = store.load()
    if cp and cp.operation == "lighting.rollback_validation" and cp.phase in UNCERTAIN_STATES and not cp.closed:
        return cp
    return None


def _checkpoint_matches_device(cp: RunCheckpoint, inst) -> bool:
    dev = cp.device or {}
    return (dev.get("vid", "").lower() == inst.vid.lower()
            and dev.get("pid", "").lower() == inst.pid.lower()
            and dev.get("firmware", "") == inst.firmware_version
            and (dev.get("family", "") in ("", inst.family if hasattr(inst, "family") else "aula_kb_v3_wired")
                 or dev.get("family", "") == "aula_kb_v3_wired"))


def run_rollback_brightness(out_dir: Path, require_real: bool) -> int:
    """Guarded K14 lighting rollback test (brightness-only full 7-byte RMW), write-ahead crash-safe."""
    if not require_real:
        print("ZERO WRITES: --rollback-test brightness requires the explicit --real write flag.")
        return 2
    store = RunStateStore(out_dir)

    # fresh session + gates (needed for recovery gate too)
    from community.vetro_probe.identity import ExactIdentityGate
    from community.vetro_probe.bundle import production_bundle_for_hero84
    from community.vetro_probe.identity import discover_real_instance_via_raw

    s0 = _real_session_factory()()
    raw = s0.raw
    p = s0.product
    gate = ExactIdentityGate(production_bundle_for_hero84())
    inst = discover_real_instance_via_raw(raw)
    verdict = gate.evaluate(inst)
    if not verdict.passed:
        s0.close()
        print(f"ZERO WRITES: identity gate failed: {verdict.reason}", file=sys.stderr)
        return 2
    if inst.firmware_version != HERO84_FIRMWARE_BRANCH:
        s0.close()
        print(f"ZERO WRITES: firmware {inst.firmware_version} != {HERO84_FIRMWARE_BRANCH}", file=sys.stderr)
        return 2

    # recovery-first (write-ahead): any UNCERTAIN state restores immutable A; bypasses "I UNDERSTAND"
    # but never bypasses --real or identity/FW gates.
    pending = _recovery_required(out_dir)
    if pending:
        if not _checkpoint_matches_device(pending, inst):
            s0.close()
            print(f"ZERO WRITES: pending checkpoint device/FW does not match connected device.", file=sys.stderr)
            return 2
        print(f"RECOVERY REQUIRED: state {pending.phase} (baseline {pending.baseline}). Restoring A...")
        rec = recover_pending_checkpoint(pending, _real_session_factory())
        if rec["ok"]:
            pending.phase = "RESTORED"
            pending.closed = True
            pending.error = ""
            store.save(pending)
            s0.close()
            print("RECOVERY COMPLETE: original lighting state restored. Rerun the test.")
            return 0
        pending.error = f"code={rec.get('error_code')} detail={rec.get('error')} restore_write_issued={rec.get('restore_write_may_have_succeeded')}"
        store.save(pending)
        s0.close()
        print(f"RECOVERY FAILED — manual restore required.\n  reason: {rec.get('error_code')}\n  detail: {rec.get('error')}\n  restore_write_may_have_succeeded: {rec.get('restore_write_may_have_succeeded')}\n  restore_verified: {rec.get('restore_verified')}", file=sys.stderr)
        return 2

    A = s0.get_light()
    if not is_valid_baseline(A):
        s0.close()
        print(f"ZERO WRITES: invalid baseline {bytes(A or b'').hex()} (len/range)", file=sys.stderr)
        return 2
    B, changed = plan_brightness_temporary(A)

    print("\nREAL LIGHTING ROLLBACK TEST")
    print(f"device = {p.display_name}")
    print(f"firmware = {inst.firmware_version}")
    print(f"baseline = {list(A)}")
    print(f"temporary = {list(B)}")
    print(f"field changed = brightness only ({changed})")
    print("\nType EXACTLY: I UNDERSTAND")
    confirm = input("> ").strip()
    if confirm != "I UNDERSTAND":
        s0.close()
        print("ZERO WRITES: confirmation not provided.", file=sys.stderr)
        return 2

    # checkpoint before first write
    cp = store.new_run()
    cp.operation = "lighting.rollback_validation"
    cp.device = {"vid": inst.vid, "pid": inst.pid, "family": p.protocol_family, "firmware": inst.firmware_version}
    cp.baseline = A.hex()
    cp.attempted = B.hex()
    cp.phase = "BASELINE_SAVED"
    store.save(cp)
    s0.close()

    def on_phase(state: str) -> None:
        cp.phase = state
        if state in ("RESTORED",):
            cp.closed = True
        store.save(cp)  # durable: temp -> flush -> fsync -> replace

    try:
        result = run_rollback_flow(_real_session_factory(), A, B, delay_s=2.5, on_phase=on_phase)
    except Exception as exc:
        # Do NOT swallow: record the exact phase + exception so recovery is actionable.
        cp.error = f"EXCEPTION during phase={cp.phase}: {exc!r}"
        store.save(cp)
        print(f"EXCEPTION during {cp.phase}: {exc!r}", file=sys.stderr)
        return 2
    print(f"BASELINE_GET = {list(A)}")
    for st in result["stages"]:
        name = st.get("stage")
        if name == "write_B":
            print("TEMP_WRITE_INTENT = DURABLE")
            print(f"  SET_B_ISSUED = {'YES' if st.get('set_B_issued') else 'NO'}"
                  f"   SET_B_COMPLETED = {'YES' if st.get('set_B_completed') else 'NO'}"
                  f"   ECHO_B_OBSERVED = {'YES' if st.get('echo_B_observed') else 'NO'}"
                  f"   ECHO_B_FRAME_VALID = {'YES' if st.get('echo_B_frame_valid') else 'NO'}"
                  f"   ECHO_B_STATE_MATCH = {'YES' if st.get('echo_B_state_match') else 'NO'}"
                  f"   written={st.get('written')}   decoded_state={st.get('decoded_state')}"
                  f"   expected_frame={st.get('expected')}   echo={st.get('echo')}")
        elif name == "readback_B":
            print(f"  GET_B = {st.get('got')}   GET_B_MATCH = {'PASS' if st.get('match') else 'FAIL'}")
            if st.get("match"):
                print("Keyboard brightness should now be temporarily different.")
        elif name == "rollback_A":
            print("RESTORE_INTENT = DURABLE")
            final_ok = st.get("final_get_equals_A") and st.get("final_get") is not None
            print(f"  SET_A_ISSUED = {'YES' if st.get('set_A_issued') else 'NO'}"
                  f"   SET_A_COMPLETED = {'YES' if st.get('set_A_completed') else 'NO'}"
                  f"   ECHO_A_OBSERVED = {'YES' if st.get('echo_observed') else 'NO'}"
                  f"   ECHO_A = {'PASS' if st.get('echo_ack') else 'FAIL'}"
                  f"   ECHO_A_FRAME_VALID = {'YES' if st.get('echo_frame_valid') else 'NO'}"
                  f"   ECHO_A_STATE_MATCH = {'YES' if st.get('echo_state_match') else 'NO'}"
                  f"   FINAL_GET_A = {st.get('final_get')}   FINAL_A_MATCH = {'PASS' if final_ok else 'FAIL'}"
                  f"   expected_frame={st.get('expected_frame')}"
                  f"   restore_write_issued = {st.get('restore_write_issued')}"
                  f"   error_code = {st.get('error_code', '')}   detail = {st.get('error', '')}")
        else:
            print(f"  [{name}] {st}")
    print(f"K14 ROLLBACK = {'PASS' if result.get('ok') else 'FAIL'}")

    if result["ok"]:
        cp.phase = "RESTORED"
        cp.closed = True
        cp.error = ""
        store.save(cp)
        print("\nOriginal brightness should now be restored.")
        print("K14 ROLLBACK (brightness) = PASS (physical)")
        return 0
    cp.phase = "RESTORE_INTENT" if result.get("recovered") else "TEMP_WRITE_APPLIED"
    cp.closed = bool(result.get("recovered"))
    cp.error = f"code={result.get('error_code', 'RECOVERY_FAILED')} detail={result.get('error', '')}"
    store.save(cp)
    print(f"\nK14 ROLLBACK = {'RECOVERED' if result.get('recovered') else 'FAILED — manual restore required'}"
          f"\n  reason: {result.get('error_code')}\n  detail: {result.get('error')}")
    return 0 if result.get("recovered") else 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vetro.lighting_probe", description="Lighting protocol probe (read-only unless --rollback-test + --real)")
    parser.add_argument("--snapshot", action="store_true", help="Dump current full lighting state once (read-only)")
    parser.add_argument("--sweep", type=Path, default=None, help="Run interactive differential sweep, log to this file")
    parser.add_argument("--rollback-test", type=str, default=None, help="Guarded reversible test: 'brightness' (full 7-byte RMW)")
    parser.add_argument("--real", action="store_true", help="Explicit real-write permission (required for --rollback-test)")
    parser.add_argument("--out", type=Path, default=Path("lighting_rollback_checkpoint"), help="Checkpoint dir for rollback test")
    args = parser.parse_args(argv)

    if args.snapshot:
        probe = LightingProbe()
        print(json.dumps(probe.snapshot(), indent=2))
        probe.close()
        return 0
    if args.sweep:
        run_sweep(Path(args.sweep))
        return 0
    if args.rollback_test:
        if args.rollback_test != "brightness":
            print(f"unknown rollback test: {args.rollback_test}", file=sys.stderr)
            return 2
        return run_rollback_brightness(args.out, require_real=args.real)
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
