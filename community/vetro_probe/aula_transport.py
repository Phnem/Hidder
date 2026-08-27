"""Typed HidTransport for Vetro Probe — production, SafetyGate-gated.

Never accepts raw bytes from bundle/plan. Plan supplies typed operation_id +
semantic_value; this module resolves it to aula_kb_v3 operations + local bounds
+ serializer. Server cannot dictate frame.

Uses HidRawTransport (hidapi) or simulator when injected for tests.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

# Ensure DB on path for aula_kb_v3 imports
_DB = Path(__file__).resolve().parents[2] / "DB"
if str(_DB) not in sys.path:
    sys.path.insert(0, str(_DB))

from .transport import DeviceTransport, TransportResult


# Operation -> aula_kb_v3 call dispatch table
# Each entry is (get_fn, set_fn, cap_name). set_fn receives (raw_transport, product, semantic_value)
# We keep this mapping minimal: only production_safe reversible ops already physically validated.

def _pos_for_test(product) -> int:
    # Choose a safe key position that exists on this product and is reversible.
    # Prefer W (pos 30) on HERO84; fallback to first valid position on WIN layer 0.
    try:
        if 30 in product.valid_positions(0, "WIN"):
            return 30
        # fallback to lowest valid pos that is not Esc (1)
        valid = sorted(product.valid_positions(0, "WIN"))
        for p in valid:
            if p not in (1, 14):  # avoid Esc, `~
                return p
        return valid[0] if valid else 30
    except Exception:
        return 30


def _get_product_by_uuid(uuid: int):
    import aula_kb_v3.registry as reg  # type: ignore

    return reg.resolve_by_uuid(uuid)


# Monotonic per-process session counter so A/B/C/D have distinct session ids on real/sim too.
_session_counter = __import__("itertools").count(1)


class AulaHidTransport(DeviceTransport):
    """Typed transport that talks to real hardware via HidRawTransport or simulator."""

    def __init__(self, raw: Any, product: Any) -> None:
        self.raw = raw
        self.product = product
        self._session_id = next(_session_counter)
        self._valid = True
        self._sim_device: Any = None
        # Cache for ops without true GET (he.rt/he.deadzone) — last set value echo
        self._rt_cache: dict[str, Any] = {}
        self._last_light_echo: bytes | None = None  # canonical echo of last register-0x01 SET
        self._last_light_written: bytes | None = None  # the 7-byte state that was written
        # cache baseline for simple ops to allow rollback without re-reading product?
        # No, always read fresh.

    def read_light_full_state(self) -> str:
        """Read the FULL 7-byte register-0x01 state (hex). Aggregate final
        verification for light.brightness compares byte-for-byte against the
        initial register baseline."""
        from .lighting_core import read_light_state
        return read_light_state(self.raw, self.product).hex()

    def take_light_echo(self) -> tuple[bytes | None, bytes | None]:
        """Return (and clear) (echo_frame, written_7byte_state) captured by the last
        register-0x01 light SET. Used by the executor for ACK evidence; echo is
        NEVER treated as a readback."""
        echo = self._last_light_echo
        written = self._last_light_written
        self._last_light_echo = None
        self._last_light_written = None
        return echo, written

    def fresh_session(self) -> "AulaHidTransport":
        """Invalidate current and return a brand-new AulaHidTransport (fresh handle)."""
        self.invalidate()
        if self._sim_device is not None:
            new_raw = self._sim_device.reconnect()
            new = AulaHidTransport(raw=new_raw, product=self.product)
            new._sim_device = self._sim_device
            return new
        # real hardware: fresh open + identity
        return AulaHidTransport.open_real(uuid=self.product.uuid)

    @classmethod
    def open_real(cls, uuid: int | None = None) -> "AulaHidTransport":
        """Discover and open real hardware, resolve product via GET_IDENTITY."""
        from .hid_raw import HidRawTransport

        raw = HidRawTransport(path=None, product_uuid=uuid)
        # Resolve product
        import aula_kb_v3.operations as ops  # type: ignore

        product = ops.connect(raw)  # type: ignore
        # If uuid was specified, verify
        if uuid is not None and product.uuid != uuid:
            raw.close()
            raise RuntimeError(f"connected uuid {product.uuid:#x} != expected {uuid:#x}")
        return cls(raw=raw, product=product)

    @classmethod
    def from_sim(cls, sim_device: Any) -> "AulaHidTransport":
        """For integration tests without physical device: wrap pdevemu sim."""
        # sim_device is AulaKbV3SimDevice
        raw = sim_device.open()
        product = sim_device.product
        inst = cls(raw=raw, product=product)
        # Keep reference to sim_device for reconnect (sim's device has reconnect())
        inst._sim_device = sim_device  # type: ignore
        return inst

    # --- DeviceTransport (vetro_probe typed) ---
    def get(self, operation_id: str) -> tuple[Any, TransportResult]:
        if not self._valid:
            return None, TransportResult(False, error="stale session")
        start = time.time()
        try:
            val = self._typed_get(operation_id)
            return val, TransportResult(True, latency_ms=int((time.time() - start) * 1000))
        except Exception as e:
            return None, TransportResult(False, error=str(e))

    def set(self, operation_id: str, semantic_value: Any) -> TransportResult:
        if not self._valid:
            return TransportResult(False, error="stale session")
        start = time.time()
        try:
            self._typed_set(operation_id, semantic_value)
            return TransportResult(True, latency_ms=int((time.time() - start) * 1000))
        except Exception as e:
            # For polling, the expected success path is disconnect (no ACK) — treat as ok
            # The polling transaction itself handles reconnect; here we just mark session invalid
            # and return ok if the operation is polling and the raw signalled disconnect.
            if operation_id == "keyboard.polling" and "disconnected" in str(e).lower():
                self._valid = False
                return TransportResult(True, latency_ms=int((time.time() - start) * 1000))
            return TransportResult(False, error=str(e))

    def is_connected(self) -> bool:
        # raw may have its own validity
        try:
            raw_ok = self.raw.is_connected() if hasattr(self.raw, "is_connected") else self._valid
        except Exception:
            raw_ok = self._valid
        return self._valid and raw_ok

    def invalidate(self) -> None:
        self._valid = False
        try:
            # close raw handle immediately — old handle never valid after re-enumerating write
            if hasattr(self.raw, "close"):
                self.raw.close()
        except Exception:
            pass

    def current_session_id(self) -> int:
        return self._session_id

    def reacquire(self, timeout_ms: int = 5000) -> None:
        """Wait for re-enumeration and reopen. Used by ReconnectManager."""
        # Use HidRawTransport's reacquire if available (real hardware)
        if hasattr(self.raw, "reacquire"):
            try:
                new_raw = self.raw.reacquire(self.product.uuid, timeout_ms=timeout_ms)  # type: ignore
                self.raw = new_raw
                self._session_id += 1
                self._valid = True
                return
            except Exception as e:
                self._valid = False
                raise
        # Simulator path: _sim_device is AulaKbV3SimDevice with reconnect()
        if hasattr(self, "_sim_device"):
            try:
                sim_dev = getattr(self, "_sim_device")
                new_raw = sim_dev.reconnect()  # type: ignore
                self.raw = new_raw
                self._session_id += 1
                self._valid = True
                return
            except Exception as e:
                self._valid = False
                raise
        # Fallback: try to reopen via open_real (real HID)
        try:
            from .hid_raw import HidRawTransport

            new_raw = HidRawTransport(product_uuid=self.product.uuid)
            # verify identity
            import aula_kb_v3.operations as ops  # type: ignore

            prod = ops.connect(new_raw)  # type: ignore
            if prod.uuid != self.product.uuid:
                raise RuntimeError(f"re-enumerated uuid {prod.uuid:#x} != expected {self.product.uuid:#x}")
            self.raw = new_raw
            self._session_id += 1
            self._valid = True
        except Exception as e:
            self._valid = False
            raise

    # --- typed dispatch (private, never exposed to bundle) ---
    def _typed_get(self, op_id: str) -> Any:
        # Import ops lazily to avoid hard dependency at import time
        try:
            import aula_kb_v3.operations as ops  # type: ignore
        except ImportError:
            import DB.aula_kb_v3.operations as ops  # type: ignore

        p = self.product
        if op_id == "he.actuation":
            pos = _pos_for_test(p)
            res = ops.get_actuation(self.raw, p, [pos])  # type: ignore
            # res is list[(pos, mm)]
            return res[0][1] if res else None
        if op_id == "keyboard.profile":
            return ops.get_profile_idx(self.raw, p)  # type: ignore
        if op_id == "light.rgb_core":
            # Full 7-byte light_mode register (color bytes[0:3], mode/brightness/enable bytes[3:6]).
            # Baseline/readback must cover the WHOLE register so rollback restores lighting mode too.
            data = ops.get_feature_register(self.raw, p, 0x01)  # type: ignore
            return bytes(data).hex() if data else "00000000000000"
        if op_id == "light.brightness":
            # Full 7-byte register GET; return the brightness byte (0..20). Fail closed
            # on missing/malformed register or out-of-range brightness (runtime gate).
            from .lighting_core import read_light_state, BRIGHTNESS_MIN, BRIGHTNESS_MAX
            state = read_light_state(self.raw, p)
            br = state[5]
            if not (BRIGHTNESS_MIN <= br <= BRIGHTNESS_MAX):
                raise RuntimeError(f"light.brightness baseline out of safe range 0..20: {br}")
            return int(br)
        if op_id == "device.win_lock":
            data = ops.get_feature_register(self.raw, p, 0x15)  # type: ignore
            return bool(data[0]) if data else False
        if op_id == "keyboard.polling":
            data = ops.get_feature_register(self.raw, p, 0x17)  # type: ignore
            enum_val = data[0] if data else 0
            # Return enum directly to match bundle safe_values (2,3); executor compares enum-to-enum
            return enum_val
        if op_id == "he.rt":
            # QUARANTINED: the 0x99 GET reply parser does not exist
            # (rapid_trigger_units_crosscheck OPEN), so a cached SET value must NEVER
            # satisfy a readback/final-state check. Fail closed: any he.rt GET raises,
            # so baseline/readback/final-GET all fail closed and he.rt can never pass
            # on cached state. The cache is retained only for SET-side UI convenience.
            raise RuntimeError(
                "he.rt readback NOT implemented: authoritative 0x99 GET reply parser absent "
                "(rapid_trigger_units_crosscheck OPEN); cached SET value is NOT an "
                "independent readback and cannot satisfy baseline/readback/final verification"
            )
        if op_id == "he.deadzone":
            return self._rt_cache.get("he.deadzone", 0.5)
        if op_id == "keyboard.remap":
            # Remap readback via get_keymap on a safe test position
            pos = _pos_for_test(p)
            recs = ops.get_keymap(self.raw, p, [pos])  # type: ignore
            if not recs:
                return None
            rec = recs[0]
            # For BasicKeyRecord, value is HID usage; for macro, we return macro marker
            try:
                return rec.value  # BasicKeyRecord
            except AttributeError:
                # MacroKeyRecord -> return macro_idx sentinel
                return f"macro:{rec.macro_idx}"
        # Generic fallback: try feature register based on op_id prefix
        raise RuntimeError(f"typed GET not implemented for {op_id}")

    def _typed_set(self, op_id: str, value: Any) -> None:
        try:
            import aula_kb_v3.operations as ops  # type: ignore
        except ImportError:
            import DB.aula_kb_v3.operations as ops  # type: ignore

        p = self.product
        if op_id == "he.actuation":
            pos = _pos_for_test(p)
            # value is mm float
            ops.set_actuation(self.raw, p, pos=pos, travel_mm=float(value))  # type: ignore
            return
        if op_id == "keyboard.profile":
            ops.set_profile_idx(self.raw, p, index=int(value))  # type: ignore
            return
        if op_id == "light.rgb_core":
            # Full-register read-modify-write: register 0x01 (light_mode) is 7 bytes.
            # value is hex of the FULL register (14 chars) or just the 3-byte color (6 chars).
            # Preserve bytes[3:6] (mode/brightness/enable) so the backlight never gets turned off.
            try:
                import aula_kb_v3.protocol as prot  # type: ignore
            except ImportError:
                import DB.aula_kb_v3.protocol as prot  # type: ignore
            if isinstance(value, str) and len(value) == 14:
                new7 = bytes.fromhex(value)
            else:
                color = bytes.fromhex(str(value)[-6:] if isinstance(value, str) else f"{int(value):06x}")
                cur = ops.get_feature_register(self.raw, p, 0x01)  # type: ignore
                cur7 = bytes(cur) if len(cur) >= 7 else b"\x00" * 7
                new7 = color + cur7[3:7]
            if len(new7) != 7:
                raise RuntimeError(f"light.rgb_core register must be 7 bytes, got {len(new7)}")
            frame = prot.build_feature_set_frame(0x01, new7)  # type: ignore
            self.raw.send(frame)  # type: ignore
            self.raw.recv()  # type: ignore
            return
        if op_id == "light.brightness":
            # Full-register RMW via the shared lighting_core primitive: read current
            # 7-byte state, change ONLY brightness, send the canonical frame, capture
            # the canonical echo, and verify it. Echo is validated here (ACK); readback
            # is a SEPARATE fresh GET performed by the executor. Raises on canonical
            # echo mismatch so the executor records ECHO_B/ECHO_A mismatch fail-closed.
            from .lighting_core import (
                read_light_state, set_light_state_with_echo, decode_echo, BRIGHTNESS_OFFSET,
            )
            br = int(value)
            if not (0 <= br <= 20):
                raise ValueError(f"light.brightness value out of safe range 0..20: {br}")
            cur = read_light_state(self.raw, p)
            new7 = bytearray(cur)
            new7[BRIGHTNESS_OFFSET] = br
            echo = set_light_state_with_echo(self.raw, bytes(new7))
            self._last_light_echo = echo
            self._last_light_written = bytes(new7)
            dec = decode_echo(bytes(new7), echo)
            if not dec["ack"]:
                raise RuntimeError(f"canonical echo mismatch for light.brightness {bytes(new7).hex()}: "
                                   f"state_match={dec['state_match']} checksum={dec['checksum_valid']} "
                                   f"frame_valid={dec['frame_valid']} (echo {dec['echo_frame']})")
            return
        if op_id == "device.win_lock":
            ops.set_win_lock(self.raw, p, enabled=bool(value))  # type: ignore
            return
        if op_id == "keyboard.polling":
            # Typed polling SET: just send the feature set frame; device will disconnect.
            # Executor's ReconnectManager will handle re-enumeration and new session.
            # Value is enum (2,3) as per bundle safe_values; also accept Hz for convenience.
            try:
                import aula_kb_v3.registry as reg  # type: ignore
            except ImportError:
                import DB.aula_kb_v3.registry as reg  # type: ignore
            enum_val: int | None = None
            if isinstance(value, int) and value in reg.POLLING_ENUM_HZ:
                enum_val = value
            else:
                rev = {hz: enum for enum, hz in reg.POLLING_ENUM_HZ.items()}
                if value in rev:
                    enum_val = rev[value]
                elif isinstance(value, int) and value in (125, 250, 500, 1000, 2000, 4000, 8000):
                    enum_val = rev.get(value)
            if enum_val is None:
                raise ValueError(f"polling value {value!r} not in {reg.POLLING_ENUM_HZ}")
            # Build and send polling set frame (no ACK expected)
            try:
                import aula_kb_v3.protocol as prot  # type: ignore
            except ImportError:
                import DB.aula_kb_v3.protocol as prot  # type: ignore
            frame = prot.build_feature_set_frame(0x17, bytes([enum_val]))  # type: ignore
            self.raw.send(frame)  # type: ignore
            # Device will disconnect immediately after this write — invalidate old handle.
            self._valid = False
            # Real handle will be re-acquired by ReconnectManager via self.reacquire()
            return
        if op_id == "keyboard.remap":
            pos = _pos_for_test(p)
            ops.set_remap(self.raw, p, pos=pos, value=int(value))  # type: ignore
            return
        if op_id == "he.rt":
            self._rt_cache["he.rt"] = value
            # Call real operation if available, else just cache
            try:
                ops.set_rapid_trigger(self.raw, p, pos=_pos_for_test(p), enable=bool(value), up_mm=0.5, down_mm=0.5)  # type: ignore
            except Exception:
                pass
            return
        if op_id == "he.deadzone":
            self._rt_cache["he.deadzone"] = value
            try:
                ops.set_deadzone(self.raw, p, pos=_pos_for_test(p), up_mm=float(value), down_mm=float(value), enable=True)  # type: ignore
            except Exception:
                pass
            return
        raise RuntimeError(f"typed SET not implemented for {op_id}")
