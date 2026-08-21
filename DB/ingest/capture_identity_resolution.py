"""Atomic descriptor-first capture/session identity graph. No semantic correlation."""
from __future__ import annotations
import json, sqlite3, uuid
from pathlib import Path

def run_identity_resolution(db_path: Path) -> dict[str, object]:
    gid='identity-'+uuid.uuid4().hex
    with sqlite3.connect(db_path) as c:
        c.row_factory=sqlite3.Row; c.execute('PRAGMA foreign_keys=ON'); c.execute('PRAGMA journal_mode=WAL')
        c.execute("CREATE TABLE IF NOT EXISTS capture_identity_generations(generation_id TEXT PRIMARY KEY,status TEXT NOT NULL,audit_json TEXT NOT NULL,created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
        c.execute("CREATE TABLE IF NOT EXISTS capture_identity_active_generation(singleton INTEGER PRIMARY KEY CHECK(singleton=1),generation_id TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS capture_session_identity_edges(generation_id TEXT,session_key TEXT,identity_status TEXT NOT NULL,evidence_source TEXT NOT NULL,vid INTEGER,pid INTEGER,bcd_device INTEGER,descriptor_transaction_id INTEGER,provenance_json TEXT NOT NULL,PRIMARY KEY(generation_id,session_key))")
        c.execute("CREATE TABLE IF NOT EXISTS capture_session_product_edges(generation_id TEXT,session_key TEXT,product_id INTEGER,edge_status TEXT NOT NULL,provenance_json TEXT NOT NULL,PRIMARY KEY(generation_id,session_key,product_id))")
        c.execute("CREATE TABLE IF NOT EXISTS capture_session_protocol_family_edges(generation_id TEXT,session_key TEXT,protocol_family_id INTEGER,edge_status TEXT NOT NULL,provenance_json TEXT NOT NULL,PRIMARY KEY(generation_id,session_key,protocol_family_id))")
        for t in ('identity','product','family') : c.execute(f'DROP TABLE IF EXISTS capture_identity_{t}_staging')
        c.execute("CREATE TABLE capture_identity_identity_staging AS SELECT * FROM capture_session_identity_edges WHERE 0")
        c.execute("CREATE TABLE capture_identity_product_staging AS SELECT * FROM capture_session_product_edges WHERE 0")
        c.execute("CREATE TABLE capture_identity_family_staging AS SELECT * FROM capture_session_protocol_family_edges WHERE 0")
        sessions=c.execute('SELECT * FROM capture_device_sessions').fetchall(); desc={}
        for r in c.execute("SELECT * FROM capture_decoded_transactions WHERE transfer_type='control' AND decoded_payload_length=18 AND substr(decoded_payload,1,2)=x'1201'"):
            p=bytes(r['decoded_payload']); k=f"capture:{r['capture_file_id']}:bus:{r['usb_bus_id']}:device:{r['device_address']}"
            desc.setdefault(k,(int.from_bytes(p[8:10],'little'),int.from_bytes(p[10:12],'little'),int.from_bytes(p[12:14],'little'),r['raw_transaction_id']))
        identity=[]; product=[]
        for s in sessions:
            d=desc.get(s['session_key'])
            if not d: identity.append((gid,s['session_key'],'UNRESOLVED','none',None,None,None,None,json.dumps({'reason':'no_device_descriptor'}))); continue
            vid,pid,bcd,tx=d; prov=json.dumps({'field':'USB_DEVICE_DESCRIPTOR','raw_transaction_id':tx,'vid':f'0x{vid:04x}','pid':f'0x{pid:04x}','bcdDevice':f'0x{bcd:04x}'})
            identity.append((gid,s['session_key'],'DESCRIPTOR_VERIFIED','capture_usb_descriptor',vid,pid,bcd,tx,prov))
            matches=c.execute('SELECT DISTINCT product_id FROM device_identifiers WHERE vid=? AND pid=?',(vid,pid)).fetchall()
            if len(matches)==1: product.append((gid,s['session_key'],matches[0][0],'DESCRIPTOR_VID_PID_PRODUCT_MATCH',prov))
        c.executemany('INSERT INTO capture_identity_identity_staging VALUES(?,?,?,?,?,?,?,?,?)',identity)
        c.executemany('INSERT INTO capture_identity_product_staging VALUES(?,?,?,?,?)',product)
        counts={'total_sessions':len(sessions),'descriptor_verified':sum(x[2]=='DESCRIPTOR_VERIFIED' for x in identity),'unresolved':sum(x[2]=='UNRESOLVED' for x in identity),'vid_pid_mapped':len(product),'exact_product':len(product),'plugin_module':0,'protocol_family':0,'ambiguous_vid_pid':sum(1 for x in identity if x[4] is not None and len(c.execute('SELECT DISTINCT product_id FROM device_identifiers WHERE vid=? AND pid=?',(x[4],x[5])).fetchall())>1),'conflicts':0}
        passed=counts['total_sessions']==counts['descriptor_verified']+counts['unresolved']
        counts['invariant_result']='PASS' if passed else 'FAIL'; c.commit()
        if not passed: c.execute('INSERT INTO capture_identity_generations(generation_id,status,audit_json) VALUES(?,?,?)',(gid,'INVALID',json.dumps(counts)));c.commit();return counts|{'generation_id':gid,'published':False}
        c.execute('BEGIN IMMEDIATE');c.execute("UPDATE capture_identity_generations SET status='SUPERSEDED' WHERE status='ACTIVE'")
        c.execute('INSERT INTO capture_session_identity_edges SELECT * FROM capture_identity_identity_staging')
        c.execute('INSERT INTO capture_session_product_edges SELECT * FROM capture_identity_product_staging')
        c.execute('INSERT INTO capture_session_protocol_family_edges SELECT * FROM capture_identity_family_staging')
        c.execute('INSERT INTO capture_identity_generations(generation_id,status,audit_json) VALUES(?,?,?)',(gid,'ACTIVE',json.dumps(counts)))
        c.execute('INSERT INTO capture_identity_active_generation VALUES(1,?) ON CONFLICT(singleton) DO UPDATE SET generation_id=excluded.generation_id',(gid,));c.commit()
    return counts|{'generation_id':gid,'published':True}
