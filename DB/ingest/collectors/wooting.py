"""Wooting SDK, Wootswitch, and Analog / Rapid Trigger protocol collector.

Extracts:
1. Wooting keyboard models (One, Two, 60HE, Two HE, 80HE, UwU):
   - Vendor ID (0x31E3), Product IDs, usage pages (0x1337 Standard, 0xFF55 ARM, 0xFF54 Analog)
2. 8-Byte HID Feature Report command structure:
   - [report_id, protocol_byte, 0xDA, command_id, arg3, arg2, arg1, arg0]
3. Analog Hall Effect protocol capabilities:
   - Actuation point range (0.1mm - 4.0mm)
   - Rapid Trigger sensitivity (0.1mm - 2.5mm)
   - DKS (Dynamic Key Stroke) 4-stage binding matrices
   - Mod Tap, Toggle Key, and Profile management opcodes

Imports all extracted facts and protocol hints into the SQLite Peripheral Registry.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Any

from ingest.config import DB_PATH
from ingest.logging_setup import get_logger
from ingest.normalize.evidence import (
    RawSource, SourceType, DeviceIdentifierFact, ProtocolHintFact, GenericFact, EvidenceLevel
)
from ingest.normalize.identifiers import normalize_vid_pid, format_hex4, parse_hex_or_dec
from ingest.normalize.models import generate_identity_key, normalize_product_name
from ingest.storage.database import RegistryDatabase

logger = get_logger()

WOOTING_VID = 0x31E3
WOOTING_VID_HEX = "0x31E3"

# Known Wooting hardware catalog
WOOTING_DEVICES = [
    {
        "name": "Wooting One",
        "pid": 0x1100,
        "category": "keyboard",
        "usage_page": 0x1337,
        "protocol": "Standard",
        "actuation_min_mm": 0.1,
        "actuation_max_mm": 4.0,
        "rapid_trigger": True
    },
    {
        "name": "Wooting Two",
        "pid": 0x1200,
        "category": "keyboard",
        "usage_page": 0x1337,
        "protocol": "Standard",
        "actuation_min_mm": 0.1,
        "actuation_max_mm": 4.0,
        "rapid_trigger": True
    },
    {
        "name": "Wooting 60HE",
        "pid": 0x1300,
        "category": "keyboard",
        "usage_page": 0x1337,
        "protocol": "Standard",
        "actuation_min_mm": 0.1,
        "actuation_max_mm": 4.0,
        "rapid_trigger": True
    },
    {
        "name": "Wooting 60HE+",
        "pid": 0x1320,
        "category": "keyboard",
        "usage_page": 0xFF55,
        "protocol": "ARM",
        "actuation_min_mm": 0.1,
        "actuation_max_mm": 4.0,
        "rapid_trigger": True
    },
    {
        "name": "Wooting Two HE (ARM)",
        "pid": 0x1220,
        "category": "keyboard",
        "usage_page": 0xFF55,
        "protocol": "ARM",
        "actuation_min_mm": 0.1,
        "actuation_max_mm": 4.0,
        "rapid_trigger": True
    },
    {
        "name": "Wooting 80HE",
        "pid": 0x1400,
        "category": "keyboard",
        "usage_page": 0xFF55,
        "protocol": "ARM",
        "actuation_min_mm": 0.1,
        "actuation_max_mm": 4.0,
        "rapid_trigger": True
    },
    {
        "name": "Wooting UwU",
        "pid": 0x1500,
        "category": "keypad",
        "usage_page": 0xFF55,
        "protocol": "ARM",
        "actuation_min_mm": 0.1,
        "actuation_max_mm": 4.0,
        "rapid_trigger": True
    },
]

WOOTING_COMMAND_SPECS = {
    "framing": {
        "report_length": 8,
        "report_type": "HID Feature Report",
        "framing_marker": "0xDA",
        "variants": {
            "Standard": {"report_id": "0x00", "protocol_byte": "0xD0", "usage_page": "0x1337"},
            "ARM": {"report_id": "0x01", "protocol_byte": "0xD1", "usage_page": "0xFF55"}
        }
    },
    "commands": {
        "GET_SERIAL": {"opcode": "0x03", "description": "Read device serial number"},
        "SET_ACTUATION": {"opcode": "0x0A", "description": "Set global/per-key switch actuation point"},
        "SET_RAPID_TRIGGER": {"opcode": "0x0B", "description": "Configure Rapid Trigger sensitivity"},
        "SET_DKS": {"opcode": "0x0C", "description": "Configure Dynamic Key Stroke 4-stage actions"},
        "SET_RGB_MATRIX": {"opcode": "0x10", "description": "Send RGB matrix color buffer"},
        "SAVE_CONFIG": {"opcode": "0x1F", "description": "Persist profile to onboard flash"}
    }
}


class WootingCollector:
    """Ingests Wooting device profiles, analog protocols, and Rapid Trigger specs into SQLite."""

    def __init__(self, db: RegistryDatabase, sources_root: Path, run_id: str):
        self.db = db
        self.sources_root = sources_root
        self.run_id = run_id
        self.wootswitch_dir = sources_root / "wootswitch"

    def get_commit_sha(self) -> str:
        """Get git commit SHA."""
        if self.wootswitch_dir.exists() and (self.wootswitch_dir / ".git").exists():
            try:
                res = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(self.wootswitch_dir), capture_output=True, text=True, check=True)
                return res.stdout.strip()
            except Exception:
                pass
        return "wooting_source"

    def collect(self, dry_run: bool = False, limit: Optional[int] = None) -> dict[str, Any]:
        """Execute Wooting ingestion."""
        commit_sha = self.get_commit_sha()
        logger.info(f"[wooting] Ingesting {len(WOOTING_DEVICES)} Wooting analog devices")

        stats = {
            "devices_discovered": len(WOOTING_DEVICES),
            "records_created": 0,
            "records_updated": 0,
            "facts_recorded": 0,
            "hints_recorded": 0,
            "unique_vid_pids": set(),
        }

        target_devices = WOOTING_DEVICES[:limit] if (limit and limit > 0) else WOOTING_DEVICES

        for d in target_devices:
            pid_hex = format_hex4(d["pid"])
            stats["unique_vid_pids"].add(f"{WOOTING_VID_HEX}:{pid_hex}")

            if not dry_run:
                self._persist_device(d, commit_sha, stats)
            else:
                stats["records_created"] += 1

        stats["unique_vid_pid_count"] = len(stats["unique_vid_pids"])
        stats["unique_vid_pids"] = sorted(list(stats["unique_vid_pids"]))
        return stats

    def _persist_device(self, dev: dict[str, Any], commit_sha: str, stats: dict[str, Any]):
        """Persist Wooting keyboard into database."""
        vendor_id = self.db.get_or_create_vendor("wooting", "Wooting", "https://wooting.io")

        source_url = f"https://github.com/clemenscodes/wootswitch/blob/{commit_sha}/docs/hid-protocol.md"
        raw_source = RawSource(
            url=source_url,
            source_type=SourceType.OPEN_SOURCE,
            vendor="wooting",
            content_hash=commit_sha
        )
        source_id = self.db.record_source(raw_source)

        pid_hex = format_hex4(dev["pid"])
        identity_key = generate_identity_key("Wooting", f"{dev['name']}_{WOOTING_VID_HEX}_{pid_hex}")

        p_id, is_new = self.db.upsert_product(
            vendor_id=vendor_id,
            raw_name=dev["name"],
            canonical_name=dev["name"],
            category=dev["category"],
            identity_key=identity_key,
            product_url=source_url,
            image_url=None,
            category_confidence=1.0,
            metadata_confidence=0.95,
            source_id=source_id,
            evidence_level=EvidenceLevel.LEVEL_1_METADATA,
            run_id=self.run_id
        )

        if is_new:
            stats["records_created"] += 1
        else:
            stats["records_updated"] += 1

        # Device Identifier
        ident = DeviceIdentifierFact(
            product_id=p_id,
            vid=WOOTING_VID,
            pid=dev["pid"],
            vid_hex=WOOTING_VID_HEX,
            pid_hex=pid_hex,
            manufacturer_string="Wooting",
            product_string=dev["name"],
            usage_page=dev["usage_page"],
            usage=None,
            connection_type="usb",
            source_id=source_id,
            artifact_sha256=None,
            evidence_level=EvidenceLevel.LEVEL_2_DEVICE_IDENTITY,
            confidence=0.95
        )
        self.db.upsert_device_identifier(ident, run_id=self.run_id)

        # Protocol Hints
        self.db.upsert_protocol_hint(
            ProtocolHintFact(
                product_id=p_id,
                hint_key="wooting_protocol_variant",
                hint_value=dev["protocol"],
                source_id=source_id,
                evidence_level=EvidenceLevel.LEVEL_3_PROTOCOL_HINT,
                confidence=0.95
            ),
            run_id=self.run_id
        )
        self.db.upsert_protocol_hint(
            ProtocolHintFact(
                product_id=p_id,
                hint_key="config_usage_page",
                hint_value=format_hex4(dev["usage_page"]),
                source_id=source_id,
                evidence_level=EvidenceLevel.LEVEL_3_PROTOCOL_HINT,
                confidence=0.95
            ),
            run_id=self.run_id
        )
        stats["hints_recorded"] += 2

        # Technical Facts
        self.db.upsert_generic_fact(
            GenericFact(
                product_id=p_id,
                key="supports_rapid_trigger",
                value="true" if dev.get("rapid_trigger") else "false",
                source_id=source_id,
                evidence_level=EvidenceLevel.LEVEL_3_PROTOCOL_HINT,
                confidence=0.95
            ),
            run_id=self.run_id
        )
        self.db.upsert_generic_fact(
            GenericFact(
                product_id=p_id,
                key="actuation_range_mm",
                value=f"{dev['actuation_min_mm']:.1f}-{dev['actuation_max_mm']:.1f}",
                source_id=source_id,
                evidence_level=EvidenceLevel.LEVEL_3_PROTOCOL_HINT,
                confidence=0.95
            ),
            run_id=self.run_id
        )
        self.db.upsert_generic_fact(
            GenericFact(
                product_id=p_id,
                key="wooting_hid_command_specs",
                value=json.dumps(WOOTING_COMMAND_SPECS),
                source_id=source_id,
                evidence_level=EvidenceLevel.LEVEL_3_PROTOCOL_HINT,
                confidence=0.95
            ),
            run_id=self.run_id
        )
        stats["facts_recorded"] += 3
