"""Evidence-only source-internal product -> implementation binding pass.

This is intentionally a separate derived generation: registry/canonical product
identity is immutable input, and a source matcher never creates cross-source
protocol equivalence.
"""
from __future__ import annotations

import json
import re
import sqlite3
import uuid
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


PRIORITY_ROOTS = {
    "openrgb", "data-openrgb", "openrazer", "libratbag", "data-libratbag", "solaar",
    "hidpp-cvuchener", "ckb-next", "corsair-protocol", "rivalcfg", "wooting-rgb-sdk",
    "wooting-analog-sdk", "wootswitch", "rgb-net", "artemis", "linux",
}
STATUSES = ("IDENTITY_ONLY", "MODULE_BOUND", "FAMILY_BOUND", "TRANSPORT_BOUND",
            "COMPLETE_STATIC_BINDING", "AMBIGUOUS", "CONFLICTED")


def _schemas(c: sqlite3.Connection) -> None:
    c.execute("CREATE TABLE IF NOT EXISTS source_product_binding_generations(generation_id TEXT PRIMARY KEY,status TEXT NOT NULL,product_identity_generation_id TEXT NOT NULL,audit_json TEXT NOT NULL,created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
    c.execute("CREATE TABLE IF NOT EXISTS source_product_binding_active_generation(singleton INTEGER PRIMARY KEY CHECK(singleton=1),generation_id TEXT)")
    c.execute("""CREATE TABLE IF NOT EXISTS source_product_binding_inputs(
        generation_id TEXT NOT NULL,input_key TEXT NOT NULL,source_product_identity_id INTEGER,
        source_root_id INTEGER NOT NULL,source_file_id INTEGER,registry_product_id INTEGER,
        vid INTEGER,pid INTEGER,identity_origin TEXT NOT NULL,identity_status TEXT NOT NULL,
        provenance_json TEXT NOT NULL,PRIMARY KEY(generation_id,input_key))""")
    c.execute("""CREATE TABLE IF NOT EXISTS implementation_modules(
        generation_id TEXT NOT NULL,module_key TEXT NOT NULL,source_root_id INTEGER NOT NULL,
        source_file_id INTEGER NOT NULL,module_kind TEXT NOT NULL,evidence_json TEXT NOT NULL,
        PRIMARY KEY(generation_id,module_key))""")
    c.execute("""CREATE TABLE IF NOT EXISTS source_internal_protocol_families(
        generation_id TEXT NOT NULL,family_key TEXT NOT NULL,source_root_id INTEGER NOT NULL,
        implementation_module_key TEXT NOT NULL,protocol_family_id INTEGER,
        family_basis TEXT NOT NULL,evidence_json TEXT NOT NULL,
        PRIMARY KEY(generation_id,family_key))""")
    c.execute("""CREATE TABLE IF NOT EXISTS transport_contracts(
        generation_id TEXT NOT NULL,contract_key TEXT NOT NULL,source_root_id INTEGER NOT NULL,
        implementation_module_key TEXT NOT NULL,transport TEXT NOT NULL,contract_json TEXT NOT NULL,
        PRIMARY KEY(generation_id,contract_key))""")
    c.execute("""CREATE TABLE IF NOT EXISTS source_product_bindings(
        generation_id TEXT NOT NULL,input_key TEXT NOT NULL,implementation_module_key TEXT,
        source_family_key TEXT,transport_contract_key TEXT,binding_kind TEXT NOT NULL,
        binding_status TEXT NOT NULL,evidence_json TEXT NOT NULL,
        PRIMARY KEY(generation_id,input_key,implementation_module_key,source_family_key,transport_contract_key))""")


def _stage(c: sqlite3.Connection) -> None:
    for table in ("source_product_binding_inputs", "implementation_modules", "source_internal_protocol_families", "transport_contracts", "source_product_bindings"):
        stage="source_binding_" + table + "_staging"
        c.execute(f"DROP TABLE IF EXISTS {stage}")
        c.execute(f"CREATE TABLE {stage} AS SELECT * FROM {table} WHERE 0")


def _transport_rows(c: sqlite3.Connection, family_id: int) -> list[dict[str, Any]]:
    rows=c.execute("""SELECT DISTINCT coalesce(transport,'unknown') transport,report_id,api_length,wire_length,
        endpoint,interface,direction FROM protocol_operations WHERE protocol_family_id=?""",(family_id,)).fetchall()
    grouped: dict[str, dict[str, Any]]={}
    for r in rows:
        item=grouped.setdefault(r["transport"],{"report_ids":set(),"api_lengths":set(),"wire_lengths":set(),"endpoints":set(),"interfaces":set(),"directions":set()})
        for key, value in (("report_ids",r["report_id"]),("api_lengths",r["api_length"]),("wire_lengths",r["wire_length"]),("endpoints",r["endpoint"]),("interfaces",r["interface"]),("directions",r["direction"])):
            if value is not None: item[key].add(value)
    return [{"transport":name,**{k:sorted(v,key=str) for k,v in values.items()}} for name,values in grouped.items()]


def _literal_matchers(c: sqlite3.Connection) -> list[dict[str, Any]]:
    """Only explicit numeric matcher macros/conditions, never arbitrary packet bytes."""
    output=[]
    files=c.execute("""SELECT sf.id source_file_id,sf.relative_path,sr.id root_id,sr.root_name,sr.local_path
       FROM source_files sf JOIN source_roots sr ON sr.id=sf.source_root_id
       WHERE sr.root_name IN (%s) AND sf.relative_path GLOB '*.*'""" % ",".join("?"*len(PRIORITY_ROOTS)),tuple(sorted(PRIORITY_ROOTS))).fetchall()
    macro=re.compile(r"(?:HID_USB_DEVICE|USB_DEVICE)\s*\(\s*(0x[0-9a-fA-F]{3,4}|\d{3,5})\s*,\s*(0x[0-9a-fA-F]{3,4}|\d{3,5})\s*\)")
    condition=re.compile(r"vendor_id\s*==\s*(0x[0-9a-fA-F]{3,4}|\d{3,5}).{0,180}?product_id\s*==\s*(0x[0-9a-fA-F]{3,4}|\d{3,5})",re.I|re.S)
    for f in files:
        path=Path(f["local_path"])/f["relative_path"]
        try: text=path.read_text(encoding="utf8",errors="replace")
        except OSError: continue
        matches=list(macro.finditer(text))+list(condition.finditer(text))
        if not matches: continue
        # Registration/module linkage is in the same implementation source file.
        has_registration=bool(re.search(r"HID_USB_DEVICE|USB_DEVICE|REGISTER_DETECTOR|MODULE_DEVICE_TABLE|\bprobe\s*\(",text,re.I))
        if not has_registration: continue
        for m in matches:
            try: vid,pid=int(m.group(1),0),int(m.group(2),0)
            except ValueError: continue
            output.append({"root_id":f["root_id"],"root_name":f["root_name"],"source_file_id":f["source_file_id"],"vid":vid,"pid":pid,
                           "line":text.count("\n",0,m.start())+1,"registration":"explicit_numeric_matcher"})
    return output


def run_source_internal_product_binding(db_path: Path, report_path: Path) -> dict[str, Any]:
    generation="source-binding-"+uuid.uuid4().hex
    with sqlite3.connect(db_path) as c:
        c.row_factory=sqlite3.Row; c.execute("PRAGMA foreign_keys=ON");c.execute("PRAGMA journal_mode=WAL")
        _schemas(c);_stage(c)
        pg=c.execute("SELECT generation_id FROM product_identity_active_generation WHERE singleton=1").fetchone()
        if not pg: raise RuntimeError("active product identity generation required")
        product_generation=pg[0]
        # Every published source identity is an input; no assumptions are made
        # about catalog/QMK identities having a host implementation.
        current=c.execute("SELECT * FROM source_product_identities WHERE generation_id=?",(product_generation,)).fetchall()
        for r in current:
            key=f"published:{r['id']}"
            c.execute("INSERT INTO source_binding_source_product_binding_inputs_staging VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (generation,key,r["id"],r["source_root_id"],r["source_file_id"],r["registry_product_id"],r["vid"],r["pid"],r["origin"],r["identity_status"],r["provenance_json"]))
        # New source-local identities are only explicit numeric registration
        # matchers.  They are intentionally not made canonical products here.
        discovered=_literal_matchers(c)
        for m in discovered:
            key=f"matcher:{m['source_file_id']}:{m['vid']:04x}:{m['pid']:04x}"
            c.execute("INSERT OR IGNORE INTO source_binding_source_product_binding_inputs_staging VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (generation,key,None,m["root_id"],m["source_file_id"],None,m["vid"],m["pid"],"EXPLICIT_SOURCE_MATCHER","IDENTITY_ONLY",json.dumps(m,sort_keys=True)))
        inputs=c.execute("SELECT * FROM source_binding_source_product_binding_inputs_staging").fetchall()
        bind_rows=[]; modules={};families={};contracts={}
        # Existing direct mapping has source-local registration, module, and a
        # concrete extracted family.  Transport is present only when a concrete
        # operation contract is present for that exact family.
        for inp in inputs:
            if inp["registry_product_id"] is None: continue
            mappings=c.execute("""SELECT d.source_file_id,d.protocol_family_id,pf.family_key
              FROM device_protocol_mappings d JOIN source_files sf ON sf.id=d.source_file_id
              JOIN protocol_families pf ON pf.id=d.protocol_family_id
              WHERE d.product_id=? AND sf.source_root_id=? AND d.mapping_basis='direct_vid_pid_in_same_source' AND d.confidence>=.8""",
                (inp["registry_product_id"],inp["source_root_id"])).fetchall()
            for d in mappings:
                module=f"module:{inp['source_root_id']}:{d['source_file_id']}"; family=f"family:{d['protocol_family_id']}"
                modules[module]=(generation,module,inp["source_root_id"],d["source_file_id"],"registered_implementation_module",json.dumps({"mapping_basis":"direct_vid_pid_in_same_source","input":inp["input_key"]},sort_keys=True))
                families[family]=(generation,family,inp["source_root_id"],module,d["protocol_family_id"],"explicit_device_protocol_mapping",json.dumps({"protocol_family_key":d["family_key"]},sort_keys=True))
                transports=_transport_rows(c,d["protocol_family_id"])
                if not transports:
                    bind_rows.append((generation,inp["input_key"],module,family,None,"registration_to_module_to_family","FAMILY_BOUND",json.dumps({"source_file_id":d["source_file_id"],"reason":"no concrete operation transport contract in this family"},sort_keys=True)))
                for t in transports:
                    ck=f"transport:{module}:{t['transport']}";contracts[ck]=(generation,ck,inp["source_root_id"],module,t["transport"],json.dumps(t,sort_keys=True))
                    bind_rows.append((generation,inp["input_key"],module,family,ck,"registration_to_module_to_family_to_transport","COMPLETE_STATIC_BINDING",json.dumps({"source_file_id":d["source_file_id"],"protocol_family_id":d["protocol_family_id"]},sort_keys=True)))
        # Registration-derived inputs have concrete module evidence but are not
        # assigned a family from its filename.  This preserves the no-name rule.
        for inp in inputs:
            if inp["identity_origin"] != "EXPLICIT_SOURCE_MATCHER": continue
            module=f"module:{inp['source_root_id']}:{inp['source_file_id']}"
            modules[module]=(generation,module,inp["source_root_id"],inp["source_file_id"],"explicit_registration_module",inp["provenance_json"])
            bind_rows.append((generation,inp["input_key"],module,None,None,"explicit_matcher_to_module","MODULE_BOUND",inp["provenance_json"]))
        c.executemany("INSERT OR IGNORE INTO source_binding_implementation_modules_staging VALUES(?,?,?,?,?,?)",modules.values())
        c.executemany("INSERT OR IGNORE INTO source_binding_source_internal_protocol_families_staging VALUES(?,?,?,?,?,?,?)",families.values())
        c.executemany("INSERT OR IGNORE INTO source_binding_transport_contracts_staging VALUES(?,?,?,?,?,?)",contracts.values())
        c.executemany("INSERT OR IGNORE INTO source_binding_source_product_bindings_staging VALUES(?,?,?,?,?,?,?,?)",bind_rows)
        # One explicit status per input, the strongest of any binding; all other
        # inputs are explicitly identity-only rather than silently omitted.
        rank={"IDENTITY_ONLY":0,"MODULE_BOUND":1,"FAMILY_BOUND":2,"TRANSPORT_BOUND":3,"COMPLETE_STATIC_BINDING":4,"AMBIGUOUS":5,"CONFLICTED":6}
        strongest=defaultdict(lambda:"IDENTITY_ONLY")
        for r in bind_rows:
            if rank[r[6]]>rank[strongest[r[1]]]: strongest[r[1]]=r[6]
        for inp in inputs:
            if inp["input_key"] not in strongest:
                bind_rows.append((generation,inp["input_key"],None,None,None,"identity_without_host_implementation","IDENTITY_ONLY",json.dumps({"reason":"no source-internal matcher -> implementation registration graph found"},sort_keys=True)))
        # Add the explicit identity-only records.  They are intentionally kept
        # separate from a bound row, so downstream readers can explain gaps.
        for inp in inputs:
            c.execute("""INSERT OR IGNORE INTO source_binding_source_product_bindings_staging
              VALUES(?,?,?,?,?,?,?,?)""",(generation,inp["input_key"],None,None,None,"identity_without_host_implementation","IDENTITY_ONLY",json.dumps({"reason":"no source-internal matcher -> implementation registration graph found"},sort_keys=True)))
        counts={name:c.execute(f"SELECT count(*) FROM source_binding_{name}_staging").fetchone()[0] for name in ("source_product_binding_inputs","implementation_modules","source_internal_protocol_families","transport_contracts","source_product_bindings")}
        orphan=c.execute("""SELECT count(*) FROM source_binding_source_product_bindings_staging b
          LEFT JOIN source_binding_source_product_binding_inputs_staging i ON i.input_key=b.input_key AND i.generation_id=b.generation_id
          WHERE i.input_key IS NULL""").fetchone()[0]
        statuses=Counter()
        root_statuses: dict[str, Counter[str]] = defaultdict(Counter)
        roots={r["id"]:r["root_name"] for r in c.execute("SELECT id,root_name FROM source_roots")}
        unresolved_reasons=Counter()
        for inp in inputs:
            status=strongest[inp["input_key"]]
            statuses[status]+=1
            root_statuses[roots[inp["source_root_id"]]][status]+=1
            if status == "IDENTITY_ONLY":
                if inp["identity_origin"] == "TYPED_DEVICE_IDENTITY":
                    unresolved_reasons["identity/catalog metadata has no host-side implementation registration"] += 1
                else:
                    unresolved_reasons["no source-internal matcher -> implementation registration graph found"] += 1
        cross=c.execute("""SELECT cp.id FROM canonical_products cp JOIN canonical_product_members cm ON cm.canonical_product_id=cp.id AND cm.generation_id=cp.generation_id
           JOIN source_product_identities si ON si.id=cm.source_product_identity_id
           JOIN source_binding_source_product_binding_inputs_staging i ON i.source_product_identity_id=si.id
           JOIN source_binding_source_product_bindings_staging b ON b.input_key=i.input_key AND b.generation_id=i.generation_id
           WHERE cp.generation_id=? AND b.binding_status IN ('FAMILY_BOUND','TRANSPORT_BOUND','COMPLETE_STATIC_BINDING')
           GROUP BY cp.id HAVING count(DISTINCT i.source_root_id)>=2""",(product_generation,)).fetchall()
        audit={"generation_id":generation,"product_identity_generation_id":product_generation,"source_products_total":len(inputs),"status_counts":dict(statuses),"by_source_root":{root:dict(values) for root,values in sorted(root_statuses.items())},"new_registration_matcher_inputs":len(discovered),"new_source_local_protocol_families":len(families),"new_product_family_bindings":sum(1 for r in bind_rows if r[6] in ('FAMILY_BOUND','COMPLETE_STATIC_BINDING')),"ambiguous_bindings":0,"conflicts":0,"canonical_products_with_family_bindings_in_2_roots":len(cross),"top_unresolved_bindings":[{"reason":reason,"count":count} for reason,count in unresolved_reasons.most_common(10)],"staging_row_counts":counts,"orphan_bindings":orphan,"invariant_result":"PASS" if not orphan else "FAIL"}
        if orphan: raise RuntimeError("orphan source product binding")
        c.commit();c.execute("BEGIN IMMEDIATE")
        c.execute("UPDATE source_product_binding_generations SET status='SUPERSEDED' WHERE status='ACTIVE'")
        for table in ("source_product_binding_inputs","implementation_modules","source_internal_protocol_families","transport_contracts","source_product_bindings"):
            c.execute(f"INSERT INTO {table} SELECT * FROM source_binding_{table}_staging")
        c.execute("INSERT INTO source_product_binding_generations(generation_id,status,product_identity_generation_id,audit_json) VALUES(?,?,?,?)",(generation,"ACTIVE",product_generation,json.dumps(audit,sort_keys=True)))
        c.execute("INSERT INTO source_product_binding_active_generation VALUES(1,?) ON CONFLICT(singleton) DO UPDATE SET generation_id=excluded.generation_id",(generation,))
        c.commit();report_path.parent.mkdir(parents=True,exist_ok=True);report_path.write_text(json.dumps(audit|{"published":True},indent=2),encoding="utf8")
        return audit|{"published":True}
