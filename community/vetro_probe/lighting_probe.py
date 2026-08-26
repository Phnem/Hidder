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
from community.vetro_probe.firmware_identity import read_firmware_via_raw
import aula_kb_v3.operations as ops  # type: ignore


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vetro.lighting_probe", description="Passive lighting protocol probe (read-only)")
    parser.add_argument("--snapshot", action="store_true", help="Dump current full lighting state once")
    parser.add_argument("--sweep", type=Path, default=None, help="Run interactive differential sweep, log to this file")
    args = parser.parse_args(argv)

    if args.snapshot:
        probe = LightingProbe()
        print(json.dumps(probe.snapshot(), indent=2))
        probe.close()
        return 0
    if args.sweep:
        run_sweep(Path(args.sweep))
        return 0
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
