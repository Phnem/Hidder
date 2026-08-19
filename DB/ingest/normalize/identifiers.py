"""VID/PID parsing, normalization, and validation."""

import re
from typing import Optional, NamedTuple


class VidPid(NamedTuple):
    vid: int
    pid: int
    vid_hex: str
    pid_hex: str


# Regex patterns
RE_VID_PID_WINDOWS = re.compile(
    r'(?:USB|HID|PCI)[\\_]VID_([0-9A-Fa-f]{4})[&_]PID_([0-9A-Fa-f]{4})',
    re.IGNORECASE
)

RE_VID_PID_PAIR = re.compile(
    r'VID[_\s:=]+(?:0x)?([0-9A-Fa-f]{4})[,\s;&\n\r]+PID[_\s:=]+(?:0x)?([0-9A-Fa-f]{4})',
    re.IGNORECASE
)

RE_CODE_VID_PID_HEX = re.compile(
    r'(?:vendor[_-]?id|vid|v_id)[^\w\d]{1,10}(?:0x)?([0-9A-Fa-f]{4})[^\w\d]{1,20}(?:product[_-]?id|pid|p_id)[^\w\d]{1,10}(?:0x)?([0-9A-Fa-f]{4})',
    re.IGNORECASE
)

RE_CODE_PID_VID_HEX = re.compile(
    r'(?:product[_-]?id|pid|p_id)[^\w\d]{1,10}(?:0x)?([0-9A-Fa-f]{4})[^\w\d]{1,20}(?:vendor[_-]?id|vid|v_id)[^\w\d]{1,10}(?:0x)?([0-9A-Fa-f]{4})',
    re.IGNORECASE
)

RE_CODE_DECIMAL_PAIR = re.compile(
    r'(?:vendor[_-]?id|vid|v_id)[^\w\d]{1,10}([0-9]{1,5})[^\w\d]{1,20}(?:product[_-]?id|pid|p_id)[^\w\d]{1,10}([0-9]{1,5})',
    re.IGNORECASE
)


def format_hex4(val: int) -> str:
    """Format integer as 0xXXXX (uppercase 4-digit hex)."""
    return f"0x{val:04X}"


def parse_hex_or_dec(val: str | int) -> Optional[int]:
    """Parse a hex or decimal string/int into an integer."""
    if isinstance(val, int):
        return val if 0 <= val <= 0xFFFF else None
    val_str = str(val).strip()
    if not val_str:
        return None
    try:
        if val_str.lower().startswith("0x"):
            n = int(val_str, 16)
        elif all(c in "0123456789ABCDEFabcdef" for c in val_str) and len(val_str) == 4 and not val_str.isdigit():
            n = int(val_str, 16)
        else:
            n = int(val_str, 10)
        if 0 <= n <= 0xFFFF:
            return n
    except ValueError:
        pass
    return None


def normalize_vid_pid(vid_raw: str | int, pid_raw: str | int) -> Optional[VidPid]:
    """Normalize and validate raw VID/PID values."""
    vid = parse_hex_or_dec(vid_raw)
    pid = parse_hex_or_dec(pid_raw)
    if vid is None or pid is None:
        return None
    # Validate realistic USB ranges (VID 0x0000 is invalid in standard USB specs)
    if vid == 0:
        return None
    return VidPid(
        vid=vid,
        pid=pid,
        vid_hex=format_hex4(vid),
        pid_hex=format_hex4(pid),
    )


def extract_vid_pid_from_text(text: str) -> list[VidPid]:
    """Extract all valid VID/PID pairs from a given text snippet."""
    results: list[VidPid] = []
    seen = set()

    def add_match(v_str: str, p_str: str, is_hex: bool = True):
        try:
            v_int = int(v_str, 16 if is_hex else 10)
            p_int = int(p_str, 16 if is_hex else 10)
            normalized = normalize_vid_pid(v_int, p_int)
            if normalized and (normalized.vid, normalized.pid) not in seen:
                seen.add((normalized.vid, normalized.pid))
                results.append(normalized)
        except ValueError:
            pass

    # 1. Match Windows HWID strings (USB\VID_xxxx&PID_yyyy)
    for m in RE_VID_PID_WINDOWS.finditer(text):
        add_match(m.group(1), m.group(2), is_hex=True)

    # 2. Match VID: 0xXXXX PID: 0xYYYY
    for m in RE_VID_PID_PAIR.finditer(text):
        add_match(m.group(1), m.group(2), is_hex=True)

    # 3. Match code patterns (vendorId: 0x..., productId: 0x...)
    for m in RE_CODE_VID_PID_HEX.finditer(text):
        add_match(m.group(1), m.group(2), is_hex=True)

    # 4. Match reverse code patterns (productId: 0x..., vendorId: 0x...)
    for m in RE_CODE_PID_VID_HEX.finditer(text):
        add_match(m.group(2), m.group(1), is_hex=True)

    # 5. Match decimal code patterns (only when explicitly named vendorId/productId)
    for m in RE_CODE_DECIMAL_PAIR.finditer(text):
        add_match(m.group(1), m.group(2), is_hex=False)

    return results
