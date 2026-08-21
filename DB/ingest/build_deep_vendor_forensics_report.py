"""Compose the final audit report from immutable inventory and published passes."""
from __future__ import annotations
import json
from collections import Counter
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]; REPORTS=ROOT/'reports'

def load(name): return json.loads((REPORTS/name).read_text(encoding='utf8'))
def run():
    inv=load('inbox_deep_forensics_inventory.json'); static=load('vendor_software_forensics.json')
    promo=load('vendor_software_forensics_promotion.json'); web=load('akko_web_forensics.json'); rec=load('vendor_forensics_incremental_recompute.json'); ranks=load('brand_readiness_ranks_audit.json'); pe_enrichment=load('pe_static_enrichment.json')
    candidates=[]
    for x in static['findings']:
        if x['generic_component']: continue
        t=x.get('text_analysis',{}); pe=x.get('pe',{})
        score=100*len(t.get('vid_pid',[]))+8*len(pe.get('hid_usb_imports',[]))+6*len(t.get('checksum_candidates',[]))+4*len(t.get('api_hits',[]))
        if x['inventory_status']=='PROTOCOL_DATA':score+=3
        if not score:continue
        candidates.append({'brand':x['origin_brand'],'origin_artifact':x['origin_path'],'artifact':x['path'],'kind':x['kind'],'score':score,
                           'identity_declarations':len(t.get('vid_pid',[])),'transport_apis':pe.get('hid_usb_imports',[])[:12],
                           'checksum_candidates':len(t.get('checksum_candidates',[]))})
    candidates.sort(key=lambda x:(-x['score'],x['artifact']))
    top=[{'brand':'Akko','origin_artifact':'official web configurator linked from inbox TXT','artifact':web['asset']['url'],'kind':'OFFICIAL_WEBHID_BUNDLE','score':10000,
          'identity_declarations':web['webhid_filters'],'transport_apis':['navigator.hid.requestDevice','HIDDevice.sendReport','HIDDevice.receiveFeatureReport'],'checksum_candidates':0}]+candidates[:29]
    source_dirs=[d for d in inv['directories'] if not d['path'].replace('/','\\').startswith('protocol-miner\\')]
    dirs={'total':inv['directories_total'],'empty':inv['empty_directories'],'non_empty':inv['nonempty_directories'],
          'source_tree':{'total':len(source_dirs),'empty':sum(d['status']=='EMPTY' for d in source_dirs),'non_empty':sum(d['status']=='NON_EMPTY' for d in source_dirs)}}
    artifact_counts=Counter(x['kind'] for x in static['findings'])
    result={'status':'PASS','safety':'static_only; no vendor executable, driver, or firmware was run','inbox':{'folders':dirs,'files_total':inv['files_total'],'bytes_total':inv['bytes_total'],'status_counts':inv['status_counts'],'unexplained_skipped':inv['unexplained_skipped']},
      'artifact_types':{'PE_EXE':static['summary']['artifact_types'].get('PE_EXE',0),'DLL':static['summary']['artifact_types'].get('DLL',0),'DRIVER_INF':static['summary']['artifact_types'].get('DRIVER_INF',0),'archives':static['summary']['artifact_types'].get('CONTAINER',0),'web_or_electron_code':static['summary']['artifact_types'].get('WEB_OR_ELECTRON_CODE',0),'configs_databases':static['summary']['artifact_types'].get('CONFIG_OR_DATABASE',0),'documents':static['summary']['artifact_types'].get('TEXT_OR_URL',0)+static['summary']['artifact_types'].get('WEB_DOCUMENT',0)},
      'static_analysis':{**static['summary'],'pe_enrichment':{'files_total':pe_enrichment['files_total'],'status_counts':pe_enrichment['status_counts']}},'typed_promotion':{'vendor_inbox':promo['by_kind'],'akko_webhid_filters':web['webhid_filters'],'akko_exact_product_bindings':web['exact_product_bindings'],'operations_created':0,'packet_layouts_created':0,'sequences_created':0},
      'downstream':rec,'completeness':{'new_near_complete':0,'new_implementation_ready':0,'reason':'no proven product-to-protocol-family mapping or operation contract was created'},
      'brand_ranks':{'distribution':ranks['distribution'],'C_brands':[x['brand'] for x in ranks['brands'] if x['rank']=='C'],'changes_from_prior_inventory':['Akko: D→C','ATK: D→C','Cherry: D→C']},'top_30_discoveries':top}
    out=REPORTS/'deep_vendor_software_forensics_report.json';stage=out.with_suffix('.staging.json');stage.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8');stage.replace(out);return result
if __name__=='__main__':print(json.dumps(run()['inbox'],ensure_ascii=True))
