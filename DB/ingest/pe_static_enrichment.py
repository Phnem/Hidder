"""Resource/metadata enrichment for every unique PE in the inbox inventory.

No binary is executed; Capstone is used only to decode a bounded entry-point
window for architecture validation, never to infer a semantic operation.
"""
from __future__ import annotations
import json
from collections import Counter
from pathlib import Path
import pefile
from capstone import Cs, CS_ARCH_X86, CS_MODE_32, CS_MODE_64

ROOT=Path(__file__).resolve().parents[1]; INV=ROOT/'reports/inbox_deep_forensics_inventory.json'; OUT=ROOT/'reports/pe_static_enrichment.json'; INBOX=ROOT/'protocol-miner/inbox'
def path(x):
 p=Path(x['path']); return p if p.is_absolute() else (ROOT/p if x['path'].replace('/','\\').startswith('protocol-miner\\') else INBOX/p)
def version(pe):
 out={}
 for block in getattr(pe,'FileInfo',[]) or []:
  for entry in block:
   if getattr(entry,'Key',b'')==b'StringFileInfo':
    for table in entry.StringTable:
     out.update({k.decode('ascii','replace'):v.decode('utf8','replace') for k,v in table.entries.items()})
 return out
def run():
 inv=json.loads(INV.read_text(encoding='utf8')); seen=set(); rows=[]
 for x in inv['files']:
  if x['magic']!='PE' or x['sha256'] in seen:continue
  seen.add(x['sha256']); r={'path':x['path'],'sha256':x['sha256'],'size':x['size']}
  try:
   pe=pefile.PE(str(path(x)),fast_load=True); pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_RESOURCE']])
   r['machine']=hex(pe.FILE_HEADER.Machine); r['entry_point_rva']=hex(pe.OPTIONAL_HEADER.AddressOfEntryPoint);r['version']=version(pe)
   resource=getattr(pe,'DIRECTORY_ENTRY_RESOURCE',None)
   r['resource_root_types']=[str(e.name) if e.name else str(e.id) for e in (resource.entries if resource else [])]
   off=pe.get_offset_from_rva(pe.OPTIONAL_HEADER.AddressOfEntryPoint); code=pe.__data__[off:off+1024]
   mode=CS_MODE_64 if pe.FILE_HEADER.Machine==0x8664 else CS_MODE_32
   dis=list(Cs(CS_ARCH_X86,mode).disasm(code,pe.OPTIONAL_HEADER.ImageBase+pe.OPTIONAL_HEADER.AddressOfEntryPoint))
   r['entrypoint_decode']={'instructions':len(dis),'bytes_window':len(code),'status':'decoded_static_only'}
  except Exception as exc:r['status']='UNSUPPORTED_WITH_REASON';r['reason']=repr(exc)
  else:r['status']='ANALYZED_NO_PROTOCOL_OR_GENERIC_TRANSPORT'
  rows.append(r)
 result={'status':'PASS','files_total':len(rows),'status_counts':dict(Counter(x['status'] for x in rows)),'rows':rows,'safety':'static PE metadata/resources/entrypoint decoding only; no execution'}
 assert len(rows)==len(seen)
 stage=OUT.with_suffix('.staging.json');stage.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8');stage.replace(OUT);return result
if __name__=='__main__':print(json.dumps({k:v for k,v in run().items() if k!='rows'},ensure_ascii=True))
