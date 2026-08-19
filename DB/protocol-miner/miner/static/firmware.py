"""Conservative USB device-descriptor mining from firmware bytes; never flashes."""

from __future__ import annotations

_VALID_BCD_USB = {0x0100, 0x0110, 0x0200, 0x0201, 0x0210, 0x0300, 0x0310, 0x0320}
_VALID_MAX_PACKET = {8, 16, 32, 64, 512, 9}
_VALID_CLASSES = {0x00, 0x02, 0x03, 0x09, 0x11, 0xEF, 0xFF}


def usb_device_descriptors(raw: bytes) -> list[dict[str, int]]:
    """Find well-formed USB device descriptors with strict USB standard conformance."""
    results: list[dict[str, int]] = []
    for offset in range(0, max(0, len(raw) - 18 + 1)):
        view = raw[offset:offset + 18]
        if len(view) != 18 or view[0] != 18 or view[1] != 1:
            continue
        bcd_usb = int.from_bytes(view[2:4], "little")
        if bcd_usb not in _VALID_BCD_USB:
            continue
        dev_class = view[4]
        if dev_class not in _VALID_CLASSES:
            continue
        max_packet = view[7]
        if max_packet not in _VALID_MAX_PACKET:
            continue
        num_configs = view[17]
        if num_configs not in (1, 2, 3, 4):
            continue
        vid = int.from_bytes(view[8:10], "little")
        pid = int.from_bytes(view[10:12], "little")
        if vid == 0 or pid == 0 or vid == 0xFFFF or pid == 0xFFFF:
            continue
        candidate = {"offset": offset, "vid": vid, "pid": pid, "usb_bcd": bcd_usb}
        if candidate not in results:
            results.append(candidate)
    return results
