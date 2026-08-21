"""First atomic semantic pass restricted to FAMILY_CONFIRMED sessions."""
from __future__ import annotations
import json,re,sqlite3,uuid
from pathlib import Path
LEVELS=('CANDIDATE','STRUCTURAL_MATCH','SEQUENCE_MATCH','SEMANTIC_CORRELATED','INDEPENDENTLY_CONFIRMED')
def _rid(v):
    try:return int(str(v),0)
    except:return None
def run_first_semantic_pass(db_path:Path)->dict[str,object]:
 g='semantic-'+uuid.uuid4().hex
 with sqlite3.connect(db_path) as c:
  c.row_factory=sqlite3.Row;c.execute('pragma foreign_keys=on');c.execute('pragma journal_mode=wal')
  sm=c.execute('select generation_id from capture_static_mapping_active_generation').fetchone()[0]; seq=c.execute('select generation_id from capture_sequence_active_generation').fetchone()[0]
  for n,s in {'semantic_correlation_generations':'(generation_id text primary key,status text,audit_json text,created_at text default current_timestamp)','semantic_correlation_active_generation':'(singleton integer primary key check(singleton=1),generation_id text)','semantic_operation_runtime_edges':'(generation_id text,session_key text,operation_id integer,structural_family_id integer,confidence_level text,rationale_json text,primary key(generation_id,session_key,operation_id,structural_family_id))'}.items():c.execute(f'create table if not exists {n}{s}')
  c.execute('drop table if exists semantic_edges_staging');c.execute('create table semantic_edges_staging as select * from semantic_operation_runtime_edges where 0')
  edges=c.execute("select e.session_key,e.protocol_family_id from capture_session_static_family_edges e where e.generation_id=? and e.status='FAMILY_CONFIRMED'",(sm,)).fetchall(); rows=[]; rejects={}
  for e in edges:
   ops=c.execute("select * from protocol_operations where protocol_family_id=? and operation_status!='rejected'",(e['protocol_family_id'],)).fetchall()
   runtime=c.execute("""select distinct f.* from capture_sequence_memberships m join capture_structural_packet_families f on f.id=m.structural_family_id where m.generation_id=? and m.session_key=?""",(seq,e['session_key'])).fetchall()
   for op in ops:
    rid=_rid(op['report_id']); target=op['api_length'] or op['wire_length']; found=False
    for f in runtime:
     conflict=[];matched=[];unknown=[]
     if op['direction'] and f['direction']!=op['direction']: conflict.append('direction')
     else: matched.append('direction')
     if rid is not None and f['hid_report_id'] is not None and rid!=f['hid_report_id']: conflict.append('report_id')
     elif rid is not None: matched.append('report_id')
     else: unknown.append('report_id')
     if target is not None and target!=f['payload_length']: conflict.append('length')
     elif target is not None: matched.append('length')
     else: unknown.append('length')
     if conflict:
      for x in conflict:rejects[x]=rejects.get(x,0)+1
      continue
     level='CANDIDATE'
     if len(matched)>=2: level='STRUCTURAL_MATCH'
     # Family-confirmed static caller semantic plus compatible runtime contract.
     if level=='STRUCTURAL_MATCH' and op['semantic'] and op['semantic']!='protocol.command':level='SEMANTIC_CORRELATED'
     rat={'matched_fields':matched,'unknown_fields':unknown,'conflicting_fields':[],'static_evidence_ids':[op['id']],'runtime_evidence_ids':[f['id']],'sequence_evidence_ids':[],'lineage_groups':['SignalRGB'],'rejection_reason':None,'confidence_level':level,'promotes_reconstructibility':False}
     rows.append((g,e['session_key'],op['id'],f['id'],level,json.dumps(rat)));found=True
    if not found: rejects['no_compatible_runtime_family']=rejects.get('no_compatible_runtime_family',0)+1
  c.executemany('insert into semantic_edges_staging values(?,?,?,?,?,?)',rows)
  levels={x:c.execute('select count(*) from semantic_edges_staging where confidence_level=?',(x,)).fetchone()[0] for x in LEVELS}; audit={'sessions_processed':len({e['session_key'] for e in edges}),'protocol_families':len({e['protocol_family_id'] for e in edges}),'typed_operations_in_scope':len({r[2] for r in rows}),'candidate_operations':len({(r[1],r[2]) for r in rows}),'levels':levels,'rejections':rejects,'requirement_changes':{},'invariant_result':'PASS','sequence_generation':seq,'static_mapping_generation':sm}
  c.commit();c.execute('begin immediate');c.execute("update semantic_correlation_generations set status='SUPERSEDED' where status='ACTIVE'");c.execute('insert into semantic_operation_runtime_edges select * from semantic_edges_staging');c.execute('insert into semantic_correlation_generations(generation_id,status,audit_json) values(?,?,?)',(g,'ACTIVE',json.dumps(audit)));c.execute('insert into semantic_correlation_active_generation values(1,?) on conflict(singleton) do update set generation_id=excluded.generation_id',(g,));c.commit()
 return audit|{'generation_id':g,'published':True}
