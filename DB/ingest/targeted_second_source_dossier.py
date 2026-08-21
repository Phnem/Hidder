"""Produce an evidence-conservative acquisition dossier for active canonical products."""
from __future__ import annotations
import json
import sqlite3
from pathlib import Path

KEYCHRON = {"Keychron Q10", "Keychron Q12", "Keychron Q65", "Keychron V1", "Keychron V4", "Keychron V5", "Keychron V6"}
GMMK = {"GMMK Pro ANSI", "GMMK Pro ISO", "GMMK V2 65 ANSI", "GMMK V2 65 ISO", "GMMK V2 96 ISO", "GMMK Numpad"}


def build_targeted_second_source_dossier(db_path: Path, report_path: Path) -> dict:
    with sqlite3.connect(db_path) as c:
        c.row_factory=sqlite3.Row
        pg=c.execute("SELECT generation_id FROM product_identity_active_generation WHERE singleton=1").fetchone()[0]
        bg=c.execute("SELECT generation_id FROM source_product_binding_active_generation WHERE singleton=1").fetchone()[0]
        rows=c.execute("""SELECT cp.id,cp.display_name,cp.vid,cp.pid,
          group_concat(DISTINCT sr.root_name) current_roots,
          group_concat(DISTINCT pf.family_key) families,
          group_concat(DISTINCT po.semantic) operations,
          count(DISTINCT CASE WHEN b.binding_status='COMPLETE_STATIC_BINDING' THEN i.source_root_id END) complete_roots
          FROM canonical_products cp
          JOIN canonical_product_members cm ON cm.canonical_product_id=cp.id AND cm.generation_id=cp.generation_id
          JOIN source_product_identities si ON si.id=cm.source_product_identity_id
          JOIN source_roots sr ON sr.id=si.source_root_id
          LEFT JOIN source_product_binding_inputs i ON i.source_product_identity_id=si.id AND i.generation_id=?
          LEFT JOIN source_product_bindings b ON b.generation_id=i.generation_id AND b.input_key=i.input_key AND b.binding_status='COMPLETE_STATIC_BINDING'
          LEFT JOIN source_internal_protocol_families sif ON sif.generation_id=b.generation_id AND sif.family_key=b.source_family_key
          LEFT JOIN protocol_families pf ON pf.id=sif.protocol_family_id
          LEFT JOIN protocol_operations po ON po.protocol_family_id=pf.id
          WHERE cp.generation_id=? GROUP BY cp.id ORDER BY cp.display_name,cp.vid,cp.pid""",(bg,pg)).fetchall()
        dossiers=[]
        for r in rows:
            name=r["display_name"]
            current_roots=(r["current_roots"] or "").split(",")
            candidates=[]
            if name in KEYCHRON:
                candidates.append({"source":"Vial/VIA host-side ecosystem","url":"https://github.com/the-via/app","status":"CANDIDATE_UNVERIFIED","reason":"independent host raw-HID implementation exists, but no exact stock VID/PID matcher for this product has been acquired"})
                candidates.append({"source":"Keychron Vial protocol documentation","url":"https://github.com/Tymon3310/keychron-vial/blob/main/docs/firmware/protocol-overview.md","status":"CANDIDATE_UNVERIFIED","reason":"documents Keychron firmware-side HID protocol; requires an exact host-side product matcher or guided runtime capture"})
            elif name in GMMK:
                candidates.append({"source":"GMMK Pro OpenRGB firmware fork","url":"https://github.com/GoXLd/gmmk-pro-OpenRGB","status":"REJECTED_AS_SECOND_HOST_IMPLEMENTATION","reason":"requires flashed custom firmware, so it cannot corroborate the stock-device protocol family"})
                candidates.append({"source":"QMK Community Module","url":"https://github.com/SRGBmods/QMK_Community_Module","status":"REJECTED_AS_SECOND_HOST_IMPLEMENTATION","reason":"device firmware module, not an independently bound host-side implementation"})
            elif name == "Pulsar XBOARD QS":
                candidates.append({"source":"local extracted Pulsar XBOARD QS Vial firmware","url":None,"status":"REJECTED_AS_SECOND_HOST_IMPLEMENTATION","reason":"the available artifact is a .bin firmware image, not vendor host application code"})
            else:
                candidates.append({"source":"QMK source root","url":None,"status":"REJECTED_AS_SECOND_HOST_IMPLEMENTATION","reason":"current matching root is device-side identity/firmware metadata; no host-side registration or transport implementation"})
            dossiers.append({"canonical_product":name,"vid_pid":f"{r['vid']:04x}:{r['pid']:04x}","current_roots":current_roots,
              "existing_bound_source":"signalrgb-qmk-plugins" if r["complete_roots"] else None,
              "existing_protocol_families":(r["families"] or "").split(",") if r["families"] else [],
              "operations":(r["operations"] or "").split(",") if r["operations"] else [],
              "other_current_roots_identity_only":[x for x in current_roots if x!="signalrgb-qmk-plugins"],
              "new_evidence_found":candidates,"second_complete_static_binding":False,"canonical_family_candidate":False,
              "remaining_blocker":"no independently acquired host-side implementation with exact product matcher and transport contract"})
        result={"product_identity_generation_id":pg,"binding_generation_id":bg,"targets_total":len(dossiers),"products_with_2_host_implementations":0,"families_with_2_independent_lineages":0,"families_with_static_and_runtime_evidence":0,"new_canonical_protocol_families":0,"near_complete_candidates":0,"dossiers":dossiers}
        report_path.parent.mkdir(parents=True,exist_ok=True);report_path.write_text(json.dumps(result,indent=2),encoding="utf8")
        return result
