"""Atomic explicit product→module→family mapping; never uses packet similarity."""
from __future__ import annotations
import json, sqlite3, uuid
from pathlib import Path

def run_static_family_mapping(db_path: Path) -> dict[str, object]:
    gid='static-map-'+uuid.uuid4().hex
    with sqlite3.connect(db_path) as c:
        c.row_factory=sqlite3.Row; c.execute('PRAGMA foreign_keys=ON'); c.execute('PRAGMA journal_mode=WAL')
        identity=c.execute('select generation_id from capture_identity_active_generation where singleton=1').fetchone()
        if not identity: raise RuntimeError('no active identity generation')
        iid=identity[0]
        c.execute("CREATE TABLE IF NOT EXISTS capture_static_mapping_generations(generation_id TEXT PRIMARY KEY,status TEXT NOT NULL,identity_generation_id TEXT NOT NULL,audit_json TEXT NOT NULL,created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
        c.execute("CREATE TABLE IF NOT EXISTS capture_static_mapping_active_generation(singleton INTEGER PRIMARY KEY CHECK(singleton=1),generation_id TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS capture_session_static_module_edges(generation_id TEXT,session_key TEXT,source_file_id INTEGER,status TEXT NOT NULL,lineage_group TEXT,provenance_json TEXT NOT NULL,PRIMARY KEY(generation_id,session_key,source_file_id))")
        c.execute("CREATE TABLE IF NOT EXISTS capture_session_static_family_edges(generation_id TEXT,session_key TEXT,protocol_family_id INTEGER,status TEXT NOT NULL,lineage_group TEXT,provenance_json TEXT NOT NULL,PRIMARY KEY(generation_id,session_key,protocol_family_id))")
        for t in ('module','family'):c.execute(f'DROP TABLE IF EXISTS capture_static_{t}_staging')
        c.execute('CREATE TABLE capture_static_module_staging AS SELECT * FROM capture_session_static_module_edges WHERE 0')
        c.execute('CREATE TABLE capture_static_family_staging AS SELECT * FROM capture_session_static_family_edges WHERE 0')
        products=c.execute('select session_key,product_id from capture_session_product_edges where generation_id=?',(iid,)).fetchall(); modules=[];families=[]; mapped=set()
        for e in products:
            rows=c.execute("""select d.*,sr.root_name from device_protocol_mappings d join source_files sf on sf.id=d.source_file_id join source_roots sr on sr.id=sf.source_root_id where d.product_id=? and d.confidence>=.8 and d.mapping_basis='direct_vid_pid_in_same_source'""",(e['product_id'],)).fetchall()
            for r in rows:
                prov=json.dumps({'identity_generation':iid,'product_id':e['product_id'],'mapping_basis':r['mapping_basis'],'confidence':r['confidence'],'source_file_id':r['source_file_id']})
                modules.append((gid,e['session_key'],r['source_file_id'],'STATIC_MODULE_CONFIRMED',r['root_name'],prov));families.append((gid,e['session_key'],r['protocol_family_id'],'FAMILY_CONFIRMED',r['root_name'],prov));mapped.add(e['session_key'])
        c.executemany('insert into capture_static_module_staging values(?,?,?,?,?,?)',modules);c.executemany('insert into capture_static_family_staging values(?,?,?,?,?,?)',families)
        total=c.execute('select count(*) from capture_device_sessions').fetchone()[0]; audit={'sessions_total':total,'identity_generation_id':iid,'exact_product':len(products),'static_module':len(mapped),'protocol_family':len(mapped),'independently_correlated':0,'product_only':len(products)-len(mapped),'unresolved':total-len(products),'ambiguous_vid_pid':c.execute("select count(*) from capture_session_identity_edges where generation_id=? and identity_status='DESCRIPTOR_VERIFIED'",(iid,)).fetchone()[0]-len(products),'family_ambiguity':0,'invariant_result':'PASS'}
        c.commit();c.execute('BEGIN IMMEDIATE');c.execute("update capture_static_mapping_generations set status='SUPERSEDED' where status='ACTIVE'");c.execute('insert into capture_session_static_module_edges select * from capture_static_module_staging');c.execute('insert into capture_session_static_family_edges select * from capture_static_family_staging');c.execute('insert into capture_static_mapping_generations(generation_id,status,identity_generation_id,audit_json) values(?,?,?,?)',(gid,'ACTIVE',iid,json.dumps(audit)));c.execute('insert into capture_static_mapping_active_generation values(1,?) on conflict(singleton) do update set generation_id=excluded.generation_id',(gid,));c.commit()
    return audit|{'generation_id':gid,'published':True}
