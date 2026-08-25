"""Single source of truth for HERO84 firmware identity.

Physical/vendor semantic -> normalized FirmwareIdentity -> registry/product knowledge
-> exported validation bundle -> SafetyGate constraint.

Evidence: HERO84 physical capture all_traces.jsonl seq 69-70, 79-80:
  HOST  820200010001... (group 0x82 sub 2, generic firmware fetch)
  DEVICE 8202000100011602... (payload bytes 6:7 = 16 02 little-endian -> 0x0216 -> vendor display "0216")
Vendor JS: fetch_firmware_version -> Lt.fetch_firmware_version in hero.aulastar.com/app-CPb3bToC.js
Little-endian decode verified: a[0]=0x16, a[1]=0x02 -> o*256+i -> 0x0216.
If this value has a different documented meaning, this file is the place to fix it;
all consumers (bundle export, identity, SafetyGate) import from here.
"""

from __future__ import annotations

# The only physically proven firmware for HERO84 HE (372E:103E, uuid 18691697672197)
HERO84_FIRMWARE_BRANCH = "0216"
HERO84_FIRMWARE_DISPLAY = "0216"
HERO84_FIRMWARE_RAW_LE = bytes([0x16, 0x02])  # little-endian 0x0216
HERO84_FIRMWARE_EVIDENCE = "DB/reports/oracle/aula_web/HERO_84_HE/physical_capture/all_traces.jsonl seq 69-70 (0x82 sub 2 -> 16 02 LE)"

# For other products in family, firmware is not yet physically proven; keep unknown
# and writes will be BLOCKED until verified.
UNKNOWN_FIRMWARE_BRANCH = "unknown"

# Allow-list of explicitly verified compatible branches/ranges.
# For HERO84, the single proven branch is 0216; no other branch is verified.
VERIFIED_BRANCHES: dict[int, set[str]] = {
    18691697672197: {"0216"},
}


def is_verified_firmware(uuid: int, branch: str) -> bool:
    return branch in VERIFIED_BRANCHES.get(uuid, set())


def normalize_firmware_display(raw_le: bytes) -> str:
    """Vendor display is hi*256+lo but wire is little-endian."""
    if len(raw_le) != 2:
        return "unknown"
    lo, hi = raw_le[0], raw_le[1]
    val = hi * 256 + lo
    return f"{val:04x}"


def build_firmware_frame() -> bytes:
    """Build HOST->DEVICE firmware fetch (group 0x82 sub 2) as observed on wire.

    Frame layout per DB/aula_kb_v3/protocol.py build_frame:
      [group, sub, 0x00, 0x01, 0x00, len, data..., pad, checksum]
    Observed request: 82 02 00 01 00 01 00 ... checksum 70
    Observed reply:   82 02 00 01 00 01 16 02 ... (payload 16 02 LE at offset 6)
    """
    # Use protocol.build_frame if available, else manual
    try:
        import aula_kb_v3.protocol as prot  # type: ignore

        return bytes(prot.build_frame(0x82, sub=2, data=bytes([0x00])))
    except Exception:
        try:
            import DB.aula_kb_v3.protocol as prot  # type: ignore

            return bytes(prot.build_frame(0x82, sub=2, data=bytes([0x00])))
        except Exception:
            # Fallback manual: group 0x82 sub 2, reserved3 1, data [0x00]
            # Build 63B frame with checksum
            frame = bytearray(63)
            frame[0] = 0x82
            frame[1] = 0x02
            frame[2] = 0x00
            frame[3] = 0x01
            frame[4] = 0x00
            frame[5] = 0x01
            frame[6] = 0x00
            # checksum byte 62 = 255 - (9 + sum(frame[:62])) %256 ; report_id 9
            total = 9 + sum(frame[:62])
            frame[62] = 255 - (total % 256)
            return bytes(frame)


def parse_firmware_reply(reply: bytes) -> str:
    """Parse DEVICE->HOST reply for firmware. Reply is 63B frame, payload at 6:8.

    Vendor response-reader semantics: slice(6, len-1) / a.slice(6, a.length-1),
    NOT bounded by declared length at reply[5]. Physical fixture has
    reply[5]==0x01 but bytes 6:8 == 16 02 -> "0216", so declared length MUST NOT gate.

    Returns normalized display string like "0216" or "unknown" if malformed.
    """
    # Normalize 64B report-id form -> 63B
    if len(reply) == 64 and reply[0] == 9:
        reply = reply[1:]
    if len(reply) != 63:
        return "unknown"
    if len(reply) < 8:
        return "unknown"
    # Require correct frame identity (group 0x82 sub 0x02) — fail closed on wrong group/sub
    if reply[0] != 0x82 or reply[1] != 0x02:
        return "unknown"
    # Read bytes[6:8] directly per physical capture, NOT via declared length
    raw_le = bytes(reply[6:8])
    # 0000 is not a valid firmware (would be unknown)
    if raw_le == b"\x00\x00":
        return "unknown"
    return normalize_firmware_display(raw_le)


def read_firmware_via_raw(raw) -> str:
    """Safe read_firmware_version via raw HID transport.

    Does NOT assign from constant. Sends 0x82 sub 2 and parses LE reply.
    Returns "unknown" on timeout/error (caller must treat as BLOCK for writes).
    """
    # Detect sim transport (pdevemu) which currently echos uuid for sub2 -> not reliable
    # For sim, we can still try, but if reply is uuid-like (6 bytes), treat as unknown and let caller fallback
    # Real hardware must return 2-byte LE firmware.
    try:
        frame = build_firmware_frame()
        # raw is expected to have send(bytes) and recv(timeout_ms) per DB/aula_kb_v3/transport.py
        raw.send(frame)
        reply = raw.recv(timeout_ms=1000)
        # reply is 63B frame (or 64 with report_id)
        fw = parse_firmware_reply(reply)
        # Validate that fw is hex 4 chars and not all zeros
        if fw == "unknown" or fw == "0000":
            return "unknown"
        return fw
    except Exception:
        return "unknown"
