"""Generation-scoped, non-semantic sequence derivation with atomic publication."""
from __future__ import annotations

import json, sqlite3, uuid
from collections import Counter
from pathlib import Path

PREFIX = 'capture_sequence'

def _exists(c,n): return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(n,)).fetchone() is not None

def run_sequence_generation(db_path: Path, batch_size: int=10000) -> dict[str, object]:
    gid='seq-'+uuid.uuid4().hex
    with sqlite3.connect(db_path) as c:
        c.row_factory=sqlite3.Row; c.execute('PRAGMA foreign_keys=ON'); c.execute('PRAGMA journal_mode=WAL')
        for name, sql in {
          'capture_sequence_generations':"(generation_id TEXT PRIMARY KEY,status TEXT NOT NULL,audit_json TEXT NOT NULL,created_at TEXT DEFAULT CURRENT_TIMESTAMP)",
          'capture_sequence_active_generation':"(singleton INTEGER PRIMARY KEY CHECK(singleton=1),generation_id TEXT)",
          'capture_sequence_memberships':"(generation_id TEXT,raw_transaction_id INTEGER,session_key TEXT,sequence_key TEXT,sequence_position INTEGER,frame_number INTEGER,timestamp REAL,structural_family_id INTEGER,PRIMARY KEY(generation_id,raw_transaction_id))",
          'capture_sequence_exclusions':"(generation_id TEXT,raw_transaction_id INTEGER,reason TEXT NOT NULL,PRIMARY KEY(generation_id,raw_transaction_id))",
          'capture_observed_sequences':"(generation_id TEXT,sequence_key TEXT,session_key TEXT,transaction_count INTEGER,PRIMARY KEY(generation_id,sequence_key))",
          'capture_session_family_transitions':"(generation_id TEXT,from_structural_family_id INTEGER,to_structural_family_id INTEGER,occurrences INTEGER,session_count INTEGER,PRIMARY KEY(generation_id,from_structural_family_id,to_structural_family_id))",
        }.items(): c.execute(f'CREATE TABLE IF NOT EXISTS {name}{sql}')
        for t in ('memberships','exclusions','sequences','transitions','audit'):
            c.execute(f'DROP TABLE IF EXISTS {PREFIX}_{t}_staging')
        c.execute(f'CREATE TABLE {PREFIX}_memberships_staging AS SELECT * FROM capture_sequence_memberships WHERE 0')
        c.execute(f'CREATE TABLE {PREFIX}_exclusions_staging AS SELECT * FROM capture_sequence_exclusions WHERE 0')
        c.execute(f'CREATE TABLE {PREFIX}_sequences_staging AS SELECT * FROM capture_observed_sequences WHERE 0')
        c.execute(f'CREATE TABLE {PREFIX}_transitions_staging AS SELECT * FROM capture_session_family_transitions WHERE 0')
        fam={}
        for r in c.execute('SELECT * FROM capture_structural_packet_families'):
            k=tuple(r[x] for x in ('transfer_type','direction','interface','endpoint','control_stage','bm_request_type','b_request','hid_report_type','hid_report_id','report_id_source','payload_length'))+(bytes(r['prefix_anchor']),)
            if k in fam: raise RuntimeError('ambiguous_structural_family_key')
            fam[k]=r['id']
        rows=c.execute("""SELECT d.*,s.session_key FROM capture_decoded_transactions d LEFT JOIN capture_transaction_sessions s ON s.raw_transaction_id=d.raw_transaction_id WHERE d.decode_status='decoded' ORDER BY s.session_key,d.frame_number,d.timestamp,d.raw_transaction_id""")
        pos=Counter(); lengths=Counter(); assigned=[]; excluded=[]; eligible=0
        for r in rows:
            eligible+=1; sk=r['session_key']
            if sk is None: excluded.append((gid,r['raw_transaction_id'],'missing_session')); continue
            k=tuple(r[x] for x in ('transfer_type','direction','interface','endpoint','control_stage','bm_request_type','b_request','hid_report_type','hid_report_id','report_id_source','decoded_payload_length'))+(bytes(r['decoded_payload'])[:3],)
            fid=fam.get(k)
            if fid is None: excluded.append((gid,r['raw_transaction_id'],'no_structural_family')); continue
            pos[sk]+=1; lengths[sk]+=1; assigned.append((gid,r['raw_transaction_id'],sk,sk,pos[sk],r['frame_number'],r['timestamp'],fid))
            if len(assigned)>=batch_size: c.executemany(f'INSERT INTO {PREFIX}_memberships_staging VALUES(?,?,?,?,?,?,?,?)',assigned);assigned=[]
            if len(excluded)>=batch_size: c.executemany(f'INSERT INTO {PREFIX}_exclusions_staging VALUES(?,?,?)',excluded);excluded=[]
        if assigned:c.executemany(f'INSERT INTO {PREFIX}_memberships_staging VALUES(?,?,?,?,?,?,?,?)',assigned)
        if excluded:c.executemany(f'INSERT INTO {PREFIX}_exclusions_staging VALUES(?,?,?)',excluded)
        c.executemany(f'INSERT INTO {PREFIX}_sequences_staging VALUES(?,?,?,?)',((gid,k,k,v) for k,v in lengths.items()))
        c.execute(f"""INSERT INTO {PREFIX}_transitions_staging SELECT ?,a.structural_family_id,b.structural_family_id,count(*),count(DISTINCT a.session_key) FROM {PREFIX}_memberships_staging a JOIN {PREFIX}_memberships_staging b ON b.generation_id=a.generation_id AND b.sequence_key=a.sequence_key AND b.sequence_position=a.sequence_position+1 WHERE a.structural_family_id!=b.structural_family_id GROUP BY a.structural_family_id,b.structural_family_id""",(gid,))
        assigned_n=c.execute(f'SELECT count(*) FROM {PREFIX}_memberships_staging').fetchone()[0]; excluded_n=c.execute(f'SELECT count(*) FROM {PREFIX}_exclusions_staging').fetchone()[0]
        dup=c.execute(f'SELECT count(*) FROM (SELECT raw_transaction_id FROM {PREFIX}_memberships_staging GROUP BY raw_transaction_id HAVING count(*)>1)').fetchone()[0]
        order=c.execute(f"SELECT count(*) FROM (SELECT frame_number,lag(frame_number) OVER(PARTITION BY sequence_key ORDER BY sequence_position) p FROM {PREFIX}_memberships_staging) WHERE frame_number<p").fetchone()[0]
        orphan=c.execute(f"SELECT count(*) FROM {PREFIX}_memberships_staging m LEFT JOIN {PREFIX}_sequences_staging s ON s.generation_id=m.generation_id AND s.sequence_key=m.sequence_key WHERE s.sequence_key IS NULL").fetchone()[0]
        audit={'decoded_total':eligible,'eligible':eligible,'assigned':assigned_n,'excluded':excluded_n,'excluded_by_reason':dict(c.execute(f'SELECT reason,count(*) FROM {PREFIX}_exclusions_staging GROUP BY reason')),'duplicates':dup,'ordering_violations':order,'orphans':orphan,'silently_unassigned':eligible-assigned_n-excluded_n,'sessions':len(lengths),'sequences':len(lengths),'transitions':c.execute(f'SELECT count(*) FROM {PREFIX}_transitions_staging').fetchone()[0],'control_lifecycle_pairs':c.execute('SELECT count(*) FROM capture_control_request_response_pairs').fetchone()[0],'interrupt_candidate_pairs':0}
        passed=not any((audit['silently_unassigned'],dup,order,orphan))
        audit['invariant_result']='PASS' if passed else 'FAIL'; c.commit()
        if not passed:
            c.execute('INSERT INTO capture_sequence_generations(generation_id,status,audit_json) VALUES(?,?,?)',(gid,'INVALID',json.dumps(audit)));c.commit();return audit|{'generation_id':gid,'atomic_publish_status':'not_published'}
        c.execute('BEGIN IMMEDIATE'); c.execute("UPDATE capture_sequence_generations SET status='SUPERSEDED' WHERE status='ACTIVE'")
        for base in ('memberships','exclusions','sequences','transitions'):
            target={'memberships':'capture_sequence_memberships','exclusions':'capture_sequence_exclusions','sequences':'capture_observed_sequences','transitions':'capture_session_family_transitions'}[base]
            c.execute(f'INSERT INTO {target} SELECT * FROM {PREFIX}_{base}_staging')
        c.execute('INSERT INTO capture_sequence_generations(generation_id,status,audit_json) VALUES(?,?,?)',(gid,'ACTIVE',json.dumps(audit)))
        c.execute('INSERT INTO capture_sequence_active_generation(singleton,generation_id) VALUES(1,?) ON CONFLICT(singleton) DO UPDATE SET generation_id=excluded.generation_id',(gid,));c.commit()
    return audit|{'generation_id':gid,'atomic_publish_status':'published'}
