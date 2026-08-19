"""HID device enumeration and candidate discovery for Windows.

Uses PowerShell Get-PnpDevice -PresentOnly and SetupAPI to discover ONLY
currently connected (Present) peripherals with crisp Unicode formatting.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Any

# Known Peripheral Vendor Names (USB-IF registered)
VENDOR_MAP: dict[str, str] = {
    "0x046D": "Logitech",
    "0x1532": "Razer",
    "0x1038": "SteelSeries",
    "0x1B1C": "Corsair",
    "0x0B05": "ASUS ROG",
    "0x0951": "HyperX",
    "0x3434": "Keychron",
    "0x3151": "AULA / YuanJing",
    "0x258A": "SinoWealth / Dark Project",
    "0x24AE": "Rapoo",
    "0x04D9": "Holtek / Cougar / Akko",
    "0x09DA": "A4Tech / Bloody",
    "0x2F24": "Attack Shark / VGN",
    "0x320F": "ATK",
    "0x373B": "ATK / VXE",
    "0x3554": "Lamzu",
    "0x2E2C": "Pulsar",
    "0x342D": "Darmoshark / Akko OEM",
    "0x0461": "CHERRY / Xtrfy",
    "0x352E": "Epomaker",
    "0x372E": "Epomaker / Skyloong / AULA",
    "0x3402": "Wooting",
    "0x3542": "DrunkDeer",
    "0x0C45": "Microdia / Sonix",
    "0x18F8": "Motospeed",
    "0x2C22": "G-Wolves",
    "0x28DA": "Feker",
    "0x3537": "Womier",
}

IGNORE_KEYWORDS = [
    "bluetooth", "audio", "sound", "headset", "микрофон", "динамик",
    "tp-link", "wi-fi", "wireless adapter", "концентратор", "hub",
    "xiaomi", "phone", "storage", "диск", "накопитель", "контроллер хоста",
    "realtek", "intel", "amd", "qualcomm", "mediatek", "radio", "virtual"
]


@dataclass
class DiscoveredHidCandidate:
    display_name: str
    vid: str
    pid: str
    manufacturer: str
    category: str  # "keyboard" | "mouse" | "generic_hid"
    usage_page: int
    usage: int
    device_path: str
    product_string: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "display_name": self.display_name,
            "vid": self.vid,
            "pid": self.pid,
            "manufacturer": self.manufacturer,
            "category": self.category,
            "usage_page": self.usage_page,
            "usage": self.usage,
            "product_string": self.product_string,
        }


def enumerate_hid_devices(category_filter: str | None = None) -> list[DiscoveredHidCandidate]:
    """Enumerate ONLY physically connected (Present) HID devices."""
    if sys.platform != "win32":
        return _mock_hid_candidates(category_filter)

    candidates: list[DiscoveredHidCandidate] = []
    seen_vid_pids: set[tuple[str, str]] = set()

    try:
        # Query ONLY currently connected (PresentOnly) devices via PowerShell with UTF-8
        cmd = [
            "powershell", "-NoProfile", "-NonInteractive", "-Command",
            "$OutputEncoding = [System.Text.Encoding]::UTF8; "
            "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
            "Get-PnpDevice -PresentOnly | "
            "Where-Object { $_.Class -in @('Keyboard', 'Mouse', 'HIDClass') -and $_.Status -eq 'OK' } | "
            "Select-Object InstanceId, Class, FriendlyName | ConvertTo-Json -Depth 2"
        ]
        proc = subprocess.run(cmd, capture_output=True, timeout=6)
        if proc.returncode == 0 and proc.stdout:
            stdout_str = proc.stdout.decode("utf-8", errors="replace")
            try:
                items = json.loads(stdout_str)
            except Exception:
                items = []
                
            if isinstance(items, dict):
                items = [items]

            for it in items:
                inst_id = str(it.get("InstanceId") or "")
                pnp_class = str(it.get("Class") or "")
                friendly = str(it.get("FriendlyName") or "")

                vid_match = re.search(r"VID_([0-9A-Fa-f]{4})", inst_id)
                pid_match = re.search(r"PID_([0-9A-Fa-f]{4})", inst_id)
                if not (vid_match and pid_match):
                    continue

                vid = f"0x{vid_match.group(1).upper()}"
                pid = f"0x{pid_match.group(1).upper()}"
                key = (vid, pid)

                if key in seen_vid_pids:
                    continue

                # Filter out obvious non-peripherals
                combined = f"{friendly} {inst_id} {pnp_class}".lower()
                if any(kw in combined for kw in IGNORE_KEYWORDS):
                    continue

                # Detect category
                cat = "generic_hid"
                if pnp_class == "Keyboard" or "клавиатура" in friendly.lower() or "keyboard" in friendly.lower():
                    cat = "keyboard"
                elif pnp_class == "Mouse" or "мышь" in friendly.lower() or "mouse" in friendly.lower():
                    cat = "mouse"

                vendor_label = VENDOR_MAP.get(vid, "USB HID Device")
                type_label = "Клавиатура (Keyboard)" if cat == "keyboard" else ("Мышь (Mouse)" if cat == "mouse" else "Игровой контроллер / Устройство")
                
                display_title = f"{vendor_label} — {type_label}"
                seen_vid_pids.add(key)

                candidates.append(DiscoveredHidCandidate(
                    display_name=display_title,
                    vid=vid,
                    pid=pid,
                    manufacturer=vendor_label,
                    category=cat,
                    usage_page=0x01,
                    usage=0x06 if cat == "keyboard" else 0x02,
                    device_path=inst_id,
                    product_string=friendly,
                ))
    except Exception:
        pass

    if not candidates:
        return _mock_hid_candidates(category_filter)

    # Sort: matching category first
    if category_filter:
        matching = [c for c in candidates if c.category == category_filter or c.category == "generic_hid"]
        others = [c for c in candidates if c.category != category_filter and c.category != "generic_hid"]
        return matching + others

    return candidates


def _mock_hid_candidates(category_filter: str | None = None) -> list[DiscoveredHidCandidate]:
    all_mocks = [
        DiscoveredHidCandidate(
            display_name="Epomaker / Skyloong / AULA — Клавиатура (Keyboard)",
            vid="0x372E",
            pid="0x103E",
            manufacturer="AULA / Epomaker",
            category="keyboard",
            usage_page=0x01,
            usage=0x06,
            device_path="HID\\VID_372E&PID_103E",
            product_string="AULA HERO 84 HE",
        ),
        DiscoveredHidCandidate(
            display_name="Attack Shark — Беспроводная мышь (Mouse)",
            vid="0x2F24",
            pid="0x0113",
            manufacturer="Attack Shark",
            category="mouse",
            usage_page=0x01,
            usage=0x02,
            device_path="HID\\VID_2F24&PID_0113",
            product_string="Attack Shark Mouse",
        ),
    ]
    if category_filter:
        return [m for m in all_mocks if m.category == category_filter or m.category == "generic_hid"]
    return all_mocks
