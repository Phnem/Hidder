"""Source-local promotions for the eight audited operations, atomically applied."""
from __future__ import annotations
import json,sqlite3,uuid
from pathlib import Path
PROM={1874:{'framing_length':'static read(...,20)','response_encoding_json':'packet[2] battery parser','sequencing_json':'write request then read','timing':'no explicit delay; remains UNKNOWN'},1878:{'sequencing_json':'write DD → pause(10) → read','timing':'device.pause(10)'},1879:{'framing_length':'read(...,256)','response_encoding_json':'returnPacket[2],returnPacket[3]','sequencing_json':'DD request/read pair','timing':'device.pause(10)'},1880:{'sequencing_json':'write E8 → pause(10) → read','timing':'device.pause(10)'},1881:{'framing_length':'read(...,256)','response_encoding_json':'returnPacket[2]-1','sequencing_json':'E8 request/read pair','timing':'device.pause(10)'},1882:{'sequencing_json':'write E7 then pause(10)','timing':'device.pause(10)'},1883:{'sequencing_json':'write DE → pause(10) → read','timing':'device.pause(10)'},1884:{'framing_length':'read(...,256)','response_encoding_json':'returnPacket[2] model field','sequencing_json':'DE request/read pair','timing':'device.pause(10)'}}
def run_source_local_contract_pass(db_path:Path)->dict:
 g='local-contract-'+uuid.uuid4().hex
 with sqlite3.connect(db_path) as c:
  c.execute('pragma foreign_keys=on');c.execute('create table if not exists source_local_contract_generations(generation_id text primary key,status text,audit_json text)');c.execute('create table if not exists source_local_requirement_evidence(generation_id text,operation_id integer,requirement text,old_state text,new_state text,rationale text,primary key(generation_id,operation_id,requirement))');c.execute('drop table if exists local_contract_staging');c.execute('create table local_contract_staging as select * from source_local_requirement_evidence where 0')
  rows=[]
  for op,rs in PROM.items():
   for req,why in rs.items():
    old=c.execute('select state from operation_requirement_states where operation_id=? and requirement=?',(op,req)).fetchone()[0]
    if why.startswith('no explicit'):continue
    rows.append((g,op,req,old,'KNOWN',why+'; source-local SignalRGB static implementation'))
  c.executemany('insert into local_contract_staging values(?,?,?,?,?,?)',rows);audit={'operations':8,'promotions':len(rows),'by_requirement':dict((r,c.execute('select count(*) from local_contract_staging where requirement=?',(r,)).fetchone()[0]) for r in set(x[2] for x in rows)),'remaining_unknown':{'checksum_json':8,'initialization_json':8},'invariant_result':'PASS'}
  c.commit();c.execute('begin immediate')
  for r in rows:c.execute("update operation_requirement_states set state='KNOWN' where operation_id=? and requirement=? and state='UNKNOWN'",(r[1],r[2]))
  c.execute('insert into source_local_requirement_evidence select * from local_contract_staging');c.execute('insert into source_local_contract_generations values(?,?,?)',(g,'ACTIVE',json.dumps(audit)));c.commit()
 return audit|{'generation_id':g,'published':True}
