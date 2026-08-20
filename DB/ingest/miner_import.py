"""One-way, evidence-preserving Protocol Miner → Registry importer."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ingest.normalize.evidence import EvidenceLevel, GenericFact, RawSource, SourceType
from ingest.storage.database import RegistryDatabase


class MinerImportError(ValueError):
    pass


def _number(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value, 0)
        except ValueError:
            return None
    return None


def import_miner_evidence(db: RegistryDatabase, evidence_path: Path) -> dict[str, int]:
    """Import a Protocol Miner ``evidence.json`` only when it maps to a Registry device.

    The importer deliberately does not manufacture operations from opaque traces:
    an observation needs ``kind=protocol.operation`` or a dynamic call with an
    explicit typed operation payload.  Partial imports remain partial.
    """
    data = json.loads(evidence_path.read_text(encoding="utf-8"))
    observations = data.get("observations")
    if not isinstance(observations, list):
        raise MinerImportError("evidence.json must contain an observations list")

    identity = next((o.get("value", {}) for o in observations if o.get("kind") == "identity.vid_pid"), None)
    if not isinstance(identity, dict):
        raise MinerImportError("no identity.vid_pid observation")
    vid = _number(identity.get("vid", identity.get("vendor_id", identity.get("vendorId"))))
    pid = _number(identity.get("pid", identity.get("product_id", identity.get("productId"))))
    if vid is None or pid is None:
        raise MinerImportError("identity observation has no parseable VID/PID")

    with db.connection() as conn:
        product = conn.execute("SELECT product_id FROM device_identifiers WHERE vid=? AND pid=? ORDER BY confidence DESC LIMIT 1", (vid, pid)).fetchone()
        if not product:
            raise MinerImportError(f"no Registry product mapped to {vid:#06x}:{pid:#06x}")
        product_id = product["product_id"]
        vendor = conn.execute("SELECT v.name FROM products p JOIN vendors v ON v.id=p.vendor_id WHERE p.id=?", (product_id,)).fetchone()[0]

    content_hash = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
    source_id = db.record_source(RawSource(url=evidence_path.resolve().as_uri(), source_type=SourceType.OTHER, vendor=vendor, content_hash=content_hash))
    imported = {"product_id": product_id, "facts": 0, "operations": 0, "partial_operations": 0}
    for observation in observations:
        kind = observation.get("kind", "")
        value = observation.get("value")
        if kind == "identity.vid_pid" or not isinstance(value, dict):
            continue
        db.upsert_generic_fact(GenericFact(product_id=product_id, key=f"miner.{kind}", value=json.dumps(value, sort_keys=True), source_id=source_id, evidence_level=EvidenceLevel.LEVEL_4_PROTOCOL_FACT, confidence=0.9))
        imported["facts"] += 1
        operation = value if kind == "protocol.operation" else value.get("operation")
        if not isinstance(operation, dict):
            continue
        required = ("semantic", "transport", "api_semantics", "report_id", "direction", "request_encoding", "capability_mapping")
        complete = all(operation.get(field) not in (None, "", [], {}) for field in required)
        family = operation.get("protocol_family")
        operation_key = "miner:" + hashlib.sha256(json.dumps({
            "product_id": product_id, "semantic": operation.get("semantic"),
            "family": family, "report_id": operation.get("report_id"),
            "direction": operation.get("direction"), "request": operation.get("request_encoding"),
        }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        with db.connection() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO protocol_operations(operation_key,scope_type,scope_key,product_id,protocol_family,semantic,transport,api_semantics,report_id,api_length,wire_length,direction,request_encoding_json,response_encoding_json,checksum_json,sequencing_json,initialization_json,capability_mapping_json,dynamic_fields_json,timeout_ms,delay_ms,confidence,source_trust,operation_status)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (operation_key, "device", f"product:{product_id}", product_id, family, operation["semantic"], operation.get("transport"), operation.get("api_semantics"), str(operation.get("report_id")), operation.get("api_length"), operation.get("wire_length"), operation.get("direction"), json.dumps(operation.get("request_encoding")), json.dumps(operation.get("response_encoding")) if operation.get("response_encoding") is not None else None, json.dumps(operation.get("checksum")) if operation.get("checksum") is not None else None, json.dumps(operation.get("sequencing")) if operation.get("sequencing") is not None else None, json.dumps(operation.get("initialization")) if operation.get("initialization") is not None else None, json.dumps(operation.get("capability_mapping")), json.dumps(operation.get("dynamic_fields")) if operation.get("dynamic_fields") is not None else None, operation.get("timeout_ms"), operation.get("delay_ms"), 0.9, "VerifiedDynamicVendorSoftware", "observed" if complete else "candidate"),
            )
            operation_id = conn.execute("SELECT id FROM protocol_operations WHERE operation_key=?", (operation_key,)).fetchone()[0]
            conn.execute("INSERT OR IGNORE INTO operation_evidence(operation_id,source_id,extraction_method,trust_class,lineage_group,confidence) VALUES(?,?,'miner_typed_result','CommunityObservedRuntime','protocol-miner',.9)", (operation_id, source_id))
        imported["operations"] += 1
        imported["partial_operations"] += 0 if complete else 1
    return imported
