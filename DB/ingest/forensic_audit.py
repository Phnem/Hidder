"""Evidence-first forensic audit for the peripheral registry.

The audit is intentionally conservative: missing file/line provenance lowers
coverage and reconstructibility; it is never silently converted to confirmation.
It is safe to run repeatedly and performs no HID I/O.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from ingest.collectors.openrazer import OpenRazerDriverParser
from ingest.storage.database import RegistryDatabase


SOURCE_COLLECTORS = {
    "openrazer": "OpenRazerCollector", "solaar": "SolaarCollector",
    "rivalcfg": "RivalcfgCollector", "wootswitch": "WootingCollector",
    "wooting-rgb-sdk": "WootingCollector", "wooting-analog-sdk": "WootingCollector",
    "ckb-next": "CorsairCkbCollector", "corsair-protocol": "CorsairCkbCollector",
    "artemis": "ArtemisRGBNetCollector", "artemis-plugins": "ArtemisRGBNetCollector",
    "rgb-net": "ArtemisRGBNetCollector", "linux": "LinuxHIDCollector",
    "logitech-cpg-docs": "LogitechDocsCollector", "g933-utils": "LogitechDocsCollector",
    "hidpp-cvuchener": "LogitechDocsCollector",
}


def _git(root: Path, *args: str) -> str | None:
    if not (root / ".git").exists():
        return None
    run = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    return run.stdout.strip() if run.returncode == 0 and run.stdout.strip() else None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _source_url(root: Path) -> str | None:
    url = _git(root, "config", "--get", "remote.origin.url")
    if not url:
        return None
    if url.startswith("git@github.com:"):
        return "https://github.com/" + url.split(":", 1)[1]
    return url.removesuffix(".git")


def _is_relevant(root_name: str, rel: str) -> bool:
    suffix = Path(rel).suffix.lower()
    if root_name.startswith("signalrgb"):
        return suffix in {".js", ".json", ".pcap", ".pcapng"}
    if root_name == "linux":
        return rel.startswith("drivers/hid/") and suffix in {".c", ".h"}
    if root_name in {"openrazer", "ckb-next", "corsair-protocol", "hidpp-cvuchener"}:
        return suffix in {".c", ".h", ".cpp", ".hpp"}
    if root_name in {"solaar", "rivalcfg", "g933-utils"}:
        return suffix == ".py"
    if root_name in {"artemis", "artemis-plugins", "rgb-net"}:
        return suffix == ".cs"
    if root_name.startswith("wooting") or root_name == "wootswitch":
        return suffix in {".c", ".h", ".rs", ".js", ".py"}
    return suffix in {".c", ".h", ".cpp", ".hpp", ".py", ".js", ".json", ".md"}


def _files_under(root: Path) -> list[Path]:
    """Walk working trees while pruning VCS/object directories before descent."""
    files: list[Path] = []
    for directory, children, names in os.walk(root):
        children[:] = [name for name in children if name not in {".git", ".hg", ".svn", "node_modules", "__pycache__"}]
        files.extend(Path(directory) / name for name in names)
    return files


class ForensicAudit:
    def __init__(self, database_path: Path, sources_root: Path):
        self.database_path = database_path.resolve()
        self.sources_root = sources_root.resolve()
        self.db = RegistryDatabase(self.database_path)

    def _connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def audit_source_roots(self) -> dict[str, int]:
        """Inventory every local source at file granularity without guessing coverage."""
        totals = Counter()
        with self._connection() as conn:
            urls = [unquote(r[0]).lower() for r in conn.execute("SELECT source_url FROM sources")]
            for root in sorted(p for p in self.sources_root.iterdir() if p.is_dir()):
                files = _files_under(root)
                commit, branch, repository = _git(root, "rev-parse", "HEAD"), _git(root, "branch", "--show-current"), _source_url(root)
                license_file = next((p for p in root.iterdir() if p.is_file() and p.name.lower() in {"license", "license.md", "copying", "copying.md"}), None)
                root_hash = hashlib.sha256()
                for file in files:
                    rel = file.relative_to(root).as_posix()
                    root_hash.update(rel.encode("utf-8")); root_hash.update(_sha256(file).encode("ascii"))
                status = "verified_git" if commit else "immutable_file_hash_only"
                conn.execute(
                    """INSERT INTO source_roots(root_name, local_path, repository_url, commit_sha, branch, license_file, license_text, root_content_hash, source_kind, audit_status, audited_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(root_name) DO UPDATE SET local_path=excluded.local_path, repository_url=excluded.repository_url, commit_sha=excluded.commit_sha, branch=excluded.branch, license_file=excluded.license_file, license_text=excluded.license_text, root_content_hash=excluded.root_content_hash, source_kind=excluded.source_kind, audit_status=excluded.audit_status, audited_at=excluded.audited_at""",
                    (root.name, str(root.resolve()), repository, commit, branch,
                     license_file.name if license_file else None,
                     license_file.read_text(encoding="utf-8", errors="replace")[:4000] if license_file else None,
                     root_hash.hexdigest(), "repository" if commit else "archive_or_api", status,
                     datetime.now(timezone.utc).isoformat()),
                )
                root_id = conn.execute("SELECT id FROM source_roots WHERE root_name=?", (root.name,)).fetchone()[0]
                conn.execute("DELETE FROM source_files WHERE source_root_id=?", (root_id,))
                collector = "SignalRGBCollector" if root.name.startswith("signalrgb") else SOURCE_COLLECTORS.get(root.name)
                rows = []
                for file in files:
                    rel = file.relative_to(root).as_posix()
                    relevant = _is_relevant(root.name, rel)
                    # A matching stored URL is the only defensible legacy proof of parsing.
                    parsed = relevant and any(rel.lower() in url for url in urls)
                    rows.append((root_id, rel, _sha256(file), file.stat().st_size, int(relevant), int(parsed),
                                 collector, "parsed" if parsed else ("skipped" if relevant else "not_applicable"),
                                 None if parsed or not relevant else "no file-level provenance"))
                conn.executemany(
                    """INSERT INTO source_files(source_root_id, relative_path, content_hash, size, relevant, parsed, parser_name, parse_status, warning)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""", rows)
                totals["sources_discovered"] += 1
                totals["files_scanned"] += len(files)
                totals["relevant_files"] += sum(r[4] for r in rows)
                totals["parsed_files"] += sum(r[5] for r in rows)
                totals["skipped_files"] += sum(r[4] and not r[5] for r in rows)
        return dict(totals)

    def repair_openrazer_struct(self) -> dict[str, int]:
        """Replace only the known synthetic layout with the upstream-derived one."""
        parser = OpenRazerDriverParser(self.sources_root / "openrazer")
        layouts = parser._extract_structs()
        if not layouts:
            raise RuntimeError("OpenRazer upstream razer_report could not be validated")
        layout_json = json.dumps(layouts, sort_keys=True)
        changed = 0
        with self._connection() as conn:
            rows = conn.execute("SELECT id, value FROM facts WHERE key='openrazer_packet_structs'").fetchall()
            for row in rows:
                try:
                    old = json.loads(row["value"])
                except json.JSONDecodeError:
                    continue
                if any(x.get("struct_name") == "razer_report" and x.get("total_size") == 97 for x in old if isinstance(x, dict)):
                    conn.execute("UPDATE facts SET value=?, last_seen=? WHERE id=?", (layout_json, datetime.now(timezone.utc).isoformat(), row["id"]))
                    changed += 1
            root = conn.execute("SELECT id FROM source_roots WHERE root_name='openrazer'").fetchone()
            if root:
                conn.execute(
                    """INSERT INTO struct_validations(source_root_id, source_path, struct_name, calculated_size, upstream_size, status, details_json)
                       VALUES (?, 'driver/razercommon.h', 'razer_report', 90, 90, 'validated', ?)
                       ON CONFLICT(source_root_id, source_path, struct_name) DO UPDATE SET calculated_size=excluded.calculated_size, upstream_size=excluded.upstream_size, status=excluded.status, details_json=excluded.details_json""",
                    (root[0], layout_json),
                )
        return {"invalid_struct_sizes": changed, "struct_sizes_corrected": changed}

    def repair_provenance_labels(self) -> dict[str, int]:
        """Correct two legacy overclaims without fabricating new source evidence."""
        repaired = Counter()
        with self._connection() as conn:
            # Solaar's numeric field is device metadata, not a HID++ spec name.
            result = conn.execute(
                """UPDATE protocol_hints SET hint_key='solaar_device_protocol_field',
                       hint_value=replace(hint_value, 'HID++ ', '')
                   WHERE hint_key='hidpp_protocol_version' AND source_id IN
                     (SELECT id FROM sources WHERE source_url LIKE '%pwr-Solaar/Solaar%')"""
            )
            repaired["solaar_protocol_labels_corrected"] = result.rowcount

            roots = conn.execute("SELECT root_name, repository_url, commit_sha FROM source_roots WHERE repository_url IS NOT NULL AND commit_sha IS NOT NULL").fetchall()
            for root in roots:
                if not root["root_name"].startswith("signalrgb-"):
                    continue
                repo, sha = root["repository_url"].removesuffix(".git"), root["commit_sha"]
                marker = f"/-/blob/{sha}/" if "gitlab.com" in repo else f"/blob/{sha}/"
                rows = conn.execute("SELECT id, source_url FROM sources WHERE content_hash=?", (sha,)).fetchall()
                for row in rows:
                    url = row["source_url"]
                    old_marker = f"/blob/{sha}/"
                    pos = url.find(old_marker)
                    if pos < 0:
                        continue
                    path = url[pos + len(old_marker):]
                    new_url = f"{repo}{marker}{path}"
                    if new_url != url:
                        try:
                            conn.execute("UPDATE sources SET source_url=? WHERE id=?", (new_url, row["id"]))
                            repaired["signalrgb_source_urls_corrected"] += 1
                        except sqlite3.IntegrityError:
                            repaired["signalrgb_source_url_collisions"] += 1
        return dict(repaired)

    def refresh_evidence_lineage(self) -> int:
        """Refresh lineage groups after provenance URL repairs, preserving evidence rows."""
        with self._connection() as conn:
            sources = {
                row["id"]: (row["source_type"], row["source_url"])
                for row in conn.execute("SELECT id, source_type, source_url FROM sources")
            }
            trust_by_type = {
                "official_repository": "OfficialSDK", "vendor_technical": "OfficialSpecification",
                "vendor_software": "OfficialSDK", "vendor_product": "OfficialSpecification",
                "vendor_download": "OfficialSDK", "open_source": "UpstreamImplementation",
                "community": "CommunityImplementation",
            }
            changed = 0
            for evidence_id, source_id in conn.execute("SELECT id,source_id FROM fact_evidence WHERE source_id IS NOT NULL").fetchall():
                source_type, source_url = sources.get(source_id, (None, ""))
                parsed = urlparse(source_url)
                parts = [p for p in parsed.path.split("/") if p]
                group = (f"{parsed.netloc}/{parts[0]}/{parts[1]}" if parsed.netloc in {"github.com", "gitlab.com"} and len(parts) >= 2 else parsed.netloc or "unknown").lower()
                conn.execute("UPDATE fact_evidence SET trust_class=?, independent_source_group=? WHERE id=?", (trust_by_type.get(source_type, "Unknown"), group, evidence_id))
                changed += 1
            return changed

    def rebuild_evidence(self) -> int:
        """Rebuild canonical facts from the corrected legacy rows; no evidence is invented."""
        with self._connection() as conn:
            conn.execute("DELETE FROM command_risks")
            conn.execute("DELETE FROM fact_evidence")
            conn.execute("DELETE FROM normalized_facts")
            rows = conn.execute("SELECT product_id,key,value,source_id,evidence_level,confidence FROM facts").fetchall()
            source_meta = {
                row["id"]: (row["source_type"], row["source_url"])
                for row in conn.execute("SELECT id, source_type, source_url FROM sources")
            }
            trust_by_type = {
                "official_repository": "OfficialSDK", "vendor_technical": "OfficialSpecification",
                "vendor_software": "OfficialSDK", "vendor_product": "OfficialSpecification",
                "vendor_download": "OfficialSDK", "open_source": "UpstreamImplementation",
                "community": "CommunityImplementation",
            }
            for row in rows:
                value = RegistryDatabase._canonical_value(row["value"])
                digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
                conn.execute("INSERT INTO normalized_facts(product_id,canonical_key,canonical_value,value_hash) VALUES(?,?,?,?)",
                             (row["product_id"], row["key"], value, digest))
                fact_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                source_type, source_url = source_meta.get(row["source_id"], (None, ""))
                trust = trust_by_type.get(source_type, "Unknown")
                parts = [p for p in urlparse(source_url).path.split("/") if p]
                parsed_url = urlparse(source_url)
                group = (f"{parsed_url.netloc}/{parts[0]}/{parts[1]}" if parsed_url.netloc in {"github.com", "gitlab.com"} and len(parts) >= 2 else parsed_url.netloc or "unknown").lower()
                conn.execute(
                    """INSERT INTO fact_evidence(normalized_fact_id,source_id,collector_name,collector_version,extraction_method,trust_class,confidence,evidence_level,independent_source_group,provenance_status)
                       VALUES(?,?, 'legacy_ingestion', 'pre-forensic', 'legacy_backfill', ?, ?, ?, ?, 'partial')""",
                    (fact_id, row["source_id"], trust, row["confidence"], row["evidence_level"], group),
                )
            return len(rows)

    def classify_risk_conflicts_and_reconstructibility(self) -> dict[str, int]:
        totals = Counter()
        command_re = re.compile(r"(?:command|opcode|packet|report|dfu|flash|bootloader|firmware)", re.I)
        destructive = re.compile(r"(?:dfu|flash|bootloader|factory.?reset|erase|format)", re.I)
        persistent = re.compile(r"(?:eeprom|nvram|persistent|save|store|calibration)", re.I)
        read_only = re.compile(r"(?:^get|^read|query|status|version|battery)", re.I)
        with self._connection() as conn:
            for row in conn.execute("SELECT id,canonical_key,canonical_value FROM normalized_facts"):
                text = f"{row['canonical_key']} {row['canonical_value']}"
                if not command_re.search(text):
                    continue
                risk, rationale = ("destructive", "firmware/erase/reset token") if destructive.search(text) else \
                    (("persistent_write", "persistent-storage token") if persistent.search(text) else \
                     (("read_only", "read/query token") if read_only.search(text) else ("unknown_risk", "command semantics incomplete")))
                conn.execute("INSERT OR REPLACE INTO command_risks(normalized_fact_id,risk_class,rationale) VALUES(?,?,?)", (row["id"], risk, rationale))
                totals[f"risk_{risk}"] += 1
            conn.execute("DELETE FROM fact_conflicts")
            scalar = "(canonical_key LIKE '%length%' OR canonical_key LIKE '%report_size%' OR canonical_key LIKE '%packet_size%')"
            groups = conn.execute(f"""SELECT product_id,canonical_key FROM normalized_facts WHERE {scalar}
                                      GROUP BY product_id,canonical_key HAVING COUNT(DISTINCT canonical_value)>1""").fetchall()
            for group in groups:
                values = [x[0] for x in conn.execute("SELECT DISTINCT canonical_value FROM normalized_facts WHERE product_id IS ? AND canonical_key=? ORDER BY canonical_value", (group["product_id"], group["canonical_key"]))]
                for a, b in zip(values, values[1:]):
                    conn.execute("INSERT OR IGNORE INTO fact_conflicts(product_id,canonical_key,value_a,value_b,status) VALUES(?,?,?,?, 'unresolved')", (group["product_id"], group["canonical_key"], a, b))
                    totals["conflicts_unresolved"] += 1
            conn.execute("DELETE FROM device_reconstructibility")
            rows = conn.execute("""SELECT p.id,
                EXISTS(SELECT 1 FROM device_identifiers d WHERE d.product_id=p.id) has_identity,
                EXISTS(SELECT 1 FROM protocol_hints h WHERE h.product_id=p.id) has_caps,
                EXISTS(SELECT 1 FROM normalized_facts f WHERE f.product_id=p.id AND f.canonical_key GLOB '*protocol*') has_protocol,
                EXISTS(SELECT 1 FROM normalized_facts f WHERE f.product_id=p.id AND (f.canonical_key GLOB '*packet*' OR f.canonical_key GLOB '*struct*' OR f.canonical_key GLOB '*report*')) has_framing,
                EXISTS(SELECT 1 FROM normalized_facts f WHERE f.product_id=p.id AND (f.canonical_key GLOB '*opcode*' OR f.canonical_key GLOB '*command*')) has_semantics
                FROM products p""").fetchall()
            for row in rows:
                if row["has_identity"] and row["has_protocol"] and row["has_framing"] and row["has_semantics"]:
                    klass, rationale = "IMPLEMENTATION_READY", "identity + protocol + framing + command semantics; hardware validation pending"
                elif row["has_identity"] and row["has_protocol"] and (row["has_framing"] or row["has_semantics"]):
                    klass, rationale = "NEAR_COMPLETE", "identity and partial packet reconstruction"
                elif row["has_protocol"] or row["has_framing"] or row["has_semantics"]:
                    klass, rationale = "PARTIAL_PROTOCOL", "protocol evidence is incomplete or mapping is absent"
                elif row["has_identity"] and row["has_caps"]:
                    klass, rationale = "IDENTITY_AND_CAPABILITIES", "identity and declared/implemented capabilities only"
                else:
                    klass, rationale = "IDENTITY_ONLY", "no reconstructible protocol evidence"
                confidence = 0.9 if row["has_identity"] else 0.0
                conn.execute("INSERT INTO device_reconstructibility(product_id,classification,family_reconstructibility,device_mapping_confidence,hardware_validation_state,rationale) VALUES(?,?,?,?, 'pending', ?)", (row["id"], klass, klass, confidence, rationale))
                totals[klass] += 1
        return dict(totals)

    def run(self) -> dict[str, int]:
        result = Counter(self.audit_source_roots())
        result.update(self.repair_openrazer_struct())
        result.update(self.repair_provenance_labels())
        result["evidence_links_added"] = self.rebuild_evidence()
        result["evidence_lineage_refreshed"] = self.refresh_evidence_lineage()
        result.update(self.classify_risk_conflicts_and_reconstructibility())
        return dict(result)
