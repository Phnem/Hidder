"""Conservative USB device-descriptor mining from firmware bytes; never flashes."""

from __future__ import annotations


def usb_device_descriptors(raw: bytes) -> list[dict[str, int]]:
    """Find well-formed USB device descriptors (bLength=18, bDescriptorType=1)."""
    results: list[dict[str, int]] = []
    for offset in range(0, max(0, len(raw) - 18 + 1)):
        view = raw[offset:offset + 18]
        if len(view) != 18 or view[0] != 18 or view[1] != 1 or view[7] == 0:
            continue
        vid = int.from_bytes(view[8:10], "little")
        pid = int.from_bytes(view[10:12], "little")
        if vid == 0 or pid == 0:
            continue
        candidate = {"offset": offset, "vid": vid, "pid": pid, "usb_bcd": int.from_bytes(view[2:4], "little")}
        if candidate not in results:
            results.append(candidate)
    return results
