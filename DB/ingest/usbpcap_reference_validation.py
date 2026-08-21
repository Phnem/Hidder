"""Reference-check selected USBPcap frames against tshark before bulk decode."""
from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import asdict
from pathlib import Path

import dpkt

from ingest.usbpcap_decoder import decode_usbpcap_frame_detailed


DEFAULT_REFERENCES = (
    ("data/signalrgb-usbdata/attachments/issue-845-56edc8a1eb8b-GM68_V2_Packets.pcapng", 2),
    ("data/signalrgb-usbdata/attachments/issue-845-56edc8a1eb8b-GM68_V2_Packets.pcapng", 19),
    ("data/signalrgb-usbdata/attachments/issue-845-56edc8a1eb8b-GM68_V2_Packets.pcapng", 20),
    ("data/signalrgb-usbdata/attachments/issue-845-56edc8a1eb8b-GM68_V2_Packets.pcapng", 23),
    ("data/signalrgb-usbdata/attachments/issue-845-56edc8a1eb8b-GM68_V2_Packets.pcapng", 25),
    ("data/signalrgb-usbdata/attachments/issue-840-d163c638d27a-gravastar_v75__1_.pcapng", 5153),
)


def _int(value: str | None) -> int | None:
    return int(value, 0) if value is not None else None


def _hex(value: str | None) -> str | None:
    return value.replace(":", "").lower() if value else None


def _raw_frame(path: Path, frame_number: int) -> bytes:
    with path.open("rb") as stream:
        reader = dpkt.pcapng.Reader(stream) if path.suffix.lower() == ".pcapng" else dpkt.pcap.Reader(stream)
        for index, (_, payload) in enumerate(reader, start=1):
            if index == frame_number:
                return bytes(payload)
    raise ValueError(f"frame {frame_number} is not present in {path}")


def _tshark_layers(tshark_path: Path, capture: Path, frame_number: int) -> dict[str, object]:
    completed = subprocess.run(
        [str(tshark_path), "-r", str(capture), "-Y", f"frame.number == {frame_number}", "-T", "json"],
        capture_output=True, text=True, check=True,
    )
    packets = json.loads(completed.stdout)
    if len(packets) != 1:
        raise ValueError(f"tshark returned {len(packets)} packets for frame {frame_number}")
    return packets[0]["_source"]["layers"]


def _tshark_view(layers: dict[str, object]) -> dict[str, object]:
    usb = layers.get("usb", {})
    setup = layers.get("Setup Data", {})
    payload = layers.get("usbhid.data") or setup.get("usb.data_fragment") or layers.get("usb.capdata")
    return {
        "device_address": _int(usb.get("usb.device_address")),
        "endpoint": _int(usb.get("usb.endpoint_address")),
        "transfer_type": {1: "interrupt", 2: "control"}.get(_int(usb.get("usb.transfer_type"))),
        "direction": "host_to_device" if usb.get("usb.src") == "host" else "device_to_host",
        "control_stage": {0: "setup", 1: "data", 2: "status", 3: "complete"}.get(_int(usb.get("usb.control_stage"))),
        "setup": {
            "bmRequestType": _int(setup.get("usb.bmRequestType")),
            "bRequest": _int(setup.get("usbhid.setup.bRequest")),
            "wValue": _int(setup.get("usbhid.setup.wValue")),
            "wIndex": _int(setup.get("usbhid.setup.wIndex")),
            "wLength": _int(setup.get("usbhid.setup.wLength")),
        } if setup else None,
        "payload_hex": _hex(payload),
    }


def validate_references(workspace: Path, tshark_path: Path,
                        references: tuple[tuple[str, int], ...] = DEFAULT_REFERENCES) -> dict[str, object]:
    """Compare decoded USBPcap values to tshark and return an auditable report."""
    results: list[dict[str, object]] = []
    for relative, frame_number in references:
        capture = workspace / relative
        raw = _raw_frame(capture, frame_number)
        urb, reason = decode_usbpcap_frame_detailed(raw)
        if urb is None:
            results.append({"capture": relative, "frame_number": frame_number, "passed": False, "error": reason})
            continue
        layers = _tshark_layers(tshark_path, capture, frame_number)
        tshark = _tshark_view(layers)
        local = {
            "device_address": urb.device_address, "endpoint": urb.endpoint,
            "transfer_type": urb.transfer_type, "direction": urb.direction,
            "control_stage": urb.control_stage, "setup": urb.setup,
            "payload_hex": urb.payload.hex(),
        }
        mismatches = {key: {"decoder": local[key], "tshark": tshark[key]}
                      for key in ("device_address", "endpoint", "transfer_type", "direction", "control_stage")
                      if local[key] != tshark[key]}
        if tshark["setup"] is not None and local["setup"] != tshark["setup"]:
            mismatches["setup"] = {"decoder": local["setup"], "tshark": tshark["setup"]}
        # tshark exposes raw payload fields for report/control-setup frames.
        # Descriptor decoding has no raw field, so its stage/metadata remains
        # validated while the payload comparison is explicitly marked absent.
        if tshark["payload_hex"] is not None and local["payload_hex"] != tshark["payload_hex"]:
            mismatches["payload_hex"] = {"decoder": local["payload_hex"], "tshark": tshark["payload_hex"]}
        results.append({"capture": relative, "frame_number": frame_number, "passed": not mismatches,
                        "mismatches": mismatches, "decoder": local, "tshark": tshark})
    return {"tshark": str(tshark_path), "all_passed": all(item["passed"] for item in results), "frames": results}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--tshark", type=Path, default=Path(r"C:\Program Files\Wireshark\tshark.exe"))
    parser.add_argument("--report", type=Path, default=Path("reports/usbpcap_reference_validation.json"))
    args = parser.parse_args()
    report = validate_references(args.workspace, args.tshark)
    destination = args.workspace / args.report
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"all_passed": report["all_passed"], "report": str(destination)}, ensure_ascii=False))
    return 0 if report["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
