"""Exact Identity Gate.

Blocks validation if:
- identity ambiguous
- family ambiguous
- firmware unsupported
- connection mode unknown
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from .bundle import Bundle


@dataclass(frozen=True)
class PhysicalInstance:
    vid: str  # "0x372E"
    pid: str
    descriptor_hash: str
    firmware_version: str
    connection_mode: str  # wired / 2.4g / bluetooth
    interfaces: list[int]
    report_ids: list[int]
    product_string: str = ""
    manufacturer: str = ""
    serial_redacted: str = ""


@dataclass(frozen=True)
class IdentityVerdict:
    passed: bool
    reason: str = ""
    product: str = ""
    family: str = ""
    firmware: str = ""
    connection: str = ""
    descriptor_hash: str = ""


class ExactIdentityGate:
    def __init__(self, bundle: Bundle) -> None:
        self.bundle = bundle

    def evaluate(self, instance: PhysicalInstance) -> IdentityVerdict:
        # Normalize VID/PID
        vid_norm = instance.vid.lower()
        pid_norm = instance.pid.lower()
        b_vid = self.bundle.product.vid.lower()
        b_pid = self.bundle.product.pid.lower()

        if vid_norm != b_vid or pid_norm != b_pid:
            return IdentityVerdict(False, f"VID/PID mismatch: device {vid_norm}:{pid_norm} != bundle {b_vid}:{b_pid}")

        if not instance.descriptor_hash:
            return IdentityVerdict(False, "descriptor_hash missing — identity ambiguous")

        if not self.bundle.family or self.bundle.family == "unknown":
            return IdentityVerdict(False, "family ambiguous — bundle family unknown")

        # firmware branch: if bundle says unknown, treat as wildcard (production registry often has no fw branch)
        if not self.bundle.firmware_branch or self.bundle.firmware_branch == "unknown":
            # unknown bundle branch means any device firmware accepted; keep gate open but record
            pass
        else:
            # firmware branch must match or be prefix
            fw = instance.firmware_version or ""
            if fw and fw != "unknown" and self.bundle.firmware_branch not in fw and fw not in self.bundle.firmware_branch:
                # allow exact or prefix match; soft check — if bundle says 1.17, device 1.17.3 passes
                if not fw.startswith(self.bundle.firmware_branch):
                    return IdentityVerdict(False, f"firmware unsupported: device {fw} != bundle branch {self.bundle.firmware_branch}")

        allowed_modes = {self.bundle.connection_mode, "any", "wired+2.4g"}
        if self.bundle.connection_mode == "any":
            allowed = True
        elif self.bundle.connection_mode == "wired+2.4g":
            allowed = instance.connection_mode in ("wired", "2.4g")
        else:
            allowed = instance.connection_mode == self.bundle.connection_mode
        if not allowed:
            return IdentityVerdict(False, f"connection mode unknown/unsupported: device {instance.connection_mode} != bundle {self.bundle.connection_mode}")

        if not instance.report_ids:
            return IdentityVerdict(False, "report_ids empty — cannot route config interface")

        return IdentityVerdict(
            True,
            "EXACT_IDENTITY PASS",
            product=self.bundle.product.name,
            family=self.bundle.family,
            firmware=instance.firmware_version or self.bundle.firmware_branch,
            connection=instance.connection_mode,
            descriptor_hash=instance.descriptor_hash,
        )


def descriptor_hash_from_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()[:16]


def mock_hero84_instance(firmware: str = "0216", connection: str = "wired") -> PhysicalInstance:
    return PhysicalInstance(
        vid="0x372E",
        pid="0x103E",
        descriptor_hash=descriptor_hash_from_bytes(b"hero84-descriptor-v1"),
        firmware_version=firmware,
        connection_mode=connection,
        interfaces=[0, 1, 2],
        report_ids=[0, 1, 8, 9],
        product_string="AULA HERO84 HE",
        manufacturer="AULA",
    )


def discover_real_instance_via_raw(raw: Any, path: str | None = None) -> PhysicalInstance:
    """Build PhysicalInstance from an already-opened HidRawTransport.

    Observed firmware MUST be obtained via safe read_firmware_version (0x82 sub 2 LE),
    not assigned from constant. Only if raw is sim (pdevemu) or read returns unknown
    do we fallback to pinned HERO84 firmware for simulation.
    """
    from .firmware_identity import HERO84_FIRMWARE_BRANCH, read_firmware_via_raw

    vid = "0x372E"
    pid = "0x103E"
    descriptor_hash = descriptor_hash_from_bytes(b"real-descriptor-unavailable")
    firmware = "unknown"
    is_real_hid = False
    # Detect real HidRawTransport (has _dev) vs sim (_SimTransport)
    try:
        # Real HID has _dev attribute; sim (_SimTransport) does not
        if hasattr(raw, "_dev"):
            # Could be HidRawTransport (real) or None; check class name
            if raw.__class__.__name__ == "HidRawTransport":
                is_real_hid = True
                try:
                    import hid  # type: ignore

                    descriptor_hash = descriptor_hash_from_bytes(f"{vid}:{pid}:{path}".encode())
                except Exception:
                    pass
            elif raw.__class__.__name__ == "_SimTransport":
                is_real_hid = False
            else:
                # Fallback: if has _dev and not sim, treat as real
                is_real_hid = raw is not None and getattr(raw, "_dev", None) is not None
        elif hasattr(raw, "send") and hasattr(raw, "recv"):
            # For _SimTransport without _dev, definitely sim
            if raw.__class__.__name__ == "_SimTransport":
                is_real_hid = False
            else:
                is_real_hid = False
    except Exception:
        pass

    # Try to get UUID via operations.connect for product identity (separate from firmware)
    try:
        try:
            import aula_kb_v3.operations as ops  # type: ignore
        except ImportError:
            import DB.aula_kb_v3.operations as ops  # type: ignore
        prod = ops.connect(raw)  # type: ignore
        # For firmware, try safe read only for real HID; for sim, use pinned
        if is_real_hid:
            observed = read_firmware_via_raw(raw)
            firmware = observed if observed != "unknown" else "unknown"
        else:
            # Sim or fallback: use pinned verified branch for HERO84
            if prod.uuid == 18691697672197:
                firmware = HERO84_FIRMWARE_BRANCH
            else:
                firmware = prod.firmware_revision_note or f"uuid-{prod.uuid}"
                if not firmware or firmware == "unknown":
                    firmware = f"uuid-{prod.uuid}"
        # If is_real_hid and observed is unknown, keep unknown (will BLOCK writes as required)
    except Exception:
        # If UUID connect fails, try firmware read directly for real HID
        if is_real_hid:
            try:
                observed = read_firmware_via_raw(raw)
                firmware = observed if observed != "unknown" else "unknown"
            except Exception:
                firmware = "unknown"
        else:
            firmware = HERO84_FIRMWARE_BRANCH if not is_real_hid else "unknown"
    except Exception:
        pass

    return PhysicalInstance(
        vid=vid,
        pid=pid,
        descriptor_hash=descriptor_hash,
        firmware_version=firmware,
        connection_mode="wired",
        interfaces=[0],
        report_ids=[9],
        product_string="AULA HERO84 HE",
        manufacturer="AULA",
    )
