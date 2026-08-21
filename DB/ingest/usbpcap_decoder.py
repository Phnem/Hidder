"""Minimal, evidence-preserving USBPcap URB decoder.

The decoder returns transport metadata separately from the USB/HID payload.
It does not assign semantics or correlate to operations.
"""
from __future__ import annotations

from dataclasses import dataclass


USB_TRANSFER = {0: "isochronous", 1: "interrupt", 2: "control", 3: "bulk"}
CONTROL_STAGE = {0: "setup", 1: "data", 2: "status", 3: "complete"}


@dataclass(frozen=True)
class USBPcapURB:
    header_length: int
    urb_id: int
    urb_status: int
    urb_function: int
    irp_info: int
    bus_id: int
    device_address: int
    endpoint: int
    direction: str
    endpoint_number: int
    transfer_type: str
    data_length: int
    control_stage: str | None
    setup: dict[str, int] | None
    payload: bytes


def decode_usbpcap_frame_detailed(frame: bytes) -> tuple[USBPcapURB | None, str | None]:
    """Decode one DLT_USBPCAP frame and retain a machine-readable failure reason."""
    if len(frame) < 27:
        return None, "frame_too_short"
    header_length = int.from_bytes(frame[0:2], "little")
    if header_length < 27 or header_length > len(frame):
        return None, "invalid_header_length"
    urb_id = int.from_bytes(frame[2:10], "little")
    urb_status = int.from_bytes(frame[10:14], "little")
    urb_function = int.from_bytes(frame[14:16], "little")
    irp_info = frame[16]
    bus_id = int.from_bytes(frame[17:19], "little")
    device_address = int.from_bytes(frame[19:21], "little")
    endpoint = frame[21]
    transfer_code = frame[22]
    if transfer_code not in USB_TRANSFER:
        return None, "unsupported_transfer_type"
    data_length = int.from_bytes(frame[23:27], "little")
    # USBPcap's IRP direction is authoritative for interrupt/bulk transfers.
    # Control endpoint 0 has no direction bit, so its setup request defines it.
    direction = "device_to_host" if irp_info & 0x01 else "host_to_device"
    stage = CONTROL_STAGE.get(frame[27]) if transfer_code == 2 and header_length >= 28 else None
    offset = header_length
    setup = None
    if transfer_code == 2 and stage == "setup":
        if len(frame) < offset + 8:
            return None, "truncated_control_setup"
        raw = frame[offset:offset + 8]; offset += 8
        setup = {"bmRequestType": raw[0], "bRequest": raw[1],
                 "wValue": int.from_bytes(raw[2:4], "little"),
                 "wIndex": int.from_bytes(raw[4:6], "little"),
                 "wLength": int.from_bytes(raw[6:8], "little")}
        direction = "device_to_host" if raw[0] & 0x80 else "host_to_device"
    # USBPcap's control-setup ``DataLength`` includes the eight setup bytes;
    # the HID data starts after them. Interrupt/bulk lengths describe payload.
    payload_length = max(0, data_length - 8) if transfer_code == 2 and stage == "setup" else data_length
    if len(frame) < offset + payload_length:
        return None, "truncated_payload"
    payload = frame[offset:offset + payload_length]
    return USBPcapURB(header_length, urb_id, urb_status, urb_function, irp_info,
                      bus_id, device_address, endpoint, direction, endpoint & 0x0F,
                      USB_TRANSFER[transfer_code], data_length, stage, setup, payload), None


def decode_usbpcap_frame(frame: bytes) -> USBPcapURB | None:
    """Decode one DLT_USBPCAP frame, returning ``None`` for invalid input."""
    return decode_usbpcap_frame_detailed(frame)[0]
