"""Static provenance-preserving audit of the official Akko Cloud Driver WebHID bundle."""
from __future__ import annotations
import hashlib, json, re, sqlite3, time, urllib.request
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/'data/registry.sqlite'; OUT=ROOT/'reports/akko_web_forensics.json'
PAGE='https://web.akkogear.com/'; ASSET='https://web.akkogear.com/js/index.4289e208.js'
LINEAGE='akko-official-web-static'; ROOT_NAME='akko-official-web-driver'
FILTER=re.compile(r'\{type:"(?P<type>[^"]+)",vendorId:(?P<vid>\d+),productId:(?P<pid>\d+),usage:(?P<usage>\d+),usagePage:(?P<page>\d+),interfaceNumber:(?P<iface>\d+)\}')

def fetch(url:str)->bytes:
    req=urllib.request.Request(url,headers={'User-Agent':'PeripheralProtocolForensics/1.0'})
    with urllib.request.urlopen(req,timeout=45) as response:return response.read()

def put_fact(c,typ,scope,semantic,key,value,file_id,method,confidence,sha):
    val=json.dumps(value,sort_keys=True,separators=(',',':')); h=hashlib.sha256(val.encode()).hexdigest()
    c.execute('INSERT OR IGNORE INTO typed_facts(fact_type,scope_type,scope_key,semantic_type,canonical_key,canonical_value_json,value_hash,confidence) VALUES(?,?,?,?,?,?,?,?)',(typ,'vendor_web',scope,semantic,key,val,h,confidence))
    fid=c.execute('SELECT id FROM typed_facts WHERE fact_type=? AND scope_type=? AND scope_key=? AND semantic_type=? AND canonical_key=? AND value_hash=?',(typ,'vendor_web',scope,semantic,key,h)).fetchone()[0]
    c.execute("""INSERT OR IGNORE INTO typed_fact_evidence(typed_fact_id,source_file_id,line_start,line_end,symbol,extraction_method,trust_class,lineage_group,confidence,provenance_status,artifact_sha256,external_url)
                 VALUES(?,?,NULL,NULL,NULL,?,'OfficialVendorImplementation',?,?,'external_fetched',?,?)""",(fid,file_id,method,LINEAGE,confidence,sha,ASSET))
    return fid

def run():
    html=fetch(PAGE); js=fetch(ASSET); text=js.decode('utf8','replace'); sha=hashlib.sha256(js).hexdigest()
    filters=[{k:(int(v) if k!='type' else v) for k,v in x.groupdict().items()} for x in FILTER.finditer(text)]
    assert len(filters)==len({tuple(sorted(x.items())) for x in filters}) and filters
    assert all(token in text for token in ('navigator.hid.requestDevice','device.sendReport','device.receiveFeatureReport'))
    c=sqlite3.connect(DB)
    try:
        c.execute('BEGIN IMMEDIATE')
        root=c.execute('SELECT id FROM source_roots WHERE root_name=?',(ROOT_NAME,)).fetchone()
        rid=root[0] if root else c.execute("""INSERT INTO source_roots(root_name,local_path,repository_url,source_kind,audit_status,trust_class,lineage_group,files_total,files_relevant,files_processed,files_failed,bytes_scanned,collector_version)
            VALUES(?,?,?,'official_web_application','forensic_complete','OfficialVendorImplementation',?,1,1,1,0,?,?) RETURNING id""",(ROOT_NAME,PAGE,PAGE,LINEAGE,len(js),'akko-web-forensics/1')).fetchone()[0]
        row=c.execute('SELECT id FROM source_files WHERE source_root_id=? AND relative_path=? AND content_hash=?',(rid,ASSET,sha)).fetchone()
        fid=row[0] if row else c.execute("""INSERT INTO source_files(source_root_id,relative_path,content_hash,size,relevant,parsed,parser_name,parse_status,bytes_scanned,collector_version,facts_extracted,operations_extracted,layouts_extracted,sequences_extracted)
          VALUES(?,?,?,?,1,1,'AkkoWebHIDStatic','parsed_protocol_data',?, ?,0,0,0,0) RETURNING id""",(rid,ASSET,sha,len(js),len(js),'akko-web-forensics/1')).fetchone()[0]
        facts=[]; product_bindings=[]
        for f in filters:
            key=f"{f['vid']:04x}:{f['pid']:04x}:u{f['usage']:x}:p{f['page']:x}:i{f['iface']}"
            facts.append(put_fact(c,'DeviceIdentity','akko-webhid:'+sha,'device.webhid_filter',key,f,fid,'official_webhid_filter_static',.88,sha))
            matches=c.execute("""SELECT p.id,v.display_name FROM device_identifiers d JOIN products p ON p.id=d.product_id
                               JOIN vendors v ON v.id=p.vendor_id WHERE d.vid=? AND d.pid=? AND d.product_id IS NOT NULL""",(f['vid'],f['pid'])).fetchall()
            # Exact VID/PID is accepted only when it identifies one Registry
            # product and that product is an Akko product; cross-brand OEM IDs
            # and multi-product IDs remain explicitly unresolved.
            if len(matches)==1 and matches[0][1].casefold()=='akko':
                product_bindings.append(put_fact(c,'ProductBinding','akko-webhid:'+sha,'product.static_webhid_binding',
                    f"product:{matches[0][0]}",{'product_id':matches[0][0],**f,'basis':'official Akko WebHID exact filter'},fid,'official_webhid_exact_product_binding',.86,sha))
        facts.append(put_fact(c,'TransportContract','akko-webhid:'+sha,'transport.webhid','webhid_request_report_contract',
            {'transport':'WebHID','device_selection':'navigator.hid.requestDevice(filters)','write':'HIDDevice.sendReport(reportId,padded_payload)','response':'receiveFeatureReport(reportId); strip leading report ID when nonzero','fixed_length_padding':True},fid,'official_webhid_call_graph',.90,sha))
        c.commit()
    except Exception:
        c.rollback(); raise
    finally:c.close()
    out={'status':'PASS','fetched_at':time.time(),'page':{'url':PAGE,'sha256':hashlib.sha256(html).hexdigest(),'bytes':len(html)},'asset':{'url':ASSET,'sha256':sha,'bytes':len(js)},'webhid_filters':len(filters),'typed_facts':len(set(facts)),'exact_product_bindings':len(set(product_bindings)),'operations_created':0,'hardware_verified':False,'production_safe':False}
    s=OUT.with_suffix('.staging.json');s.write_text(json.dumps(out,indent=2),encoding='utf8');s.replace(OUT);return out
if __name__=='__main__':print(json.dumps(run(),ensure_ascii=True))
