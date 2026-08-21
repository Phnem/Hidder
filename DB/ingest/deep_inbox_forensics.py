"""Exhaustive, static-only forensic inventory for protocol-miner/inbox.

Never executes vendor code.  Archive extraction is delegated to 7-Zip only;
every source and extracted file receives an explicit terminal analysis status.
"""
from __future__ import annotations

import hashlib, json, mimetypes, os, re, shutil, subprocess, time
from collections import Counter
from pathlib import Path

ARCHIVE_EXT={".zip",".7z",".rar",".cab",".msi",".msix",".appx",".asar",".tar",".gz",".bz2",".xz",".iso"}
TEXT_EXT={".txt",".md",".url",".json",".js",".html",".htm",".xml",".ini",".yaml",".yml",".toml",".csv",".inf",".log",".cfg",".config",".ps1",".bat",".cmd",".py",".c",".cc",".cpp",".h",".cs",".rs",".db"}
PROTOCOL_MARKERS=(b"navigator.hid",b"navigator.usb",b"sendreport",b"receivefeaturereport",b"sendfeaturereport",b"hidapi",b"node-hid",b"winusb",b"libusb",b"hidd_",b"hidp_",b"setupdi",b"writefile",b"readfile",b"deviceiocontrol",b"vendorid",b"productid",b"vid_",b"pid_",b"usb\\vid",b"hidraw",b"feature report")
SEVEN=Path(r"C:\Program Files\7-Zip\7z.exe")

def _sha256(path:Path)->str:
 h=hashlib.sha256()
 with path.open("rb") as f:
  for chunk in iter(lambda:f.read(4*1024*1024),b""):h.update(chunk)
 return h.hexdigest()

def _magic(path:Path)->str:
 try:
  with path.open("rb") as f: head=f.read(32)
 except OSError:return "unreadable"
 if head.startswith(b"MZ"): return "PE"
 if head.startswith(b"PK\x03\x04") or head.startswith(b"PK\x05\x06"): return "ZIP"
 if head.startswith(b"7z\xbc\xaf\x27\x1c"):return "7Z"
 if head.startswith(b"Rar!\x1a\x07"):return "RAR"
 if head.startswith(b"MSCF"):return "CAB"
 if head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):return "OLE/possibly_MSI"
 if head.startswith(b"\x1f\x8b"):return "GZIP"
 if head.startswith(b"\x7fELF"):return "ELF"
 if head.startswith(b"\0asm"):return "WASM"
 if head.startswith(b"{\"files\"") and path.suffix.lower()==".asar":return "ASAR_JSON"
 return "text_or_unknown"

def _strings_and_markers(path:Path, limit:int=64*1024*1024)->dict:
 size=path.stat().st_size
 # The full file is streamed for marker detection.  Printable strings are capped.
 markers=set(); strings=[]; carry=b""
 with path.open("rb") as f:
  seen=0
  while chunk:=f.read(4*1024*1024):
   low=(carry+chunk).lower()
   markers.update(m.decode("ascii") for m in PROTOCOL_MARKERS if m in low)
   if seen<limit:
    take=chunk[:limit-seen]; strings.extend(re.findall(rb"[\x20-\x7e]{6,}",take));seen+=len(take)
   carry=chunk[-128:]
 out=[x.decode("ascii","replace")[:500] for x in strings if any(t in x.lower() for t in (b"hid",b"usb",b"report",b"vid_",b"pid_",b"checksum"))]
 return {"markers":sorted(markers),"strings":out[:250],"strings_truncated":size>limit}

def _seven_list(path:Path)->tuple[str,str]:
 if not SEVEN.exists():return "UNSUPPORTED_WITH_REASON","7-Zip unavailable"
 p=subprocess.run([str(SEVEN),"l","-slt",str(path)],capture_output=True,text=True,errors="replace",timeout=300)
 if p.returncode:return "BLOCKED_WITH_REASON",(p.stderr or p.stdout)[-1500:]
 return "LISTED",p.stdout[-20000:]

def _extract(path:Path, dest:Path)->tuple[str,str]:
 dest.mkdir(parents=True,exist_ok=True)
 p=subprocess.run([str(SEVEN),"x","-y",f"-o{dest}",str(path)],capture_output=True,text=True,errors="replace",timeout=1800)
 if p.returncode:return "BLOCKED_WITH_REASON",(p.stderr or p.stdout)[-1500:]
 return "EXTRACTED",p.stdout[-2000:]

def run_inbox_forensics(inbox:Path, report:Path, extracted_root:Path, max_depth:int=8)->dict:
 started=time.time(); queue=[(inbox,0,None,None)]; files=[];directories=[]; hashes={};seen_paths=set()
 while queue:
  root,depth,parent_sha,container_path=queue.pop(0)
  key=str(root.resolve()).casefold()
  if key in seen_paths or depth>max_depth:continue
  seen_paths.add(key)
  try: children=sorted(root.iterdir(),key=lambda p:p.name.casefold())
  except OSError as exc:
   directories.append({"path":str(root),"status":"BLOCKED_WITH_REASON","reason":str(exc)});continue
  directories.append({"path":str(root.relative_to(inbox)) if root==inbox or root.is_relative_to(inbox) else str(root),"status":"EMPTY" if not children else "NON_EMPTY","depth":depth})
  for p in children:
   if p.is_dir():queue.append((p,depth,parent_sha,container_path));continue
   item={"path":str(p.relative_to(inbox)) if p.is_relative_to(inbox) else str(p),"size":p.stat().st_size,"extension":p.suffix.lower() or "[none]","mime":mimetypes.guess_type(p.name)[0] or "application/octet-stream","magic":_magic(p),"parent_sha256":parent_sha,"path_inside_container":container_path}
   try:item["sha256"]=_sha256(p)
   except OSError as exc:item.update(status="BLOCKED_WITH_REASON",reason=str(exc));files.append(item);continue
   if item["sha256"] in hashes:
    item.update(status="DUPLICATE",duplicate_of=hashes[item["sha256"]]);files.append(item);continue
   hashes[item["sha256"]]=item["path"]
   analysis=_strings_and_markers(p)
   item["protocol_markers"]=analysis["markers"];item["relevant_strings"]=analysis["strings"];item["strings_truncated"]=analysis["strings_truncated"]
   archive=item["extension"] in ARCHIVE_EXT or item["magic"] in {"ZIP","7Z","RAR","CAB","OLE/possibly_MSI","GZIP"}
   if archive:
    listing_status,detail=_seven_list(p);item["container_listing_status"]=listing_status;item["container_listing"]=detail
    if listing_status=="LISTED":
     dest=extracted_root/item["sha256"]
     extract_status,extract_detail=_extract(p,dest);item["extraction_status"]=extract_status;item["extraction_detail"]=extract_detail
     if extract_status=="EXTRACTED":queue.append((dest,depth+1,item["sha256"],item["path"]))
     item["status"]="PROTOCOL_DATA" if analysis["markers"] else ("ANALYZED_NO_PROTOCOL" if extract_status=="EXTRACTED" else extract_status)
    else:item["status"]=listing_status
   else:
    item["status"]="PROTOCOL_DATA" if analysis["markers"] else "ANALYZED_NO_PROTOCOL"
   files.append(item)
   if len(files)%10==0:print(f"forensics files={len(files)} queue={len(queue)}",flush=True)
 statuses=Counter(x["status"] for x in files)
 result={"started_at":started,"finished_at":time.time(),"inbox":str(inbox),"directories_total":len(directories),"empty_directories":sum(x["status"]=="EMPTY" for x in directories),"nonempty_directories":sum(x["status"]=="NON_EMPTY" for x in directories),"files_total":len(files),"bytes_total":sum(x["size"] for x in files),"status_counts":dict(statuses),"unexplained_skipped":sum(1 for x in files if not x.get("status")),"directories":directories,"files":files}
 tmp=report.with_suffix(".staging.json");tmp.parent.mkdir(parents=True,exist_ok=True);tmp.write_text(json.dumps(result,indent=2),encoding="utf8");tmp.replace(report)
 return result
