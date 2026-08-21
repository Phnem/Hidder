"""Atomic, evidence-only cross-source physical-product identity resolution.

The source identities are immutable observations.  CanonicalProduct is a
generation-scoped view over *confirmed* cross-root observations; it never
rewrites products, device identifiers, or source-family mappings.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


STATUSES = (
    "UNRESOLVED", "VID_PID_OVERLAP", "IDENTITY_COMPATIBLE",
    "EXACT_PRODUCT_CONFIRMED", "REVISION_VARIANT", "AMBIGUOUS", "CONFLICTED",
)


def _norm(value: str | None, vendor: str | None = None) -> str | None:
    if not value:
        return None
    out = re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()
    # A vendor prefix is formatting, not a model discriminator, but the vendor
    # itself remains a separate equality check below.
    if vendor:
        prefix = re.sub(r"[^a-z0-9]+", " ", vendor.casefold()).strip()
        if prefix and out.startswith(prefix + " "):
            out = out[len(prefix):].strip()
    return out or None


def _parse_int(value: Any) -> int | None:
    try:
        return int(str(value), 0)
    except (TypeError, ValueError):
        return None


def _json_identity_metadata(root_path: str, relative_path: str) -> dict[str, Any]:
    """Read only explicit device metadata from the declaring JSON source."""
    path = Path(root_path) / relative_path
    if path.suffix.lower() != ".json" or not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    if not isinstance(value, dict):
        return {}
    usb = value.get("usb") if isinstance(value.get("usb"), dict) else {}
    name = next((value.get(key) for key in ("product_name", "keyboard_name", "model", "name")
                 if isinstance(value.get(key), str) and value.get(key).strip()), None)
    maker = next((value.get(key) for key in ("manufacturer", "vendor", "brand")
                  if isinstance(value.get(key), str) and value.get(key).strip()), None)
    return {
        "product_name": name,
        "manufacturer": maker,
        "bcd_device": usb.get("device_version") or usb.get("bcdDevice"),
    }


def _schemas(c: sqlite3.Connection) -> None:
    c.execute("""CREATE TABLE IF NOT EXISTS product_identity_generations(
        generation_id TEXT PRIMARY KEY, status TEXT NOT NULL, audit_json TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
    c.execute("""CREATE TABLE IF NOT EXISTS product_identity_active_generation(
        singleton INTEGER PRIMARY KEY CHECK(singleton=1), generation_id TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS source_product_identities(
        id INTEGER PRIMARY KEY, generation_id TEXT NOT NULL, source_root_id INTEGER NOT NULL,
        source_file_id INTEGER, typed_fact_id INTEGER, registry_product_id INTEGER,
        source_scope_key TEXT, vid INTEGER NOT NULL, pid INTEGER NOT NULL, bcd_device TEXT,
        manufacturer TEXT, product_name TEXT, normalized_manufacturer TEXT,
        normalized_product_name TEXT, origin TEXT NOT NULL, identity_status TEXT NOT NULL,
        provenance_json TEXT NOT NULL)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_source_product_identity_lookup ON source_product_identities(generation_id,vid,pid,source_root_id)")
    c.execute("""CREATE TABLE IF NOT EXISTS canonical_products(
        id INTEGER PRIMARY KEY, generation_id TEXT NOT NULL, canonical_key TEXT NOT NULL,
        display_name TEXT, vid INTEGER NOT NULL, pid INTEGER NOT NULL, status TEXT NOT NULL,
        created_from_evidence_json TEXT NOT NULL,
        UNIQUE(generation_id,canonical_key))""")
    c.execute("""CREATE TABLE IF NOT EXISTS canonical_product_members(
        generation_id TEXT NOT NULL, canonical_product_id INTEGER NOT NULL,
        source_product_identity_id INTEGER NOT NULL, membership_status TEXT NOT NULL,
        PRIMARY KEY(generation_id,canonical_product_id,source_product_identity_id))""")
    c.execute("""CREATE TABLE IF NOT EXISTS product_identity_evidence(
        id INTEGER PRIMARY KEY, generation_id TEXT NOT NULL, source_identity_a INTEGER NOT NULL,
        source_identity_b INTEGER NOT NULL, status TEXT NOT NULL, exact_fields_json TEXT NOT NULL,
        lineage_json TEXT NOT NULL, rationale TEXT NOT NULL,
        UNIQUE(generation_id,source_identity_a,source_identity_b))""")
    c.execute("""CREATE TABLE IF NOT EXISTS product_identity_conflicts(
        id INTEGER PRIMARY KEY, generation_id TEXT NOT NULL, vid INTEGER NOT NULL, pid INTEGER NOT NULL,
        conflict_type TEXT NOT NULL, details_json TEXT NOT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS canonical_product_source_families(
        generation_id TEXT NOT NULL, canonical_product_id INTEGER NOT NULL,
        source_root_id INTEGER NOT NULL, source_product_identity_id INTEGER NOT NULL,
        source_family_key TEXT NOT NULL, protocol_family_id INTEGER,
        mapping_kind TEXT NOT NULL, provenance_json TEXT NOT NULL,
        PRIMARY KEY(generation_id,canonical_product_id,source_product_identity_id,source_family_key,mapping_kind))""")


def _stage(c: sqlite3.Connection) -> None:
    for table in ("source_product_identities", "canonical_products", "canonical_product_members",
                  "product_identity_evidence", "product_identity_conflicts", "canonical_product_source_families"):
        staging = f"product_identity_{table}_staging"
        c.execute(f"DROP TABLE IF EXISTS {staging}")
        c.execute(f"CREATE TABLE {staging} AS SELECT * FROM {table} WHERE 0")


def _input_identities(c: sqlite3.Connection) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    # Existing static mapping gives the source-root-local VID/PID -> product link.
    for r in c.execute("""SELECT DISTINCT sr.id root_id,sr.root_name,sf.id source_file_id,
               d.product_id,p.canonical_name,v.display_name vendor_name,
               di.vid,di.pid,di.manufacturer_string,di.product_string
            FROM device_protocol_mappings d
            JOIN source_files sf ON sf.id=d.source_file_id JOIN source_roots sr ON sr.id=sf.source_root_id
            JOIN products p ON p.id=d.product_id JOIN vendors v ON v.id=p.vendor_id
            JOIN device_identifiers di ON di.product_id=p.id
            WHERE d.mapping_basis='direct_vid_pid_in_same_source' AND d.confidence>=0.8"""):
        name = r["product_string"] or r["canonical_name"]
        maker = r["manufacturer_string"] or r["vendor_name"]
        rows.append(dict(source_root_id=r["root_id"], source_file_id=r["source_file_id"], typed_fact_id=None,
            registry_product_id=r["product_id"], source_scope_key=None, vid=r["vid"], pid=r["pid"],
            bcd_device=None, manufacturer=maker, product_name=name, origin="STATIC_DEVICE_PROTOCOL_MAPPING",
            provenance={"mapping_basis":"direct_vid_pid_in_same_source","source_file_id":r["source_file_id"],
                        "registry_product_id":r["product_id"]}))
    # Typed DeviceIdentity facts are source-local identities, even when there is
    # no registry product yet.  JSON metadata is read only from the same source file.
    for r in c.execute("""SELECT tf.id typed_fact_id,tf.scope_key,tf.canonical_value_json,
               sf.id source_file_id,sf.relative_path,sr.id root_id,sr.local_path
            FROM typed_facts tf JOIN typed_fact_evidence te ON te.typed_fact_id=tf.id
            JOIN source_files sf ON sf.id=te.source_file_id JOIN source_roots sr ON sr.id=sf.source_root_id
            WHERE tf.fact_type='DeviceIdentity' AND tf.semantic_type='device.usb_identity'"""):
        try:
            raw = json.loads(r["canonical_value_json"])
            vid, pid = _parse_int(raw.get("vid")), _parse_int(raw.get("pid"))
        except (json.JSONDecodeError, AttributeError):
            continue
        if vid is None or pid is None:
            continue
        meta = _json_identity_metadata(r["local_path"], r["relative_path"])
        rows.append(dict(source_root_id=r["root_id"], source_file_id=r["source_file_id"],
            typed_fact_id=r["typed_fact_id"], registry_product_id=None, source_scope_key=r["scope_key"],
            vid=vid, pid=pid, bcd_device=meta.get("bcd_device"), manufacturer=meta.get("manufacturer"),
            product_name=meta.get("product_name"), origin="TYPED_DEVICE_IDENTITY",
            provenance={"typed_fact_id":r["typed_fact_id"],"source_file_id":r["source_file_id"],
                        "scope_key":r["scope_key"],"identity_value":raw}))
    # A source file can evidence more than one fact, but an identity observation
    # must have one deterministic row.
    unique: dict[tuple[Any, ...], dict[str, Any]] = {}
    for x in rows:
        x["normalized_manufacturer"] = _norm(x["manufacturer"])
        x["normalized_product_name"] = _norm(x["product_name"], x["manufacturer"])
        key = (x["source_root_id"], x["source_file_id"], x["typed_fact_id"], x["registry_product_id"],
               x["source_scope_key"], x["vid"], x["pid"], x["normalized_product_name"])
        unique[key] = x
    return [unique[k] for k in sorted(unique, key=lambda z: tuple("" if v is None else str(v) for v in z))]


def run_cross_source_product_identity_resolution(db_path: Path, report_path: Path) -> dict[str, Any]:
    generation = "product-identity-" + uuid.uuid4().hex
    with sqlite3.connect(db_path) as c:
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON"); c.execute("PRAGMA journal_mode=WAL")
        _schemas(c); _stage(c)
        identities = _input_identities(c)
        source_id_base = c.execute("SELECT coalesce(max(id),0) FROM source_product_identities").fetchone()[0]
        canonical_id_base = c.execute("SELECT coalesce(max(id),0) FROM canonical_products").fetchone()[0]
        for offset, x in enumerate(identities, 1):
            x["staging_id"] = source_id_base + offset
            c.execute("""INSERT INTO product_identity_source_product_identities_staging
                (id,generation_id,source_root_id,source_file_id,typed_fact_id,registry_product_id,source_scope_key,
                 vid,pid,bcd_device,manufacturer,product_name,normalized_manufacturer,normalized_product_name,
                 origin,identity_status,provenance_json)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (x["staging_id"],generation,x["source_root_id"],x["source_file_id"],x["typed_fact_id"],x["registry_product_id"],
                 x["source_scope_key"],x["vid"],x["pid"],x["bcd_device"],x["manufacturer"],x["product_name"],
                 x["normalized_manufacturer"],x["normalized_product_name"],x["origin"],"UNRESOLVED",
                 json.dumps(x["provenance"],sort_keys=True)))
        staged = c.execute("SELECT * FROM product_identity_source_product_identities_staging ORDER BY id").fetchall()
        by_vp: dict[tuple[int,int], list[sqlite3.Row]] = defaultdict(list)
        for x in staged: by_vp[(x["vid"],x["pid"])].append(x)
        exact_edges: list[tuple[int,int,dict[str,Any]]] = []
        evidence_status = Counter(); conflicts = []
        identity_status: dict[int, str] = {}
        priority = {"UNRESOLVED": 0, "VID_PID_OVERLAP": 1, "IDENTITY_COMPATIBLE": 2,
                    "AMBIGUOUS": 3, "REVISION_VARIANT": 4, "EXACT_PRODUCT_CONFIRMED": 5,
                    "CONFLICTED": 6}
        for (vid,pid), group in sorted(by_vp.items()):
            roots = {x["source_root_id"] for x in group}
            if len(roots) < 2:
                continue
            registry = c.execute("""SELECT DISTINCT p.id,p.canonical_name,v.display_name vendor_name
                FROM device_identifiers di JOIN products p ON p.id=di.product_id JOIN vendors v ON v.id=p.vendor_id
                WHERE di.vid=? AND di.pid=?""",(vid,pid)).fetchall()
            registry_models = {_norm(r["canonical_name"],r["vendor_name"]) for r in registry}
            registry_models.discard(None)
            if len(registry_models) > 1:
                conflicts.append((generation,vid,pid,"MULTI_MODEL_VID_PID",json.dumps({"registry_product_ids":[r["id"] for r in registry],"models":sorted(registry_models)})))
            for i,a in enumerate(group):
                for b in group[i+1:]:
                    if a["source_root_id"] == b["source_root_id"]:
                        continue
                    names_equal = bool(a["normalized_product_name"] and a["normalized_product_name"] == b["normalized_product_name"])
                    makers_compatible = not (a["normalized_manufacturer"] and b["normalized_manufacturer"]) or a["normalized_manufacturer"] == b["normalized_manufacturer"]
                    exact = (len(registry_models) == 1) or (names_equal and makers_compatible)
                    if exact:
                        status="EXACT_PRODUCT_CONFIRMED"; rationale="unique registry model for VID/PID" if len(registry_models)==1 else "same VID/PID plus exact normalized source model and compatible manufacturer"
                    elif a["normalized_product_name"] and b["normalized_product_name"]:
                        status="AMBIGUOUS"; rationale="multi-model VID/PID; explicit source model discriminators differ"
                    else:
                        status="VID_PID_OVERLAP"; rationale="VID/PID overlaps across roots but multi-model registry group lacks two source-side discriminators"
                    fields={"vid":vid,"pid":pid,"registry_models":sorted(registry_models),"product_name_a":a["product_name"],"product_name_b":b["product_name"],"manufacturer_a":a["manufacturer"],"manufacturer_b":b["manufacturer"],"bcd_device_a":a["bcd_device"],"bcd_device_b":b["bcd_device"]}
                    c.execute("""INSERT INTO product_identity_product_identity_evidence_staging
                        (generation_id,source_identity_a,source_identity_b,status,exact_fields_json,lineage_json,rationale)
                        VALUES(?,?,?,?,?,?,?)""",(generation,a["id"],b["id"],status,json.dumps(fields,sort_keys=True),json.dumps(sorted({a["source_root_id"],b["source_root_id"]})),rationale))
                    evidence_status[status]+=1
                    for identity_id in (a["id"], b["id"]):
                        previous = identity_status.get(identity_id, "UNRESOLVED")
                        if priority[status] > priority[previous]:
                            identity_status[identity_id] = status
                    if status == "EXACT_PRODUCT_CONFIRMED": exact_edges.append((a["id"],b["id"],fields))
        for identity_id, status in identity_status.items():
            c.execute("UPDATE product_identity_source_product_identities_staging SET identity_status=? WHERE id=?", (status, identity_id))
        c.executemany("""INSERT INTO product_identity_product_identity_conflicts_staging
            (generation_id,vid,pid,conflict_type,details_json) VALUES(?,?,?,?,?)""", conflicts)
        # Exact edges form connected components; each component is a single
        # canonical product, while source products remain untouched.
        parent={x["id"]:x["id"] for x in staged}
        def find(x: int) -> int:
            while parent[x] != x:
                parent[x]=parent[parent[x]]; x=parent[x]
            return x
        def union(a: int,b: int) -> None:
            a,b=find(a),find(b)
            if a!=b: parent[max(a,b)]=min(a,b)
        for a,b,_ in exact_edges: union(a,b)
        components: dict[int,list[int]] = defaultdict(list)
        for a,b,_ in exact_edges: components[find(a)].extend((a,b))
        identities_by_id={x["id"]:x for x in staged}
        members_by_cp: dict[int,int] = {}
        for component_offset, (root, member_ids) in enumerate(sorted(components.items()), 1):
            mids=sorted(set(member_ids)); first=identities_by_id[mids[0]]
            named=next((identities_by_id[mid]["product_name"] for mid in mids if identities_by_id[mid]["product_name"]),None)
            key=f"usb:{first['vid']:04x}:{first['pid']:04x}:{_norm(named) or 'unique-registry-model'}"
            source_edges=[fields for a,b,fields in exact_edges if a in mids and b in mids]
            c.execute("""INSERT INTO product_identity_canonical_products_staging
                (id,generation_id,canonical_key,display_name,vid,pid,status,created_from_evidence_json)
                VALUES(?,?,?,?,?,?,?,?)""",(canonical_id_base+component_offset,generation,key,named,first["vid"],first["pid"],"EXACT_PRODUCT_CONFIRMED",json.dumps(source_edges,sort_keys=True)))
            cp=canonical_id_base+component_offset
            for mid in mids:
                members_by_cp[mid]=cp
                c.execute("INSERT INTO product_identity_canonical_product_members_staging VALUES(?,?,?,?)",(generation,cp,mid,"EXACT_PRODUCT_CONFIRMED"))
        # Product -> source-family edges are retained separately from identity.
        for mid,cp in sorted(members_by_cp.items()):
            x=identities_by_id[mid]
            if x["registry_product_id"] is not None:
                for r in c.execute("""SELECT DISTINCT pf.id,pf.family_key,d.mapping_basis,d.source_file_id
                    FROM device_protocol_mappings d JOIN protocol_families pf ON pf.id=d.protocol_family_id
                    JOIN source_files sf ON sf.id=d.source_file_id
                    WHERE d.product_id=? AND sf.source_root_id=?""",(x["registry_product_id"],x["source_root_id"])):
                    c.execute("INSERT OR IGNORE INTO product_identity_canonical_product_source_families_staging VALUES(?,?,?,?,?,?,?,?)",
                        (generation,cp,x["source_root_id"],mid,r["family_key"],r["id"],"EXPLICIT_DEVICE_PROTOCOL_MAPPING",json.dumps({"source_file_id":r["source_file_id"],"mapping_basis":r["mapping_basis"]},sort_keys=True)))
            if x["source_scope_key"]:
                c.execute("INSERT OR IGNORE INTO product_identity_canonical_product_source_families_staging VALUES(?,?,?,?,?,?,?,?)",
                    (generation,cp,x["source_root_id"],mid,x["source_scope_key"],None,"DECLARED_SOURCE_SCOPE",json.dumps({"typed_fact_id":x["typed_fact_id"],"scope":"DeviceIdentity"},sort_keys=True)))
        # Audit the complete staging generation before one publish transaction.
        staging_counts={name:c.execute(f"SELECT count(*) FROM product_identity_{name}_staging").fetchone()[0]
            for name in ("source_product_identities","canonical_products","canonical_product_members","product_identity_evidence","product_identity_conflicts","canonical_product_source_families")}
        bad_member=c.execute("""SELECT count(*) FROM product_identity_canonical_product_members_staging m
            LEFT JOIN product_identity_canonical_products_staging p ON p.id=m.canonical_product_id
            LEFT JOIN product_identity_source_product_identities_staging s ON s.id=m.source_product_identity_id
            WHERE p.id IS NULL OR s.id IS NULL OR p.generation_id<>m.generation_id OR s.generation_id<>m.generation_id""").fetchone()[0]
        canonical_cross_roots=c.execute("""SELECT count(*) FROM (SELECT canonical_product_id FROM product_identity_canonical_product_members_staging m
            JOIN product_identity_source_product_identities_staging s ON s.id=m.source_product_identity_id
            GROUP BY canonical_product_id HAVING count(DISTINCT s.source_root_id)>=2)""").fetchone()[0]
        root_coverage = Counter()
        for r in c.execute("""SELECT m.canonical_product_id,count(DISTINCT s.source_root_id) roots
            FROM product_identity_canonical_product_members_staging m JOIN product_identity_source_product_identities_staging s
              ON s.id=m.source_product_identity_id GROUP BY m.canonical_product_id"""):
            root_coverage[str(r["roots"] if r["roots"] < 4 else "4+")] += 1
        family_roots = c.execute("""SELECT count(*) FROM (SELECT canonical_product_id
            FROM product_identity_canonical_product_source_families_staging GROUP BY canonical_product_id
            HAVING count(DISTINCT source_root_id)>=2)""").fetchone()[0]
        top_products=[]
        for r in c.execute("""SELECT cp.id,cp.display_name,cp.vid,cp.pid,
              group_concat(DISTINCT sr.root_name) roots,count(DISTINCT m.source_product_identity_id) members
              FROM product_identity_canonical_products_staging cp
              JOIN product_identity_canonical_product_members_staging m ON m.canonical_product_id=cp.id
              JOIN product_identity_source_product_identities_staging s ON s.id=m.source_product_identity_id
              JOIN source_roots sr ON sr.id=s.source_root_id GROUP BY cp.id ORDER BY members DESC,cp.display_name LIMIT 25"""):
            fam=[dict(x) for x in c.execute("""SELECT source_family_key,mapping_kind FROM product_identity_canonical_product_source_families_staging
                WHERE canonical_product_id=? ORDER BY source_family_key""",(r["id"],))]
            top_products.append({"canonical_product":r["display_name"],"vid_pid":f"{r['vid']:04x}:{r['pid']:04x}","source_roots":r["roots"].split(","),"members":r["members"],"source_families":fam})
        audit={"generation_id":generation,"source_product_identities_total":len(staged),"canonical_products_total":staging_counts["canonical_products"],
          "exact_product_confirmations":evidence_status["EXACT_PRODUCT_CONFIRMED"],"revision_variants":evidence_status["REVISION_VARIANT"],
          "ambiguous_groups":evidence_status["AMBIGUOUS"],"vid_pid_overlap":evidence_status["VID_PID_OVERLAP"],"conflicts":len(conflicts),
          "cross_source_canonical_products":canonical_cross_roots,"cross_source_products_by_roots":{"2":root_coverage["2"],"3":root_coverage["3"],"4+":root_coverage["4+"]},
          "canonical_products_with_source_family_mappings_in_2_roots":family_roots,
          "staging_row_counts":staging_counts,"orphan_members":bad_member,"invariant_result":"PASS" if bad_member==0 else "FAIL",
          "top_cross_source_products":top_products}
        if bad_member: raise RuntimeError("product identity staging orphan members")
        # Persist diagnostic-only staging first.  The following transaction is
        # the sole production publication boundary.
        c.commit()
        c.execute("BEGIN IMMEDIATE")
        c.execute("UPDATE product_identity_generations SET status='SUPERSEDED' WHERE status='ACTIVE'")
        for name in ("source_product_identities","canonical_products","canonical_product_members","product_identity_evidence","product_identity_conflicts","canonical_product_source_families"):
            c.execute(f"INSERT INTO {name} SELECT * FROM product_identity_{name}_staging")
        c.execute("INSERT INTO product_identity_generations(generation_id,status,audit_json) VALUES(?,?,?)",(generation,"ACTIVE",json.dumps(audit,sort_keys=True)))
        c.execute("INSERT INTO product_identity_active_generation VALUES(1,?) ON CONFLICT(singleton) DO UPDATE SET generation_id=excluded.generation_id",(generation,))
        c.commit()
        report_path.parent.mkdir(parents=True,exist_ok=True); report_path.write_text(json.dumps(audit|{"published":True},indent=2),encoding="utf8")
        return audit|{"published":True}
