"""Second forensic repair pass: scoped facts and typed protocol operations.

This module is intentionally conservative.  Untyped strings are retained as
facts, but can never become executable commands, risks, or READY evidence.
"""
from __future__ import annotations

import json
import sqlite3
from collections import Counter
from pathlib import Path


REQUIRED_OPERATION_FIELDS = (
    "protocol_family", "transport", "api_semantics", "direction",
    "request_encoding_json", "dynamic_fields_json", "response_encoding_json",
    "checksum_json", "sequencing_json", "initialization_json", "capability_mapping_json",
)


class RepairPass2:
    def __init__(self, db_path: Path):
        self.db_path = db_path

    def connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def rebuild_scoped_graph(self) -> dict[str, int]:
        """Apply device scope by default; never globally deduplicate device facts."""
        with self.connection() as conn:
            rows = conn.execute("SELECT id, product_id, canonical_key, canonical_value, value_hash FROM normalized_facts").fetchall()
            for row in rows:
                conn.execute(
                    "UPDATE normalized_facts SET scope_type='device', scope_key=?, semantic=? WHERE id=?",
                    (f"product:{row['product_id']}" if row["product_id"] is not None else f"legacy:{row['id']}", row["canonical_key"], row["id"]),
                )
            # Explicitly mark source roots with no derivation proof as independent
            # only as repositories, never as independent evidence for a fact.
            conn.execute("DELETE FROM source_lineage")
            conn.execute("INSERT INTO source_lineage(child_source_root_id,parent_source_root_id,relationship,rationale) SELECT id,NULL,'unknown','no derivation proof recorded' FROM source_roots")
            return {"scoped_fact_nodes": len(rows), "lineage_unknown": conn.execute("SELECT count(*) FROM source_lineage WHERE relationship='unknown'").fetchone()[0]}

    @staticmethod
    def _operation_missing(operation: sqlite3.Row, evidence_count: int) -> list[str]:
        missing: list[str] = []
        for field in REQUIRED_OPERATION_FIELDS:
            if operation[field] in (None, "", "[]", "{}"):
                missing.append(field)
                continue
            if field.endswith("_json"):
                try:
                    structured = json.loads(operation[field])
                except (TypeError, json.JSONDecodeError):
                    missing.append(f"{field}:invalid_json")
                    continue
                if isinstance(structured, dict) and structured.get("state") in {"unknown", "unresolved", "pending"}:
                    missing.append(f"{field}:unknown")
        if operation["transport"] == "hid" and operation["report_id"] in (None, ""):
            missing.append("report_id")
        if operation["api_length"] is None and operation["wire_length"] is None:
            missing.append("framing_length")
        if operation["timeout_ms"] is None and operation["delay_ms"] is None:
            try:
                sequencing = json.loads(operation["sequencing_json"] or "null")
            except json.JSONDecodeError:
                sequencing = None
            if not isinstance(sequencing, dict) or sequencing.get("timing_state") not in {"known", "not_applicable"}:
                missing.append("timing")
        if evidence_count == 0:
            missing.append("operation_evidence")
        return sorted(set(missing))

    def derive_risk_and_reconstructibility(self) -> dict[str, int]:
        """Classify only typed operations; generic facts are deliberately excluded."""
        totals: Counter[str] = Counter()
        with self.connection() as conn:
            conn.execute("DELETE FROM command_risks")
            conn.execute("DELETE FROM operation_completeness")
            for op in conn.execute("SELECT * FROM protocol_operations WHERE operation_status != 'rejected'").fetchall():
                evidence_symbols = " ".join(row[0] or "" for row in conn.execute(
                    "SELECT symbol FROM operation_evidence WHERE operation_id=?", (op["id"],)).fetchall())
                payload = " ".join([*(str(op[field] or "") for field in ("semantic", "side_effect", "persistence", "risk_state")), evidence_symbols]).lower()
                if op["risk_state"] == "destructive" or any(token in payload for token in ("dfu", "flash", "bootloader", "erase", "factory_reset")):
                    risk, reason = "destructive", "typed operation explicitly includes destructive firmware/reset semantics"
                elif op["persistence"] == "persistent" or any(token in payload for token in ("eeprom", "nvram", "persistent", "save", "store", "calibration")):
                    risk, reason = "persistent_write", "typed operation explicitly writes persistent state"
                elif op["direction"] == "device_to_host":
                    risk, reason = "read_only", "typed operation has device-to-host direction"
                elif op["direction"] == "host_to_device":
                    risk, reason = "volatile_write", "typed operation has host-to-device direction without persistent/destructive semantics"
                else:
                    risk, reason = "unknown_risk", "typed operation direction is incomplete"
                conn.execute("INSERT INTO command_risks(normalized_fact_id,operation_id,risk_class,rationale) VALUES(?,?,?,?)", (op["source_fact_id"], op["id"], risk, reason))
                totals[risk] += 1
                evidence_count = conn.execute("SELECT count(*) FROM operation_evidence WHERE operation_id=?", (op["id"],)).fetchone()[0]
                missing = self._operation_missing(op, evidence_count)
                total_requirements = len(REQUIRED_OPERATION_FIELDS) + 4
                score = max(0, round(100 * (total_requirements - len(missing)) / total_requirements))
                conn.execute("INSERT INTO operation_completeness(operation_id,score,missing_requirements_json,complete,explanation) VALUES(?,?,?,?,?)", (op["id"], score, json.dumps(missing), int(not missing), "complete typed contract" if not missing else "missing: " + ", ".join(missing)))

            conn.execute("DELETE FROM device_reconstructibility")
            products = conn.execute("""SELECT p.id,
                EXISTS(SELECT 1 FROM device_identifiers d WHERE d.product_id=p.id) AS identity,
                EXISTS(SELECT 1 FROM protocol_hints h WHERE h.product_id=p.id) AS capabilities
                FROM products p""").fetchall()
            for product in products:
                operations = conn.execute("""SELECT DISTINCT po.*
                    FROM protocol_operations po
                    LEFT JOIN device_protocol_mappings dpm
                      ON po.scope_type='protocol_family' AND dpm.protocol_family_id=po.protocol_family_id
                    WHERE po.operation_status != 'rejected'
                      AND ((po.scope_type='device' AND po.product_id=?) OR dpm.product_id=?)""",
                    (product["id"], product["id"])).fetchall()
                critical_conflict = conn.execute("SELECT 1 FROM fact_conflicts WHERE product_id=? AND status='unresolved' AND canonical_key IN ('packet_length','report_length','wire_length','report_id')", (product["id"],)).fetchone()
                completeness = [conn.execute("SELECT score,complete FROM operation_completeness WHERE operation_id=?", (op["id"],)).fetchone() for op in operations]
                complete = bool(operations) and all(row["complete"] for row in completeness)
                best_score = max((row["score"] for row in completeness), default=0)
                if product["identity"] and complete and not critical_conflict:
                    classification, rationale = "IMPLEMENTATION_READY", "all typed operation requirements present; hardware validation remains pending"
                elif operations and best_score >= 75:
                    classification, rationale = "NEAR_COMPLETE", f"best typed operation completeness={best_score}; mandatory fields remain"
                elif operations:
                    classification, rationale = "PARTIAL_PROTOCOL", "typed operations exist but required implementation fields are incomplete"
                elif product["identity"] and product["capabilities"]:
                    classification, rationale = "IDENTITY_AND_CAPABILITIES", "no typed executable operation"
                else:
                    classification, rationale = "IDENTITY_ONLY", "no typed protocol operation"
                has_device_operation = any(op["scope_type"] == "device" for op in operations)
                mapping_confidence = 1.0 if has_device_operation else conn.execute("SELECT coalesce(max(confidence),0) FROM device_protocol_mappings WHERE product_id=?", (product["id"],)).fetchone()[0]
                if classification == "IMPLEMENTATION_READY" and mapping_confidence < 0.8:
                    classification, rationale = "NEAR_COMPLETE", f"family contract complete but device mapping confidence={mapping_confidence:.2f} < 0.80"
                conn.execute("INSERT INTO device_reconstructibility(product_id,classification,family_reconstructibility,device_mapping_confidence,hardware_validation_state,rationale) VALUES(?,?,?,?, 'pending', ?)", (product["id"], classification, classification, mapping_confidence, rationale))
                totals[classification] += 1
            for family in conn.execute("SELECT id,family_key FROM protocol_families").fetchall():
                ops = conn.execute("SELECT oc.complete,oc.score FROM protocol_operations po JOIN operation_completeness oc ON oc.operation_id=po.id WHERE po.protocol_family=?", (family["family_key"],)).fetchall()
                reconstruction = "IMPLEMENTATION_READY" if ops and all(row["complete"] for row in ops) else ("NEAR_COMPLETE" if ops and max(row["score"] for row in ops) >= 75 else ("PARTIAL_PROTOCOL" if ops else "IDENTITY_ONLY"))
                conn.execute("UPDATE protocol_families SET reconstructibility=? WHERE id=?", (reconstruction, family["id"]))
        return dict(totals)
