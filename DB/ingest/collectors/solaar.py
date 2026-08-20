"""Solaar Logitech HID++ descriptor collector, feature table extractor, and settings miner.

Extracts:
1. Logitech devices registered in Solaar (descriptors.py):
   - Name, codename, kind, wpid (wireless PID), usbid (USB PID), btid (Bluetooth PID), protocol (HID++ 1.0, 2.0, 4.5)
2. Complete HID++ 2.0 Feature Table (hidpp20_constants.py):
   - Feature IDs (0x0000 - 0xFFFF), feature names, categories (battery, DPI, profiles, RGB, scrolling)
3. Safety metadata: DFU commands flagged destructive.

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

LOGITECH_VID = 0x046D
LOGITECH_VID_HEX = "0x046D"


@dataclass
class SolaarDevice:
    """Logitech device descriptor and HID++ capabilities."""
    name: str
    codename: Optional[str]
    category: str
    protocol: Optional[float]
    wpid: Optional[str]
    usbid: Optional[int]
    usbid_hex: Optional[str]
    btid: Optional[int]
    btid_hex: Optional[str]
    registers: list[str] = field(default_factory=list)
    supported_features: list[dict[str, Any]] = field(default_factory=list)
    source_file: str = "lib/logitech_receiver/descriptors.py"


class SolaarDescriptorParser:
    """Parses Solaar descriptor files and HID++ constants."""

    def __init__(self, repo_path: Path):
        self.repo_path = repo_path
        self.lib_dir = repo_path / "lib" / "logitech_receiver"

    def get_commit_sha(self) -> str:
        """Get git commit SHA."""
        if self.repo_path.exists() and (self.repo_path / ".git").exists():
            try:
                res = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(self.repo_path), capture_output=True, text=True, check=True)
                return res.stdout.strip()
            except Exception:
                pass
        return "solaar_source"

    def parse_hidpp_features(self) -> list[dict[str, Any]]:
        """Extract all SupportedFeature enum members."""
        features_file = self.lib_dir / "hidpp20_constants.py"
        if not features_file.exists():
            return []

        text = features_file.read_text(encoding="utf-8", errors="replace")
        re_feat = re.compile(r'^\s*([A-Za-z0-9_]+)\s*=\s*(0x[0-9A-Fa-fxX]+|\d+)', re.MULTILINE)

        in_enum = False
        features = []
        for line in text.splitlines():
            if "class SupportedFeature" in line:
                in_enum = True
                continue
            if in_enum and line.startswith("class "):
                break
            if in_enum:
                m = re_feat.match(line)
                if m:
                    f_name = m.group(1)
                    f_val_str = m.group(2)
                    f_val = parse_hex_or_dec(f_val_str)
                    if f_val is not None:
                        is_destructive = any(d_kw in f_name.lower() for d_kw in ["dfu", "reset", "flash", "bootloader"])
                        features.append({
                            "feature_name": f_name,
                            "feature_id": f_val,
                            "feature_id_hex": f"0x{f_val:04X}",
                            "destructive": is_destructive
                        })

        return features

    def parse_all_devices(self) -> list[SolaarDevice]:
        """Extract all Logitech device descriptors."""
        desc_file = self.lib_dir / "descriptors.py"
        if not desc_file.exists():
            return []

        features = self.parse_hidpp_features()
        text = desc_file.read_text(encoding="utf-8", errors="replace")

        # Parse _D(...) entries (excluding def _D)
        re_entry = re.compile(r'(?<!def )_D\s*\(([\s\S]*?)(?=\n\s*(?:_D\s*\(|def |DEVICES)|\Z)', re.MULTILINE)
        devices: list[SolaarDevice] = []

        for m in re_entry.finditer(text):
            body = m.group(1).strip()
            if not body or body.startswith("name,") or body.startswith("name="):
                continue

            name = self._extract_field(body, "name")
            if not name:
                # Check first positional string
                m_first = re.search(r'^\s*[\'"`]([^\'"`]+)[\'"`]', body)
                if m_first:
                    name = m_first.group(1)

            if not name:
                continue

            codename = self._extract_field(body, "codename")
            kind_str = self._extract_field(body, "kind")
            wpid = self._extract_field(body, "wpid")
            proto_str = self._extract_field(body, "protocol")
            usbid_str = self._extract_field(body, "usbid")
            btid_str = self._extract_field(body, "btid")

            protocol = float(proto_str) if proto_str and self._is_float(proto_str) else None
            usbid = self._parse_solaar_hex(usbid_str)
            btid = self._parse_solaar_hex(btid_str)

            # If wpid is present without usbid, and wpid looks hex, use as PID
            if not usbid and wpid and len(wpid) == 4:
                try:
                    usbid = int(wpid, 16)
                except ValueError:
                    pass

            category = self._resolve_category(name, kind_str)

            devices.append(SolaarDevice(
                name=name,
                codename=codename,
                category=category,
                protocol=protocol,
                wpid=wpid,
                usbid=usbid,
                usbid_hex=format_hex4(usbid) if usbid is not None else None,
                btid=btid,
                btid_hex=format_hex4(btid) if btid is not None else None,
                supported_features=features
            ))

        return devices

    def _parse_solaar_hex(self, val_str: Optional[str]) -> Optional[int]:
        if not val_str:
            return None
        val_str = val_str.strip().strip('\'"')
        if not val_str or val_str == "None":
            return None
        if val_str.startswith("0x") or val_str.startswith("0X"):
            try:
                return int(val_str, 16)
            except ValueError:
                return None
        try:
            return int(val_str, 16) if len(val_str) == 4 and all(c in "0123456789abcdefABCDEF" for c in val_str) else int(val_str)
        except ValueError:
            return None

    def _extract_field(self, body: str, field_name: str) -> Optional[str]:
        """Extract named argument from Python function call string."""
        m = re.search(rf'{field_name}\s*=\s*(?:[\'"`]([^\'"`]+)[\'"`]|([^\s,\)]+))', body)
        if m:
            val = m.group(1) or m.group(2)
            val = val.strip().strip('\'"')
            return val if val != "None" else None
        return None

    def _is_float(self, val: str) -> bool:
        try:
            float(val)
            return True
        except ValueError:
            return False

    def _resolve_category(self, name: str, kind_str: Optional[str]) -> str:
        """Map Solaar kind or device name to category."""
        target = f"{name} {kind_str or ''}".lower()
        if "mouse" in target or "trackball" in target:
            return "mouse"
        if "keyboard" in target or "numpad" in target:
            return "keyboard"
        if "touchpad" in target:
            return "touchpad"
        if "headset" in target or "g933" in target or "g533" in target:
            return "headset"
        return "other"


class SolaarCollector:
    """Ingests Solaar Logitech descriptors and HID++ 2.0 feature tables into SQLite."""

    def __init__(self, db: RegistryDatabase, repo_path: Path, run_id: str):
        self.db = db
        self.repo_path = repo_path
        self.run_id = run_id
        self.parser = SolaarDescriptorParser(repo_path)
        self.commit_sha = self.parser.get_commit_sha()
        self.repo_url = "https://github.com/pwr-Solaar/Solaar"

    def collect(self, dry_run: bool = False, limit: Optional[int] = None) -> dict[str, Any]:
        """Execute Solaar ingestion."""
        devices = self.parser.parse_all_devices()
        features = self.parser.parse_hidpp_features()
        logger.info(f"[solaar] Parsed {len(devices)} Logitech devices and {len(features)} HID++ features")

        stats = {
            "devices_discovered": len(devices),
            "records_created": 0,
            "records_updated": 0,
            "with_vid_pid": 0,
            "unique_vid_pids": set(),
            "facts_recorded": 0,
            "hints_recorded": 0,
            "features_recorded": len(features)
        }

        if limit and limit > 0:
            devices = devices[:limit]

        for d in devices:
            if d.usbid is not None:
                stats["with_vid_pid"] += 1
                stats["unique_vid_pids"].add(f"{LOGITECH_VID_HEX}:{d.usbid_hex}")

            if not dry_run:
                self._persist_device(d, stats)
            else:
                stats["records_created"] += 1

        stats["unique_vid_pid_count"] = len(stats["unique_vid_pids"])
        stats["unique_vid_pids"] = sorted(list(stats["unique_vid_pids"]))
        return stats

    def _persist_device(self, dev: SolaarDevice, stats: dict[str, Any]):
        """Persist Logitech device into database."""
        vendor_id = self.db.get_or_create_vendor("logitech", "Logitech", "https://www.logitech.com")

        source_url = f"{self.repo_url}/blob/{self.commit_sha}/{dev.source_file}"
        raw_source = RawSource(
            url=source_url,
            source_type=SourceType.OPEN_SOURCE,
            vendor="logitech",
            content_hash=self.commit_sha
        )
        source_id = self.db.record_source(raw_source)

        suffix = dev.usbid_hex or dev.wpid or dev.codename or "device"
        identity_key = generate_identity_key("Logitech", f"{dev.name}_{LOGITECH_VID_HEX}_{suffix}")

        p_id, is_new = self.db.upsert_product(
            vendor_id=vendor_id,
            raw_name=dev.name,
            canonical_name=dev.name,
            category=dev.category,
            identity_key=identity_key,
            product_url=source_url,
            image_url=None,
            category_confidence=1.0,
            metadata_confidence=0.90,
            source_id=source_id,
            evidence_level=EvidenceLevel.LEVEL_1_METADATA,
            run_id=self.run_id
        )

        if is_new:
            stats["records_created"] += 1
        else:
            stats["records_updated"] += 1

        # Device Identifier
        if dev.usbid is not None:
            ident = DeviceIdentifierFact(
                product_id=p_id,
                vid=LOGITECH_VID,
                pid=dev.usbid,
                vid_hex=LOGITECH_VID_HEX,
                pid_hex=dev.usbid_hex,
                manufacturer_string="Logitech",
                product_string=dev.name,
                usage_page=None,
                usage=None,
                connection_type="usb",
                source_id=source_id,
                artifact_sha256=None,
                evidence_level=EvidenceLevel.LEVEL_2_DEVICE_IDENTITY,
                confidence=0.90
            )
            self.db.upsert_device_identifier(ident, run_id=self.run_id)

        # Protocol Hints
        if dev.protocol:
            self.db.upsert_protocol_hint(
                ProtocolHintFact(
                    product_id=p_id,
                    # This is Solaar's per-device descriptor field.  It is not
                    # evidence of a separately published HID++ specification.
                    hint_key="solaar_device_protocol_field",
                    hint_value=f"{dev.protocol:.1f}",
                    source_id=source_id,
                    evidence_level=EvidenceLevel.LEVEL_3_PROTOCOL_HINT,
                    confidence=0.95
                ),
                run_id=self.run_id
            )
            stats["hints_recorded"] += 1

        if dev.codename:
            self.db.upsert_protocol_hint(
                ProtocolHintFact(
                    product_id=p_id,
                    hint_key="logitech_codename",
                    hint_value=dev.codename,
                    source_id=source_id,
                    evidence_level=EvidenceLevel.LEVEL_3_PROTOCOL_HINT,
                    confidence=0.95
                ),
                run_id=self.run_id
            )
            stats["hints_recorded"] += 1

        # Technical Facts
        if dev.wpid:
            self.db.upsert_generic_fact(
                GenericFact(
                    product_id=p_id,
                    key="logitech_wireless_pid",
                    value=dev.wpid,
                    source_id=source_id,
                    evidence_level=EvidenceLevel.LEVEL_2_DEVICE_IDENTITY,
                    confidence=0.95
                ),
                run_id=self.run_id
            )
            stats["facts_recorded"] += 1

        if dev.supported_features:
            self.db.upsert_generic_fact(
                GenericFact(
                    product_id=p_id,
                    key="hidpp20_feature_registry",
                    value=json.dumps(dev.supported_features),
                    source_id=source_id,
                    evidence_level=EvidenceLevel.LEVEL_3_PROTOCOL_HINT,
                    confidence=0.95
                ),
                run_id=self.run_id
            )
            stats["facts_recorded"] += 1
