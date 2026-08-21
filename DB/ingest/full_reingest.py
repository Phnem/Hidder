"""Deterministic full-source typed protocol re-ingestion.

The reprocessor classifies every relevant file and emits only evidence-backed
typed entities.  It never performs device I/O and never promotes operations to
production-safe or hardware-verified states.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import subprocess
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import dpkt
import json5
from tree_sitter_language_pack import get_parser

from ingest.storage.database import RegistryDatabase


COLLECTOR_VERSION = "full-typed-reingest/1"
CODE_LANGUAGES = {
    ".c": "c", ".h": "c", ".cc": "cpp", ".cpp": "cpp", ".hpp": "cpp",
    ".cs": "c_sharp", ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".py": "python", ".rs": "rust",
}
CAPTURE_SUFFIXES = {".pcap", ".pcapng"}
DOC_SUFFIXES = {".md", ".rst", ".txt"}
DATA_SUFFIXES = {".json", ".yaml", ".yml", ".toml", ".ini", ".device", ".mk"}
FINAL_STATUSES = {
    "parsed_protocol_data", "parsed_identity_only", "parsed_metadata_only",
    "parsed_no_relevant_facts", "duplicate_or_derived", "test_fixture",
    "documentation_only", "capture_parsed", "unsupported_format", "parse_failed",
}

TRUST_BY_ROOT = {
    "artemis": "IndependentImplementation",
    "artemis-plugins": "CommunityImplementation",
    "ckb-next": "IndependentImplementation",
    "corsair-protocol": "ReverseEngineeredImplementation",
    "g933-utils": "ReverseEngineeredImplementation",
    "hidpp-cvuchener": "ReverseEngineeredImplementation",
    "rgb-net": "IndependentImplementation",
    "openrazer": "OfficialVendorImplementation",
    "solaar": "IndependentImplementation",
    "linux": "KernelImplementation",
    "libratbag": "UpstreamImplementation",
    "data-libratbag": "UpstreamImplementation",
    "openrgb": "IndependentImplementation",
    "data-openrgb": "IndependentImplementation",
    "rivalcfg": "ReverseEngineeredImplementation",
    "ckb-next": "IndependentImplementation",
    "corsair-protocol": "ReverseEngineeredImplementation",
    "logitech-cpg-docs": "OfficialSpecification",
    "wooting-rgb-sdk": "OfficialSDK",
    "wooting-analog-sdk": "OfficialSDK",
    "wootswitch": "ReverseEngineeredImplementation",
    "g933-utils": "ReverseEngineeredImplementation",
    "signalrgb-official-plugins": "OfficialVendorImplementation",
    "signalrgb-community-plugins": "CommunityImplementation",
    "signalrgb-community-public": "CommunityImplementation",
    "signalrgb-qmk-community-module": "CommunityImplementation",
    "signalrgb-qmk-firmware-research": "CommunityClaim",
    "signalrgb-qmk-plugins": "CommunityImplementation",
    "reddit-openrgb": "CommunityClaim",
    "protocol-miner-inbox": "CommunityObservedRuntime",
    "protocol-miner-workspace": "CommunityObservedRuntime",
    "protocol-miner-reports": "CommunityObservedRuntime",
    "extracted-artifacts": "CatalogMetadata",
    "cas-extracted": "CatalogMetadata",
    "artifact-cas": "CatalogMetadata",
    "signalrgb-usbdata": "CommunityClaim",
    "signalrgb-usbdata-attachments": "CommunityCapture",
    "qmk_firmware": "UpstreamImplementation",
    "data-qmk_firmware": "UpstreamImplementation",
}


@dataclass(frozen=True)
class SourceRoot:
    name: str
    path: Path
    repository_url: str | None
    commit_sha: str | None
    branch: str | None
    trust: str


def _run_git(root: Path, *args: str) -> str | None:
    if not (root / ".git").exists():
        return None
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _files(root: Path) -> Iterable[Path]:
    for directory, children, names in os.walk(root):
        children[:] = sorted(name for name in children if name not in {".git", ".hg", ".svn", ".venv", "node_modules", "__pycache__", ".pytest_cache"})
        for name in sorted(names):
            yield Path(directory) / name


def _is_relevant(root_name: str, relative: str) -> bool:
    path = Path(relative)
    suffix = path.suffix.lower()
    low = relative.lower()
    if any(part in low for part in ("/vendor/", "/third_party/", "/third-party/", "/build/", "/dist/")):
        return False
    if suffix in CAPTURE_SUFFIXES:
        return True
    if root_name == "linux":
        return low.startswith("drivers/hid/") and suffix in CODE_LANGUAGES
    if root_name.startswith("signalrgb"):
        return suffix in {".js", ".json", ".pcap", ".pcapng"}
    if root_name in {"data-qmk_firmware", "qmk_firmware"}:
        if suffix == ".json":
            return Path(low).name in {"info.json", "keyboard.json", "via.json", "vial.json"}
        if Path(low).name in {"config.h", "rules.mk", "halconf.h", "mcuconf.h"} and "keyboards/" in low:
            return True
        protocol_tokens = ("quantum/", "protocol/", "tmk_core/", "raw_hid", "/via", "/vial", "usb", "hid", "bluetooth", "serial")
        return suffix in {".c", ".h", ".mk"} and any(token in low for token in protocol_tokens)
    if suffix in CODE_LANGUAGES or suffix in DATA_SUFFIXES:
        return True
    return suffix in DOC_SUFFIXES and any(token in low for token in ("protocol", "usb", "hid", "report", "command", "reverse", "notes/"))


def _scope(root_name: str, relative: str) -> tuple[str, str]:
    root_key = re.sub(r"[^a-z0-9]+", "-", root_name.lower()).strip("-")
    # A repository is evidence provenance, not a protocol family.  Conservatively
    # scope extracted contracts to the declaring module/file until durable shared
    # protocol lineage proves that multiple modules are one family.
    module = str(Path(relative).with_suffix(""))
    module_key = re.sub(r"[^a-z0-9]+", "-", module.lower()).strip("-")
    family = f"{root_key}:{module_key}" if module_key else root_key
    return "protocol_family", f"family:{family}"


def _semantic_from_name(name: str) -> str | None:
    low = name.lower()
    mappings = (
        ("dpi", "mouse.dpi"), ("poll", "mouse.polling_rate"), ("battery", "battery.status"),
        ("sidetone", "audio.sidetone"), ("equalizer", "audio.equalizer"), ("eq", "audio.equalizer"),
        ("brightness", "lighting.brightness"), ("color", "lighting.color"), ("rgb", "lighting.color"),
        ("led", "lighting.update"), ("update", "lighting.update"),
        ("profile", "profile.management"), ("actuation", "keyboard.actuation"),
        ("rapid_trigger", "keyboard.rapid_trigger"), ("firmware", "firmware.operation"),
        ("dfu", "firmware.dfu"), ("reset", "device.reset"), ("mode", "device.mode"),
        ("handshake", "protocol.initialize"), ("initialize", "protocol.initialize"),
        ("initialise", "protocol.initialize"), ("init", "protocol.initialize"),
        ("commit", "protocol.commit"), ("apply", "protocol.commit"),
        ("readback", "protocol.readback"), ("query", "protocol.readback"),
    )
    return next((semantic for token, semantic in mappings if token in low), None)


class FullTypedReprocessor:
    def __init__(self, db_path: Path, workspace: Path):
        self.db_path = db_path.resolve()
        self.workspace = workspace.resolve()
        self.db = RegistryDatabase(self.db_path)
        self.parsers: dict[str, Any] = {}
        self.content_owner: dict[str, tuple[int, int]] = {}
        self.stats: Counter[str] = Counter()

    def connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def discover_roots(self) -> list[SourceRoot]:
        candidates: list[tuple[str, Path]] = []
        sources = self.workspace / "sources"
        if sources.is_dir():
            candidates.extend((p.name, p) for p in sorted(sources.iterdir()) if p.is_dir())
        for name in ("qmk_firmware", "libratbag", "openrgb"):
            path = self.workspace / "data" / name
            if path.is_dir():
                candidates.append((f"data-{name}", path))
        for name, path in (
            ("signalrgb-usbdata-attachments", self.workspace / "data" / "signalrgb-usbdata"),
            ("extracted-artifacts", self.workspace / "extracted"),
            ("cas-extracted", self.workspace / "artifacts" / "extracted"),
            ("artifact-cas", self.workspace / "artifacts"),
            ("protocol-miner-inbox", self.workspace / "protocol-miner" / "inbox"),
            ("protocol-miner-workspace", self.workspace / "protocol-miner" / "workspace"),
            ("protocol-miner-reports", self.workspace / "protocol-miner" / "reports"),
        ):
            if path.is_dir() and any(_files(path)):
                candidates.append((name, path))
        roots: list[SourceRoot] = []
        for name, path in candidates:
            remote = _run_git(path, "config", "--get", "remote.origin.url")
            if remote:
                remote = remote.removesuffix(".git")
            trust = TRUST_BY_ROOT.get(name, "CommunityImplementation" if "community" in name else "Unknown")
            roots.append(SourceRoot(name, path, remote, _run_git(path, "rev-parse", "HEAD"), _run_git(path, "branch", "--show-current"), trust))
        return roots

    def reset_typed_derivatives(self) -> None:
        with self.connection() as conn:
            for table in (
                "command_risks", "device_reconstructibility", "operation_completeness",
                "protocol_sequence_steps", "operation_evidence", "protocol_sequences",
                "capture_transactions", "capture_files", "runtime_observations", "packet_fields",
                "packet_layouts", "typed_fact_evidence", "typed_facts", "device_protocol_mappings",
                "protocol_operations", "protocol_families", "struct_validations", "source_lineage", "source_files", "source_roots",
            ):
                conn.execute(f"DELETE FROM {table}")

    def prime_content_cache_from_inventory(self) -> int:
        """Reuse hashes from the immediately preceding audited inventory."""
        inserted = 0
        with self.connection() as conn:
            rows = conn.execute("""SELECT sr.local_path,sf.relative_path,sf.size,sf.content_hash
                FROM source_files sf JOIN source_roots sr ON sr.id=sf.source_root_id""").fetchall()
            for row in rows:
                path = Path(row["local_path"]) / Path(row["relative_path"])
                try:
                    stat = path.stat()
                except OSError:
                    continue
                if stat.st_size != row["size"]:
                    continue
                conn.execute("""INSERT OR IGNORE INTO source_content_cache(absolute_path,size,mtime_ns,sha256)
                                VALUES(?,?,?,?)""",
                             (str(path.resolve()), stat.st_size, stat.st_mtime_ns, row["content_hash"]))
                inserted += 1
        return inserted

    def _record_root(self, conn: sqlite3.Connection, root: SourceRoot) -> int:
        license_file = next((p for p in root.path.iterdir() if p.is_file() and p.name.lower() in {"license", "license.md", "copying", "copying.md"}), None)
        conn.execute(
            """INSERT INTO source_roots(root_name,local_path,repository_url,commit_sha,branch,license_file,license_text,root_content_hash,source_kind,audit_status,trust_class,lineage_group,collector_version)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (root.name, str(root.path), root.repository_url, root.commit_sha, root.branch,
             license_file.name if license_file else None,
             license_file.read_text(encoding="utf-8", errors="replace")[:4000] if license_file else None,
             root.commit_sha, "repository" if root.commit_sha else "archive_or_api",
             "verified_git" if root.commit_sha else "immutable_file_hash_only", root.trust, root.name, COLLECTOR_VERSION),
        )
        root_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute("INSERT INTO source_lineage(child_source_root_id,parent_source_root_id,relationship,rationale) VALUES(?,NULL,'unknown','no durable derivation proof')", (root_id,))
        return root_id

    def _content_hash(self, conn: sqlite3.Connection, root: SourceRoot, path: Path) -> str:
        if root.name == "artifact-cas" and re.fullmatch(r"[0-9a-fA-F]{64}", path.name):
            return path.name.lower()
        stat = path.stat()
        absolute = str(path.resolve())
        cached = conn.execute(
            "SELECT sha256 FROM source_content_cache WHERE absolute_path=? AND size=? AND mtime_ns=?",
            (absolute, stat.st_size, stat.st_mtime_ns),
        ).fetchone()
        if cached:
            self.stats["hash_cache_hits"] += 1
            return cached[0]
        value = _sha256(path)
        conn.execute("""INSERT OR REPLACE INTO source_content_cache(absolute_path,size,mtime_ns,sha256,verified_at)
                        VALUES(?,?,?,?,CURRENT_TIMESTAMP)""", (absolute, stat.st_size, stat.st_mtime_ns, value))
        self.stats["hash_cache_misses"] += 1
        return value

    def _insert_typed_fact(self, conn: sqlite3.Connection, fact_type: str, scope_type: str, scope_key: str,
                           semantic: str, key: str, value: Any, source_file_id: int,
                           trust: str, lineage: str, line: int | None, symbol: str | None,
                           method: str, confidence: float = 0.8) -> int:
        canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        value_hash = hashlib.sha256(canonical.encode()).hexdigest()
        conn.execute(
            """INSERT OR IGNORE INTO typed_facts(fact_type,scope_type,scope_key,semantic_type,canonical_key,canonical_value_json,value_hash,confidence)
               VALUES(?,?,?,?,?,?,?,?)""",
            (fact_type, scope_type, scope_key, semantic, key, canonical, value_hash, confidence),
        )
        fact_id = conn.execute(
            "SELECT id FROM typed_facts WHERE fact_type=? AND scope_type=? AND scope_key=? AND semantic_type=? AND canonical_key=? AND value_hash=?",
            (fact_type, scope_type, scope_key, semantic, key, value_hash),
        ).fetchone()[0]
        conn.execute(
            """INSERT OR IGNORE INTO typed_fact_evidence(typed_fact_id,source_file_id,line_start,line_end,symbol,extraction_method,trust_class,lineage_group,confidence,provenance_status)
               VALUES(?,?,?,?,?,?,?,?,?,'exact_file')""",
            (fact_id, source_file_id, line, line, symbol, method, trust, lineage, confidence),
        )
        return fact_id

    def _parser(self, language: str):
        if language not in self.parsers:
            self.parsers[language] = get_parser(language)
        return self.parsers[language]

    def _parse_code(self, conn: sqlite3.Connection, root: SourceRoot, source_file_id: int,
                    relative: str, data: bytes) -> tuple[str, Counter[str], str | None]:
        suffix = Path(relative).suffix.lower()
        language = CODE_LANGUAGES[suffix]
        tree = self._parser(language).parse(data)
        text = data.decode("utf-8", errors="replace")
        scope_type, scope_key = _scope(root.name, relative)
        counts: Counter[str] = Counter()

        # Explicit constants remain constants; they never create operations.
        const_pattern = re.compile(
            r"(?:#define\s+|(?:const|static\s+final|public\s+const|enum\s+)?[A-Za-z0-9_<>,\[\]\s]+\s+)?"
            r"(?P<name>[A-Za-z0-9_]*(?:CMD|COMMAND|OPCODE|REPORT_ID|FEATURE_ID)[A-Za-z0-9_]*)\s*(?:=|\s)\s*(?P<value>0x[0-9A-Fa-f]+|\d+)"
        )
        for match in const_pattern.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            self._insert_typed_fact(conn, "ProtocolConstant", scope_type, scope_key, "protocol.constant",
                                    match.group("name"), {"value": match.group("value")}, source_file_id,
                                    root.trust, root.name, line, match.group("name"), f"tree_sitter_{language}+constant")
            counts["facts"] += 1

        # Packet-like C/C++ structs with upstream/static size evidence.
        if language in {"c", "cpp"}:
            struct_pattern = re.compile(r"(?:typedef\s+)?struct\s+(?P<name>[A-Za-z0-9_]+)?\s*\{(?P<body>.*?)\}\s*(?P<alias>[A-Za-z0-9_]+)?\s*;", re.DOTALL)
            static_sizes = {m.group(1): int(m.group(2)) for m in re.finditer(r"static_assert\s*\(\s*sizeof\s*\(\s*(?:struct\s+)?([A-Za-z0-9_]+)\s*\)\s*==\s*(\d+)", text)}
            type_sizes = {"u8": 1, "uint8_t": 1, "unsigned char": 1, "char": 1, "__be16": 2, "u16": 2, "uint16_t": 2, "u32": 4, "uint32_t": 4}
            struct_matches = list(struct_pattern.finditer(text))
            # Static assertions are valid upstream compiler evidence for nested
            # types even when a helper struct itself is not packet-like.
            known_struct_sizes = dict(static_sizes)
            field_pattern = re.compile(r"\b(?P<type>(?:struct\s+)?[A-Za-z_][A-Za-z0-9_]*(?:\s+char)?)\s+(?P<name>[A-Za-z0-9_]+)(?:\[(?P<n>\d+)\])?\s*;")
            for match in struct_matches:
                name = match.group("name") or match.group("alias") or "anonymous"
                if not any(token in name.lower() for token in ("report", "packet", "message", "command", "request", "response")):
                    continue
                fields, offset, resolved = [], 0, True
                for field in field_pattern.finditer(match.group("body")):
                    normalized_type = re.sub(r"\s+", " ", field.group("type")).removeprefix("struct ")
                    base = type_sizes.get(normalized_type, known_struct_sizes.get(normalized_type))
                    if base is None:
                        resolved = False; continue
                    size = base * int(field.group("n") or 1)
                    fields.append((field.group("name"), offset, size, field.group("type")))
                    offset += size
                upstream = static_sizes.get(name)
                validation = "validated_static_assert" if upstream is not None and resolved and offset == upstream else ("partially_validated" if fields else "unresolved")
                conn.execute(
                    """INSERT OR IGNORE INTO packet_layouts(scope_type,scope_key,layout_name,struct_size,wire_length,endianness,validation_status,source_file_id)
                       VALUES(?,?,?,?,?,?,?,?)""",
                    (scope_type, scope_key, name, upstream if upstream is not None else (offset if resolved and fields else None), None, "source_declared", validation, source_file_id),
                )
                layout_id = conn.execute("SELECT id FROM packet_layouts WHERE scope_type=? AND scope_key=? AND layout_name=? AND variant='default' AND source_file_id=?", (scope_type, scope_key, name, source_file_id)).fetchone()[0]
                for field_name, field_offset, size, field_type in fields:
                    conn.execute("INSERT OR IGNORE INTO packet_fields(packet_layout_id,field_name,byte_offset,size_bytes,field_type,dynamic) VALUES(?,?,?,?,?,0)", (layout_id, field_name, field_offset, size, field_type))
                counts["layouts"] += 1
                if upstream is not None:
                    struct_status = "validated" if validation == "validated_static_assert" else ("unverified" if not resolved else "mismatch")
                    conn.execute("INSERT OR REPLACE INTO struct_validations(source_root_id,source_path,struct_name,calculated_size,upstream_size,status,details_json) VALUES((SELECT source_root_id FROM source_files WHERE id=?),?,?,?,?,?,?)", (source_file_id, relative, name, offset if resolved else None, upstream, struct_status, json.dumps({"method": "static_assert", "fields": len(fields), "all_fields_resolved": resolved})))

        # Explicit procedural sinks.  Semantic is required from the enclosing/name context.
        sinks = {
            "hid_write": ("hidapi hid_write", "host_to_device", True),
            "hid_send_feature_report": ("hidapi feature report", "host_to_device", True),
            "sendReport": ("WebHID sendReport", "host_to_device", False),
            "send_report": ("WebHID sendReport", "host_to_device", False),
            "receiveFeatureReport": ("WebHID receiveFeatureReport", "device_to_host", False),
            "get_report": ("HID feature GET", "device_to_host", False),
            "write": ("vendor API write", "host_to_device", None),
            "Write": ("vendor API write", "host_to_device", None),
            "read": ("vendor API read", "device_to_host", None),
            "Read": ("vendor API read", "device_to_host", None),
        }
        operation_pattern = re.compile(r"(?P<prefix>[A-Za-z0-9_:.>\-]*)\b(?P<sink>hid_write|hid_send_feature_report|sendReport|send_report|receiveFeatureReport|get_report|write|Write|read|Read)\s*\((?P<args>[^;\n]*)\)")
        raw_ids = []
        direct_vids = re.findall(r"(?is)(?:function\s+)?VendorId\s*\([^)]*\)\s*\{?\s*(?:return\s+)?(0x[0-9A-Fa-f]+|\d+)", text)
        direct_pids = re.findall(r"(?is)(?:function\s+)?ProductId\s*\([^)]*\)\s*\{?\s*(?:return\s+)?(0x[0-9A-Fa-f]+|\d+)", text)
        raw_ids.extend(zip(direct_vids, direct_pids))
        for vid, pid in re.findall(r"(?is)(?:VendorId|vendor_id|VID)\s*\(?\s*\)?\s*(?:\{|=>|=|:|return)?\s*(0x[0-9A-Fa-f]+|\d+).{0,240}?(?:ProductId|product_id|PID)\s*\(?\s*\)?\s*(?:\{|=>|=|:|return)?\s*(0x[0-9A-Fa-f]+|\d+)", text):
            raw_ids.append((vid, pid))
        product_ids: set[int] = set()
        for vid_text, pid_text in raw_ids:
            try:
                vid, pid = int(vid_text, 0), int(pid_text, 0)
            except ValueError:
                continue
            product_ids.update(row[0] for row in conn.execute("SELECT DISTINCT product_id FROM device_identifiers WHERE vid=? AND pid=? AND product_id IS NOT NULL", (vid, pid)))
        for index, match in enumerate(operation_pattern.finditer(text)):
            before = text[max(0, match.start() - 600):match.start()]
            function_names = re.findall(r"(?:function\s+|def\s+|[A-Za-z0-9_<>,\[\]\s]+\s+)([A-Za-z_][A-Za-z0-9_]*)\s*\([^()]*\)\s*(?:\{|:)", before)
            symbol = function_names[-1] if function_names else None
            semantic = _semantic_from_name(symbol or "")
            # OpenRGB controller helpers often have transport-only names such
            # as SendControlPacket.  They are still concrete procedural
            # builders when they terminate in a HID sink; retain them as a
            # deliberately generic command instead of discarding the evidence.
            if semantic is None and root.name in {"openrgb", "data-openrgb"} and symbol:
                if re.search(r"(?i)(send|write).*(packet|report|command)|(?:send|write)control", symbol):
                    semantic = "protocol.command"
                elif re.search(r"(?i)(read|get).*(packet|report|response|status)", symbol):
                    semantic = "protocol.readback"
            if semantic is None:
                continue
            api, direction, report_in_buffer = sinks[match.group("sink")]
            if match.group("sink").lower() in {"write", "read"} and not (root.name.startswith("signalrgb") or re.search(r"(?i)(hid|usb|device|endpoint)", match.group("prefix") + before[-120:])):
                continue
            args = match.group("args")
            literals = re.findall(r"0x[0-9A-Fa-f]+|\b\d+\b", args)
            report_id = literals[0] if literals else None
            arg_parts = [part.strip() for part in args.split(",")]
            length_sink = match.group("sink") in {"hid_write", "hid_send_feature_report"} or (root.name.startswith("signalrgb") and match.group("sink") == "write")
            length = int(arg_parts[-1], 0) if length_sink and len(arg_parts) >= 2 and re.fullmatch(r"0x[0-9A-Fa-f]+|\d+", arg_parts[-1]) else None
            request_layout = json.dumps({"source_expression": args[:500], "dynamic": bool(re.search(r"[A-Za-z_]", args))})
            family_key = scope_key.removeprefix("family:")
            conn.execute("INSERT OR IGNORE INTO protocol_families(family_key,display_name) VALUES(?,?)", (family_key, family_key))
            family_id = conn.execute("SELECT id FROM protocol_families WHERE family_key=?", (family_key,)).fetchone()[0]
            for product_id in sorted(product_ids):
                conn.execute(
                    """INSERT OR IGNORE INTO device_protocol_mappings(product_id,protocol_family_id,mapping_basis,confidence,source_file_id)
                       VALUES(?,?,'direct_vid_pid_in_same_source',.85,?)""",
                    (product_id, family_id, source_file_id),
                )
            contract = {
                "scope": scope_key, "semantic": semantic, "method": match.group("sink"),
                "report_id": report_id, "direction": direction, "request": args[:500],
            }
            operation_key = "source-contract:" + hashlib.sha256(
                json.dumps(contract, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            conn.execute(
                """INSERT OR IGNORE INTO protocol_operations(
                       operation_key,scope_type,scope_key,protocol_family_id,protocol_family,
                       semantic,transport,api_semantics,report_id,api_length,direction,
                       request_encoding_json,capability_mapping_json,confidence,source_trust,
                       operation_status,request_method,report_id_in_buffer,dynamic_fields_json,production_safe)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)""",
                (operation_key, "protocol_family", scope_key, family_id, family_key,
                 semantic, "hid" if "HID" in api or "hid" in api else "vendor_api", api,
                 report_id, length, direction, request_layout, json.dumps({semantic: "implemented"}),
                 0.7, root.trust, "candidate", match.group("sink"),
                 int(report_in_buffer) if report_in_buffer is not None else None,
                 json.dumps({"state": "known", "expressions": [args]}) if re.search(r"[A-Za-z_]", args)
                 else json.dumps({"state": "not_applicable", "fields": []})),
            )
            operation_id = conn.execute("SELECT id FROM protocol_operations WHERE operation_key=?", (operation_key,)).fetchone()[0]
            conn.execute("INSERT OR IGNORE INTO operation_evidence(operation_id,source_file_id,extraction_method,trust_class,lineage_group,confidence,line_start,symbol) VALUES(?,?,?,?,?,?,?,?)", (operation_id, source_file_id, f"tree_sitter_{language}+procedural_sink", root.trust, root.name, 0.7, text.count("\n", 0, match.start()) + 1, symbol))
            counts["operations"] += 1
            counts["operation_candidates"] += 1
            if direction == "device_to_host":
                self._insert_typed_fact(
                    conn, "ResponseDefinition", scope_type, scope_key,
                    "protocol.response", f"{symbol or match.group('sink')}:{index}",
                    {"api_semantics": api, "report_id": report_id,
                     "source_expression": args[:500]}, source_file_id,
                    root.trust, root.name, text.count("\n", 0, match.start()) + 1,
                    symbol, f"tree_sitter_{language}+response_sink", 0.7,
                )
                counts["facts"] += 1

        # Explicit checksum functions are structured definitions, not operations.
        for match in re.finditer(r"(?is)(?:function\s+|def\s+|[A-Za-z0-9_<>,\[\]\s]+\s+)(?P<name>[A-Za-z0-9_]*(?:crc|checksum)[A-Za-z0-9_]*)\s*\([^)]*\)\s*(?:\{|:)(?P<body>.{0,1800})", text):
            body = match.group("body")
            algorithm = "xor" if "^" in body else ("additive" if "+=" in body else "unknown")
            value = {"algorithm": algorithm, "range": "source_expression", "expression": body[:500]}
            self._insert_typed_fact(conn, "ChecksumDefinition", scope_type, scope_key, "protocol.checksum", match.group("name"), value, source_file_id, root.trust, root.name, text.count("\n", 0, match.start()) + 1, match.group("name"), f"tree_sitter_{language}+checksum", 0.75 if algorithm != "unknown" else 0.5)
            counts["facts"] += 1

        # Multi-call functions become sequence candidates with ordered, unlinked steps.
        for seq_index, match in enumerate(re.finditer(r"(?is)(?:function\s+|def\s+|[A-Za-z0-9_<>,\[\]\s]+\s+)(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\([^)]*\)\s*(?:\{|:)(?P<body>.{0,4000}?)(?:\n\}|\n(?=def\s|function\s))", text)):
            body = match.group("body")
            calls = list(operation_pattern.finditer(body))
            if len(calls) < 2 or not (_semantic_from_name(match.group("name")) or any(token in match.group("name").lower() for token in ("init", "handshake", "startup", "shutdown", "apply", "commit"))):
                continue
            sequence_key = f"{Path(relative).stem}:{match.group('name')}:{seq_index}"
            semantic = _semantic_from_name(match.group("name")) or f"sequence.{match.group('name').lower()}"
            conn.execute("INSERT OR IGNORE INTO protocol_sequences(scope_type,scope_key,sequence_key,semantic,source_file_id) VALUES(?,?,?,?,?)", (scope_type, scope_key, sequence_key, semantic, source_file_id))
            sequence_id = conn.execute("SELECT id FROM protocol_sequences WHERE scope_type=? AND scope_key=? AND sequence_key=? AND source_file_id=?", (scope_type, scope_key, sequence_key, source_file_id)).fetchone()[0]
            for step, call in enumerate(calls):
                conn.execute("INSERT OR IGNORE INTO protocol_sequence_steps(sequence_id,step_index,step_kind) VALUES(?,?,?)", (sequence_id, step, call.group("sink")))
            counts["sequences"] += 1

        if counts["layouts"] or counts["operation_candidates"] or counts["facts"]:
            return "parsed_protocol_data", counts, "tree-sitter parsed" + (" with recoverable syntax errors" if tree.root_node.has_error else "")
        return "parsed_no_relevant_facts", counts, "tree-sitter parsed; no explicit protocol entities" + ("; syntax recovery used" if tree.root_node.has_error else "")

    def _parse_data(self, conn: sqlite3.Connection, root: SourceRoot, source_file_id: int,
                    relative: str, data: bytes) -> tuple[str, Counter[str], str | None]:
        suffix = Path(relative).suffix.lower()
        text = data.decode("utf-8-sig", errors="replace")
        scope_type, scope_key = _scope(root.name, relative)
        counts: Counter[str] = Counter()
        if suffix == ".json":
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError as exc:
                try:
                    parsed = json5.loads(text)
                except Exception as relaxed_exc:
                    return "parse_failed", counts, f"JSON: {exc}; JSON5: {relaxed_exc}"
            serialized = json.dumps(parsed, ensure_ascii=False)
            identity_matches = re.findall(r"(?i)\b(?:vid|vendor.?id)\b[^0-9a-f]*(0x[0-9a-f]+|\d+).{0,100}\b(?:pid|product.?id)\b[^0-9a-f]*(0x[0-9a-f]+|\d+)", serialized)
            for idx, (vid, pid) in enumerate(identity_matches):
                self._insert_typed_fact(conn, "DeviceIdentity", scope_type, scope_key, "device.usb_identity", f"vid_pid:{idx}", {"vid": vid, "pid": pid}, source_file_id, root.trust, root.name, None, None, "json_structured_identity", 0.7)
                counts["facts"] += 1
            return ("parsed_identity_only" if identity_matches else "parsed_metadata_only"), counts, None
        if suffix in {".device", ".ini", ".toml", ".yaml", ".yml", ".mk"}:
            if re.search(r"(?i)(device|vendor|product|protocol|hid|usb|report)", text):
                return "parsed_metadata_only", counts, "structured configuration inspected"
            return "parsed_no_relevant_facts", counts, "structured configuration has no protocol tokens"
        return "unsupported_format", counts, f"no typed parser for {suffix}"

    def _parse_capture(self, conn: sqlite3.Connection, root: SourceRoot, source_file_id: int,
                       path: Path) -> tuple[str, Counter[str], str | None]:
        counts: Counter[str] = Counter()
        semantic = _semantic_from_name(path.stem)
        try:
            with path.open("rb") as stream:
                reader = dpkt.pcapng.Reader(stream) if path.suffix.lower() == ".pcapng" else dpkt.pcap.Reader(stream)
                frames = list(reader)
                linktype = reader.datalink()
        except Exception as exc:
            return "parse_failed", counts, f"capture: {exc}"
        conn.execute("INSERT INTO capture_files(source_file_id,sha256,capture_format,packet_count,transaction_count,parse_status,semantic_label) VALUES(?,?,?,?,?,'parsed',?)", (source_file_id, _sha256(path), path.suffix.lower().lstrip("."), len(frames), len(frames), semantic))
        capture_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        scope_type, scope_key = _scope(root.name, path.name)
        for seq, (timestamp, payload) in enumerate(frames):
            raw = bytes(payload)
            conn.execute("""INSERT INTO capture_transactions(capture_file_id,sequence_no,timestamp,transfer_type,direction,report_id,payload_hex,payload_length,pair_key)
                            VALUES(?,?,?,?,?,?,?,?,?)""",
                         (capture_id, seq, float(timestamp), f"linktype:{linktype}", "unknown", raw[0] if raw else None, raw.hex(), len(raw), f"frame:{seq // 2}"))
        value = {"capture_sha256": _sha256(path), "frames": len(frames), "linktype": linktype, "semantic_label": semantic}
        conn.execute("INSERT OR IGNORE INTO runtime_observations(scope_type,scope_key,observation_kind,semantic_label,value_json,source_file_id,trust_class,hardware_verified) VALUES(?,?,'CommunityCapture',?,?,?,?,0)", (scope_type, scope_key, semantic, json.dumps(value, sort_keys=True), source_file_id, "CommunityCapture"))
        counts["captures"] = 1; counts["transactions"] = len(frames)
        return "capture_parsed", counts, None

    def _process_file(self, conn: sqlite3.Connection, root: SourceRoot, root_id: int, path: Path,
                      relative: str, content_hash: str, duplicate: tuple[int, int] | None) -> None:
        size = path.stat().st_size
        relevant = _is_relevant(root.name, relative)
        source_file_id = conn.execute(
            """INSERT INTO source_files(source_root_id,relative_path,content_hash,size,relevant,parsed,parser_name,parse_status,bytes_scanned,collector_version)
               VALUES(?,?,?,?,?,0,?,'not_applicable',?,?) RETURNING id""",
            (root_id, relative, content_hash, size, int(relevant), "FullTypedReprocessor", size if relevant else 0, COLLECTOR_VERSION),
        ).fetchone()[0]
        self.stats["files_total"] += 1
        if not relevant:
            return
        self.stats["relevant_files"] += 1; self.stats["bytes_scanned"] += size
        if duplicate and duplicate[0] != root_id:
            status, counts, detail = "duplicate_or_derived", Counter(), f"exact content duplicate of source_file:{duplicate[1]}"
            conn.execute("UPDATE source_lineage SET parent_source_root_id=?,relationship='copied',rationale='exact relevant-file content duplicate' WHERE child_source_root_id=? AND parent_source_root_id IS NULL", (duplicate[0], root_id))
        elif any(part.lower() in {"test", "tests", "fixtures", "examples"} for part in Path(relative).parts):
            status, counts, detail = "test_fixture", Counter(), "path classified as test/example fixture"
        elif path.suffix.lower() in CAPTURE_SUFFIXES:
            status, counts, detail = self._parse_capture(conn, root, source_file_id, path)
        elif path.suffix.lower() in CODE_LANGUAGES:
            try:
                status, counts, detail = self._parse_code(conn, root, source_file_id, relative, path.read_bytes())
            except Exception as exc:
                status, counts, detail = "parse_failed", Counter(), f"{type(exc).__name__}: {exc}"
        elif path.suffix.lower() in DOC_SUFFIXES:
            status, counts, detail = "documentation_only", Counter(), "documentation inspected; no operation synthesized"
        else:
            try:
                status, counts, detail = self._parse_data(conn, root, source_file_id, relative, path.read_bytes())
            except Exception as exc:
                status, counts, detail = "parse_failed", Counter(), f"{type(exc).__name__}: {exc}"
        if status not in FINAL_STATUSES:
            raise AssertionError(f"invalid final status {status}")
        conn.execute("""UPDATE source_files SET parsed=1,parse_status=?,warning=?,failure_detail=?,facts_extracted=?,operations_extracted=?,layouts_extracted=?,sequences_extracted=? WHERE id=?""",
                     (status, detail if status != "parse_failed" else None, detail if status == "parse_failed" else None, counts["facts"], counts["operations"], counts["layouts"], counts["sequences"], source_file_id))
        self.stats[status] += 1
        self.stats.update(counts)
        if status != "parse_failed":
            self.content_owner.setdefault(content_hash, (root_id, source_file_id))

    def run(self) -> dict[str, int]:
        self.stats = Counter()
        self.content_owner.clear()
        self.stats["hash_cache_primed"] = self.prime_content_cache_from_inventory()
        self.reset_typed_derivatives()
        roots = self.discover_roots()
        self.stats["source_roots"] = len(roots)
        with self.connection() as conn:
            for root_index, root in enumerate(roots, 1):
                print(f"root {root_index}/{len(roots)} start {root.name}", flush=True)
                root_id = self._record_root(conn, root)
                root_digest = hashlib.sha256()
                for path in _files(root.path):
                    relative = path.relative_to(root.path).as_posix()
                    # CAS filenames are already verified SHA-256 identities.  Do
                    # not reread multi-gigabyte opaque blobs merely to rediscover
                    # the same digest.
                    content_hash = self._content_hash(conn, root, path)
                    root_digest.update(relative.encode("utf-8", errors="surrogatepass"))
                    root_digest.update(b"\0")
                    root_digest.update(content_hash.encode("ascii"))
                    root_digest.update(b"\n")
                    duplicate = self.content_owner.get(content_hash) if _is_relevant(root.name, relative) else None
                    self._process_file(conn, root, root_id, path, relative, content_hash, duplicate)
                conn.execute("""UPDATE source_roots SET root_content_hash=?,
                    files_total=(SELECT count(*) FROM source_files WHERE source_root_id=?),
                    files_relevant=(SELECT count(*) FROM source_files WHERE source_root_id=? AND relevant=1),
                    files_processed=(SELECT count(*) FROM source_files WHERE source_root_id=? AND relevant=1 AND parsed=1),
                    files_failed=(SELECT count(*) FROM source_files WHERE source_root_id=? AND parse_status='parse_failed'),
                    bytes_scanned=(SELECT coalesce(sum(bytes_scanned),0) FROM source_files WHERE source_root_id=?)
                    WHERE id=?""", (root_digest.hexdigest(), root_id, root_id, root_id, root_id, root_id, root_id))
                conn.commit()
                coverage = conn.execute("SELECT files_total,files_relevant,files_processed,files_failed FROM source_roots WHERE id=?", (root_id,)).fetchone()
                print(f"root {root_index}/{len(roots)} done {root.name} total={coverage[0]} relevant={coverage[1]} processed={coverage[2]} failed={coverage[3]}", flush=True)
        return dict(self.stats)
