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

        if not self.bundle.firmware_branch or self.bundle.firmware_branch == "unknown":
            return IdentityVerdict(False, "firmware branch unknown")

        # firmware branch must match or be prefix
        fw = instance.firmware_version or ""
        if fw and self.bundle.firmware_branch not in fw and fw not in self.bundle.firmware_branch:
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


def mock_hero84_instance(firmware: str = "1.17.3", connection: str = "wired") -> PhysicalInstance:
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
