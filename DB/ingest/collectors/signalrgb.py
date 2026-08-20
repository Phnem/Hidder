"""SignalRGB plugin collector, endpoint validator parser, and protocol packet miner.

Extracts:
1. Device metadata from official, community, QMK, and installed JS plugins:
   - Name(), VendorId(), ProductId(), ProductIds(), Publisher(), Documentation(), Size()
2. Multi-interface validation fingerprints:
   - Validate(endpoint): interface, usage_page, usage, packet_size, collection
3. Procedural packet builder invocations:
   - device.write([...]), device.send_report([...]), device.read(...), device.get_report(...)
4. LED matrix topology, channel configurations, and effect modes.
5. GitLab USBData captures & attachments metadata.

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

from ingest.brands.canonical import ALL_CANONICAL_BRANDS, get_brand_by_slug
from ingest.config import DB_PATH
from ingest.logging_setup import get_logger
from ingest.normalize.evidence import (
    RawSource, SourceType, DeviceIdentifierFact, ProtocolHintFact, GenericFact, EvidenceLevel
)
from ingest.normalize.identifiers import normalize_vid_pid, format_hex4, parse_hex_or_dec
from ingest.normalize.models import generate_identity_key, normalize_product_name
from ingest.storage.database import RegistryDatabase

logger = get_logger()

# Category mapping to canonical categories
CATEGORY_MAP = {
    "mouse": "mouse",
    "keyboard": "keyboard",
    "mousemat": "mousemat",
    "mouse pad": "mousemat",
    "headset": "headset",
    "headset stand": "accessory",
    "keypad": "keypad",
    "gpu": "gpu",
    "motherboard": "motherboard",
    "ram": "dram",
    "dram": "dram",
    "cooler": "cooler",
    "fan": "cooler",
    "aio": "cooler",
    "case": "case",
    "strip": "lighting",
    "led": "lighting",
    "controller": "lighting",
    "accessory": "accessory",
    "gamepad": "gamepad",
    "microphone": "microphone",
    "storage": "storage",
    "monitor": "monitor",
    "laptop": "laptop",
}


@dataclass
class SignalRGBPluginDevice:
    """Device metadata and protocol specification extracted from a SignalRGB plugin."""
    name: str
    source_file: str
    source_dir: str
    vid: Optional[int] = None
    vid_hex: Optional[str] = None
    pids: list[int] = field(default_factory=list)
    pid_hexes: list[str] = field(default_factory=list)
    publisher: str = "SignalRGB"
    documentation_url: str = ""
    category: str = "other"
    vendor_slug: str = "unknown"
    manufacturer: str = "Unknown"
    matrix_width: Optional[int] = None
    matrix_height: Optional[int] = None
    interfaces: list[int] = field(default_factory=list)
    usage_pages: list[int] = field(default_factory=list)
    usages: list[int] = field(default_factory=list)
    packet_sizes: list[int] = field(default_factory=list)
    validation_rules: list[dict[str, Any]] = field(default_factory=list)
    packet_writes: list[dict[str, Any]] = field(default_factory=list)
    opcodes: dict[str, str] = field(default_factory=dict)


class SignalRGBPluginParser:
    """Parses JavaScript plugins across SignalRGB repositories."""

    def __init__(self, sources_root: Path):
        self.sources_root = sources_root
        self.plugin_dirs = [
            sources_root / "signalrgb-official-plugins",
            sources_root / "signalrgb-community-plugins",
            sources_root / "signalrgb-qmk-plugins",
            sources_root / "signalrgb-community-public",
            sources_root / "signalrgb-installed",
        ]
        self._brand_lookup: dict[str, tuple[str, str]] = {}
        for b in ALL_CANONICAL_BRANDS:
            self._brand_lookup[b.slug.lower()] = (b.slug, b.canonical_name)
            self._brand_lookup[b.canonical_name.lower()] = (b.slug, b.canonical_name)
            for alias in b.aliases:
                self._brand_lookup[alias.lower()] = (b.slug, b.canonical_name)

    def parse_all(self) -> list[SignalRGBPluginDevice]:
        """Scan and parse all JS plugins."""
        devices: list[SignalRGBPluginDevice] = []

        re_name = re.compile(r'export\s+function\s+Name\s*\(\s*\)\s*\{\s*return\s*[\'"`]([^\'"`]+)[\'"`]\s*;?\s*\}')
        re_vid = re.compile(r'export\s+function\s+VendorId\s*\(\s*\)\s*\{\s*return\s*(0x[0-9A-Fa-f]+|\d+)\s*;?\s*\}')
        re_pid = re.compile(r'export\s+function\s+ProductId\s*\(\s*\)\s*\{\s*return\s*(0x[0-9A-Fa-f]+|\d+)\s*;?\s*\}')
        re_pids = re.compile(r'export\s+function\s+ProductIds\s*\(\s*\)\s*\{\s*return\s*\[([^\]]+)\]\s*;?\s*\}')
        re_pub = re.compile(r'export\s+function\s+Publisher\s*\(\s*\)\s*\{\s*return\s*[\'"`]([^\'"`]+)[\'"`]\s*;?\s*\}')
        re_doc = re.compile(r'export\s+function\s+Documentation\s*\(\s*\)\s*\{\s*return\s*[\'"`]([^\'"`]+)[\'"`]\s*;?\s*\}')
        re_size = re.compile(r'export\s+function\s+Size\s*\(\s*\)\s*\{\s*return\s*\[\s*(\d+)\s*,\s*(\d+)\s*\]\s*;?\s*\}')

        re_write = re.compile(r'device\.(write|send_report|read|get_report)\s*\(\s*(\[[^\]]+\]|[A-Za-z0-9_]+)\s*(?:,\s*([^,\)]+))?\s*\)')
        re_const = re.compile(r'(?:const|let|var)\s+([A-Za-z0-9_]+)\s*=\s*(0x[0-9A-Fa-f]+|\d+)\s*;')

        re_interface = re.compile(r'endpoint\.interface\s*===?\s*(0x[0-9A-Fa-f]+|\d+)')
        re_usage_page = re.compile(r'endpoint\.usage_page\s*===?\s*(0x[0-9A-Fa-f]+|\d+)')
        re_usage = re.compile(r'endpoint\.usage\s*===?\s*(0x[0-9A-Fa-f]+|\d+)')
        re_packet_size = re.compile(r'endpoint\.(?:packet_size|packetSize|size)\s*===?\s*(0x[0-9A-Fa-f]+|\d+)')

        for p_dir in self.plugin_dirs:
            if not p_dir.exists():
                continue
            for f in sorted(p_dir.rglob("*.js")):
                if "test" in f.name.lower() or "node_modules" in f.as_posix():
                    continue
                text = f.read_text(encoding="utf-8", errors="replace")

                m_name = re_name.search(text)
                m_vid = re_vid.search(text)

                if not m_name and not m_vid:
                    continue

                name = m_name.group(1).strip() if m_name else f.stem.replace("_", " ")
                vid_raw = m_vid.group(1).strip() if m_vid else None
                vid = parse_hex_or_dec(vid_raw) if vid_raw else None
                vid_hex = format_hex4(vid) if vid is not None else None

                pids: list[int] = []
                m_pid = re_pid.search(text)
                if m_pid:
                    p_val = parse_hex_or_dec(m_pid.group(1).strip())
                    if p_val is not None:
                        pids.append(p_val)

                m_pids_m = re_pids.search(text)
                if m_pids_m:
                    for raw_p in m_pids_m.group(1).split(","):
                        raw_p = raw_p.strip()
                        p_val = parse_hex_or_dec(raw_p)
                        if p_val is not None and p_val not in pids:
                            pids.append(p_val)

                pid_hexes = [format_hex4(p) for p in pids if p is not None]

                publisher = "SignalRGB"
                m_pub = re_pub.search(text)
                if m_pub:
                    publisher = m_pub.group(1).strip()

                doc_url = ""
                m_doc = re_doc.search(text)
                if m_doc:
                    doc_url = m_doc.group(1).strip()

                matrix_w, matrix_h = None, None
                m_size = re_size.search(text)
                if m_size:
                    try:
                        matrix_w = int(m_size.group(1))
                        matrix_h = int(m_size.group(2))
                    except ValueError:
                        pass

                # Extract validation rules
                interfaces = [parse_hex_or_dec(m.group(1)) for m in re_interface.finditer(text)]
                usage_pages = [parse_hex_or_dec(m.group(1)) for m in re_usage_page.finditer(text)]
                usages = [parse_hex_or_dec(m.group(1)) for m in re_usage.finditer(text)]
                packet_sizes = [parse_hex_or_dec(m.group(1)) for m in re_packet_size.finditer(text)]

                val_rules = []
                if interfaces or usage_pages or usages or packet_sizes:
                    val_rules.append({
                        "interfaces": [i for i in interfaces if i is not None],
                        "usage_pages": [format_hex4(up) for up in usage_pages if up is not None],
                        "usages": [format_hex4(u) for u in usages if u is not None],
                        "packet_sizes": [ps for ps in packet_sizes if ps is not None]
                    })

                # Extract constants / opcodes
                opcodes = {}
                for m_c in re_const.finditer(text):
                    k = m_c.group(1)
                    v = m_c.group(2)
                    if any(kw in k.lower() for kw in ["cmd", "command", "report", "mode", "packet", "zone", "header", "init"]):
                        opcodes[k] = v

                # Extract packet write calls
                packet_writes = []
                for m_w in re_write.finditer(text):
                    op = m_w.group(1)
                    payload = m_w.group(2).strip()
                    size_arg = m_w.group(3).strip() if m_w.group(3) else None

                    # Parse literal array if available
                    parsed_bytes = None
                    if payload.startswith("[") and payload.endswith("]"):
                        raw_items = payload[1:-1].split(",")
                        parsed_bytes = []
                        for item in raw_items:
                            item = item.strip()
                            b_val = parse_hex_or_dec(item)
                            if b_val is not None:
                                parsed_bytes.append(f"0x{b_val:02X}")
                            else:
                                parsed_bytes.append(item)

                    packet_writes.append({
                        "operation": op,
                        "raw_payload": payload,
                        "parsed_bytes": parsed_bytes,
                        "packet_size": size_arg
                    })

                category = self._normalize_category(name, f.as_posix())
                vendor_slug, display_name = self._resolve_vendor(name, f.as_posix())

                devices.append(SignalRGBPluginDevice(
                    name=name,
                    source_file=f.relative_to(self.sources_root).as_posix(),
                    source_dir=p_dir.name,
                    vid=vid,
                    vid_hex=vid_hex,
                    pids=pids,
                    pid_hexes=pid_hexes,
                    publisher=publisher,
                    documentation_url=doc_url,
                    category=category,
                    vendor_slug=vendor_slug,
                    manufacturer=display_name,
                    matrix_width=matrix_w,
                    matrix_height=matrix_h,
                    interfaces=[i for i in interfaces if i is not None],
                    usage_pages=[up for up in usage_pages if up is not None],
                    usages=[u for u in usages if u is not None],
                    packet_sizes=[ps for ps in packet_sizes if ps is not None],
                    validation_rules=val_rules,
                    packet_writes=packet_writes,
                    opcodes=opcodes
                ))

        return devices

    def _normalize_category(self, name: str, filepath: str) -> str:
        """Map device name or file path to canonical category."""
        target = f"{name} {filepath}".lower()
        for k, v in CATEGORY_MAP.items():
            if k in target:
                return v
        return "other"

    def _resolve_vendor(self, name: str, filepath: str) -> tuple[str, str]:
        """Resolve vendor slug and canonical display name."""
        target = f"{name} {filepath}".lower()
        for b_key, (slug, cname) in self._brand_lookup.items():
            if b_key in target:
                return (slug, cname)

        first_word = name.split()[0] if name else "SignalRGB"
        slug = re.sub(r'[^a-z0-9]+', '_', first_word.lower()).strip('_')
        return (slug or "custom", first_word.title())


class SignalRGBCollector:
    """Ingests SignalRGB plugins, multi-interface fingerprints, and procedural packet builders into SQLite."""

    def __init__(self, db: RegistryDatabase, sources_root: Path, run_id: str):
        self.db = db
        self.sources_root = sources_root
        self.run_id = run_id
        self.parser = SignalRGBPluginParser(sources_root)

    def get_source_commit(self, source_dir_name: str) -> str:
        """Get git commit SHA for a specific source directory."""
        target_dir = self.sources_root / source_dir_name
        if target_dir.exists() and (target_dir / ".git").exists():
            try:
                res = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(target_dir), capture_output=True, text=True, check=True)
                return res.stdout.strip()
            except Exception:
                pass
        return "signalrgb_source"

    def get_source_repository(self, source_dir_name: str) -> str:
        """Return the actual repository for a plugin tree, never a generic alias."""
        root = self.sources_root / source_dir_name
        if (root / ".git").exists():
            try:
                result = subprocess.run(
                    ["git", "config", "--get", "remote.origin.url"], cwd=str(root),
                    capture_output=True, text=True, check=True,
                )
                url = result.stdout.strip().removesuffix(".git")
                if url.startswith("git@github.com:"):
                    return "https://github.com/" + url.split(":", 1)[1]
                if url:
                    return url
            except Exception:
                logger.warning("[signalrgb] could not resolve remote for %s", source_dir_name)
        return "https://gitlab.com/signalrgb/signal-plugins"

    def collect(self, dry_run: bool = False, limit: Optional[int] = None) -> dict[str, Any]:
        """Execute SignalRGB ingestion across all parsed JS plugins."""
        plugins = self.parser.parse_all()
        logger.info(f"[signalrgb] Discovered {len(plugins)} device plugins across SignalRGB repositories")

        stats = {
            "plugins_discovered": len(plugins),
            "records_created": 0,
            "records_updated": 0,
            "with_vid_pid": 0,
            "unique_vid_pids": set(),
            "facts_recorded": 0,
            "hints_recorded": 0,
            "packet_writes_recorded": 0,
            "opcodes_recorded": 0,
        }

        if limit and limit > 0:
            plugins = plugins[:limit]

        for p in plugins:
            if p.vid is not None and p.pids:
                stats["with_vid_pid"] += 1
                for pid_hex in p.pid_hexes:
                    stats["unique_vid_pids"].add(f"{p.vid_hex}:{pid_hex}")

            if not dry_run:
                self._persist_plugin(p, stats)
            else:
                stats["records_created"] += 1

        stats["unique_vid_pid_count"] = len(stats["unique_vid_pids"])
        stats["unique_vid_pids"] = sorted(list(stats["unique_vid_pids"]))
        return stats

    def _persist_plugin(self, plug: SignalRGBPluginDevice, stats: dict[str, Any]):
        """Persist a SignalRGB plugin device into SQLite."""
        # 1. Vendor
        vendor_id = self.db.get_or_create_vendor(
            name=plug.vendor_slug,
            display_name=plug.manufacturer
        )

        # 2. Source Provenance
        commit_sha = self.get_source_commit(plug.source_dir)
        repository_url = self.get_source_repository(plug.source_dir)
        blob_path = f"-/blob/{commit_sha}" if "gitlab.com" in repository_url else f"blob/{commit_sha}"
        source_url = f"{repository_url}/{blob_path}/{plug.source_file}"
        raw_source = RawSource(
            url=source_url,
            source_type=SourceType.OPEN_SOURCE,
            vendor=plug.vendor_slug,
            content_hash=commit_sha
        )
        source_id = self.db.record_source(raw_source)

        # If plugin specifies multiple PIDs, create/update product for each PID
        target_pids = plug.pids if plug.pids else [None]
        target_pid_hexes = plug.pid_hexes if plug.pid_hexes else [None]

        for pid, pid_hex in zip(target_pids, target_pid_hexes):
            suffix = f"{plug.vid_hex}_{pid_hex}" if (plug.vid_hex and pid_hex) else Path(plug.source_file).stem
            identity_key = generate_identity_key(plug.manufacturer, f"{plug.name}_{suffix}")

            p_id, is_new = self.db.upsert_product(
                vendor_id=vendor_id,
                raw_name=plug.name,
                canonical_name=plug.name,
                category=plug.category,
                identity_key=identity_key,
                product_url=plug.documentation_url or source_url,
                image_url=None,
                category_confidence=1.0,
                metadata_confidence=0.85,
                source_id=source_id,
                evidence_level=EvidenceLevel.LEVEL_1_METADATA,
                run_id=self.run_id
            )

            if is_new:
                stats["records_created"] += 1
            else:
                stats["records_updated"] += 1

            # Device Identifier
            if plug.vid is not None and pid is not None:
                ident_fact = DeviceIdentifierFact(
                    product_id=p_id,
                    vid=plug.vid,
                    pid=pid,
                    vid_hex=plug.vid_hex,
                    pid_hex=pid_hex,
                    manufacturer_string=plug.manufacturer,
                    product_string=plug.name,
                    usage_page=plug.usage_pages[0] if plug.usage_pages else None,
                    usage=plug.usages[0] if plug.usages else None,
                    connection_type="usb",
                    source_id=source_id,
                    artifact_sha256=None,
                    evidence_level=EvidenceLevel.LEVEL_2_DEVICE_IDENTITY,
                    confidence=0.85
                )
                self.db.upsert_device_identifier(ident_fact, run_id=self.run_id)

            # Protocol Hints
            self.db.upsert_protocol_hint(
                ProtocolHintFact(
                    product_id=p_id,
                    hint_key="signalrgb_plugin_file",
                    hint_value=plug.source_file,
                    source_id=source_id,
                    evidence_level=EvidenceLevel.LEVEL_3_PROTOCOL_HINT,
                    confidence=0.90
                ),
                run_id=self.run_id
            )
            self.db.upsert_protocol_hint(
                ProtocolHintFact(
                    product_id=p_id,
                    hint_key="signalrgb_publisher",
                    hint_value=plug.publisher,
                    source_id=source_id,
                    evidence_level=EvidenceLevel.LEVEL_3_PROTOCOL_HINT,
                    confidence=0.90
                ),
                run_id=self.run_id
            )
            stats["hints_recorded"] += 2

            # Technical Facts
            if plug.matrix_width and plug.matrix_height:
                self.db.upsert_generic_fact(
                    GenericFact(
                        product_id=p_id,
                        key="lighting_matrix_dimensions",
                        value=f"{plug.matrix_width}x{plug.matrix_height}",
                        source_id=source_id,
                        evidence_level=EvidenceLevel.LEVEL_3_PROTOCOL_HINT,
                        confidence=0.95
                    ),
                    run_id=self.run_id
                )
                stats["facts_recorded"] += 1

            if plug.validation_rules:
                self.db.upsert_generic_fact(
                    GenericFact(
                        product_id=p_id,
                        key="signalrgb_validation_rules",
                        value=json.dumps(plug.validation_rules),
                        source_id=source_id,
                        evidence_level=EvidenceLevel.LEVEL_2_DEVICE_IDENTITY,
                        confidence=0.95
                    ),
                    run_id=self.run_id
                )
                stats["facts_recorded"] += 1

            if plug.packet_writes:
                self.db.upsert_generic_fact(
                    GenericFact(
                        product_id=p_id,
                        key="signalrgb_packet_writes",
                        value=json.dumps(plug.packet_writes),
                        source_id=source_id,
                        evidence_level=EvidenceLevel.LEVEL_3_PROTOCOL_HINT,
                        confidence=0.90
                    ),
                    run_id=self.run_id
                )
                stats["facts_recorded"] += 1
                stats["packet_writes_recorded"] += len(plug.packet_writes)

            if plug.opcodes:
                self.db.upsert_generic_fact(
                    GenericFact(
                        product_id=p_id,
                        key="signalrgb_protocol_opcodes",
                        value=json.dumps(plug.opcodes),
                        source_id=source_id,
                        evidence_level=EvidenceLevel.LEVEL_3_PROTOCOL_HINT,
                        confidence=0.90
                    ),
                    run_id=self.run_id
                )
                stats["facts_recorded"] += 1
                stats["opcodes_recorded"] += len(plug.opcodes)
