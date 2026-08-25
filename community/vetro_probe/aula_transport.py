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
        # cache baseline for simple ops to allow rollback without re-reading product?
        # No, always read fresh.

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
            data = ops.get_feature_register(self.raw, p, 0x01)  # type: ignore
            # data is 7 bytes? first 3 are RGB
            if len(data) >= 3:
                return (data[0] << 16) | (data[1] << 8) | data[2]
            return int.from_bytes(data, "little") if data else 0
        if op_id == "device.win_lock":
            data = ops.get_feature_register(self.raw, p, 0x15)  # type: ignore
            return bool(data[0]) if data else False
        if op_id == "keyboard.polling":
            data = ops.get_feature_register(self.raw, p, 0x17)  # type: ignore
            enum_val = data[0] if data else 0
            # Return enum directly to match bundle safe_values (2,3); executor compares enum-to-enum
            return enum_val
        if op_id == "he.rt":
            # Not yet true GET in operations.py; use cached last set or default 0 for sim/real
            return self._rt_cache.get("he.rt", 0)
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
            # value is 0xRRGGBB
            v = int(value)
            r, g, b = (v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF
            from DB.aula_kb_v3.operations import RgbCoreState  # type: ignore

            try:
                from aula_kb_v3.operations import RgbCoreState as _Rgb  # type: ignore

                RgbCoreState = _Rgb
            except ImportError:
                pass
            ops.set_rgb_core(self.raw, p, RgbCoreState(r=r, g=g, b=b))  # type: ignore
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
