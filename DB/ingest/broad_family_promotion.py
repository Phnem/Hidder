"""Evidence-only family closability inventory; no semantic/capture mining."""
from __future__ import annotations
import json,sqlite3
from pathlib import Path
EXTERNAL=(1874,1878,1879,1880,1881,1882,1883,1884)
def build_broad_family_inventory(db_path:Path, report_path:Path)->dict:
 with sqlite3.connect(db_path) as c:
  c.row_factory=sqlite3.Row
  c.execute("create table if not exists operation_closure_queue(operation_id integer primary key,status text not null,reason text not null)")
  c.executemany("insert into operation_closure_queue values(?,?,?) on conflict(operation_id) do update set status=excluded.status,reason=excluded.reason",((x,'NEEDS_EXTERNAL_EVIDENCE','generic closure exhausted; requires independent static/docs/guided capture') for x in EXTERNAL))
  families=[]
  for f in c.execute('select id,family_key,display_name from protocol_families'):
   ops=c.execute("select count(*) from protocol_operations where protocol_family_id=? and operation_status!='rejected'",(f['id'],)).fetchone()[0]
   maps=c.execute('select count(distinct product_id) from device_protocol_mappings where protocol_family_id=?',(f['id'],)).fetchone()[0]
   src=[r[0] for r in c.execute("select distinct sr.root_name from operation_evidence oe join source_files sf on sf.id=oe.source_file_id join source_roots sr on sr.id=sf.source_root_id join protocol_operations po on po.id=oe.operation_id where po.protocol_family_id=?",(f['id'],))]
   text=' '.join(str(x or '') for x in c.execute("select request_encoding_json,response_encoding_json,checksum_json,initialization_json,sequencing_json,timeout_ms,delay_ms from protocol_operations where protocol_family_id=?",(f['id'],)).fetchall())
   features={'independent_sources':len(src),'packet_builders':ops>0,'response_parsers':'response' in text.lower(),'checksum':'checksum' in text.lower() or 'crc' in text.lower(),'initialization':'init' in text.lower() or 'handshake' in text.lower(),'sequencing':'sequenc' in text.lower(),'timing':'timeout' in text.lower() or 'delay' in text.lower()}
   score=maps*2+len(src)*3+sum(bool(v) for k,v in features.items() if k!='independent_sources')
   families.append({'family':f['family_key'],'products':maps,'operations':ops,'independent_sources':src,'features':features,'closability_score':score,'next_action':'semantic promotion' if len(src)>=2 and maps else ('obtain verified capture' if maps else 'explicit device mapping')})
  families.sort(key=lambda x:(x['closability_score'],x['operations']),reverse=True)
  out={'protocol_families_total':len(families),'families_with_explicit_static_mappings':sum(x['products']>0 for x in families),'families_with_2_independent_sources':sum(len(x['independent_sources'])>=2 for x in families),'old_operations_needs_external_evidence':len(EXTERNAL),'top_25':families[:25],'new_near_complete':0,'new_implementation_ready':0}
 report_path.parent.mkdir(parents=True,exist_ok=True);report_path.write_text(json.dumps(out,indent=2),encoding='utf8');return out
