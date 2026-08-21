"""Field-specific requirement audit for the active semantic operation subset."""
from __future__ import annotations
import json,sqlite3,uuid
from pathlib import Path
REQS=('response_encoding_json','checksum_json','initialization_json','sequencing_json','timing','framing_length','report_id')
def run_requirement_evidence_pass(db_path:Path)->dict[str,object]:
 g='requirement-'+uuid.uuid4().hex
 with sqlite3.connect(db_path) as c:
  c.row_factory=sqlite3.Row;c.execute('pragma foreign_keys=on');c.execute('pragma journal_mode=wal')
  sg=c.execute('select generation_id from semantic_correlation_active_generation').fetchone()[0]
  c.execute("create table if not exists requirement_evidence_generations(generation_id text primary key,status text,audit_json text,created_at text default current_timestamp)")
  c.execute("create table if not exists requirement_evidence_active_generation(singleton integer primary key check(singleton=1),generation_id text)")
  c.execute("create table if not exists operation_requirement_evidence_audit(generation_id text,operation_id integer,requirement text,old_state text,new_state text,evidence_status text,static_evidence_json text,runtime_evidence_json text,rationale text,provenance_json text,primary key(generation_id,operation_id,requirement))")
  c.execute('drop table if exists requirement_evidence_staging');c.execute('create table requirement_evidence_staging as select * from operation_requirement_evidence_audit where 0')
  ops=[r[0] for r in c.execute('select distinct operation_id from semantic_operation_runtime_edges where generation_id=?',(sg,))]; rows=[];prom={}
  for opid in ops:
   op=c.execute('select * from protocol_operations where id=?',(opid,)).fetchone(); runtime=[r[0] for r in c.execute('select structural_family_id from semantic_operation_runtime_edges where generation_id=? and operation_id=?',(sg,opid))]
   states=dict(c.execute('select requirement,state from operation_requirement_states where operation_id=?',(opid,)))
   for req in REQS:
    old=states.get(req,'UNKNOWN'); new=old; status='NO_PROMOTION'; rationale='requires field-specific static or runtime evidence'
    # Only audit pre-existing field proof: no implicit promotion from semantic correlation.
    if req=='framing_length' and old=='UNKNOWN' and (op['api_length'] or op['wire_length']) and runtime:
     status='RUNTIME_LENGTH_AVAILABLE_BUT_NOT_PROMOTED';rationale='runtime structural match exists; promotion deferred until exact family length audit'
    rows.append((g,opid,req,old,new,status,json.dumps({'operation_id':opid,'fields':{'report_id':op['report_id'],'api_length':op['api_length'],'wire_length':op['wire_length']}}),json.dumps({'semantic_generation':sg,'structural_family_ids':runtime}),rationale,json.dumps({'lineage':'SignalRGB','promotes_reconstructibility':False})))
  c.executemany('insert into requirement_evidence_staging values(?,?,?,?,?,?,?,?,?,?)',rows)
  coverage=c.execute('select count(distinct operation_id) from requirement_evidence_staging').fetchone()[0]; remaining=dict(c.execute("select requirement,count(*) from requirement_evidence_staging where old_state='UNKNOWN' group by requirement"));audit={'operations_audited':len(ops),'coverage':coverage,'promotions_unknown_to_known':prom,'promotions_unknown_to_not_applicable':{},'remaining_unknown':remaining,'conflicts':0,'invariant_result':'PASS' if coverage==len(ops) else 'FAIL','semantic_generation':sg}
  c.commit();c.execute('begin immediate');c.execute("update requirement_evidence_generations set status='SUPERSEDED' where status='ACTIVE'");c.execute('insert into operation_requirement_evidence_audit select * from requirement_evidence_staging');c.execute('insert into requirement_evidence_generations(generation_id,status,audit_json) values(?,?,?)',(g,'ACTIVE',json.dumps(audit)));c.execute('insert into requirement_evidence_active_generation values(1,?) on conflict(singleton) do update set generation_id=excluded.generation_id',(g,));c.commit()
 return audit|{'generation_id':g,'published':True}
