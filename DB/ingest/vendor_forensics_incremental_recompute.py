"""Audit whether vendor-forensics facts have a valid incremental downstream scope."""
from __future__ import annotations
import json, sqlite3
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def run() -> dict:
    promo=json.loads((ROOT/'reports/vendor_software_forensics_promotion.json').read_text(encoding='utf8'))
    db=sqlite3.connect(ROOT/'data/registry.sqlite')
    rows=db.execute("""SELECT DISTINCT tf.canonical_value_json FROM typed_facts tf JOIN typed_fact_evidence e
                       ON e.typed_fact_id=tf.id WHERE e.lineage_group IN ('vendor-inbox-official-static','akko-official-web-static')
                       AND tf.fact_type='DeviceIdentity'""").fetchall()
    pairs={(json.loads(x[0])['vid'],json.loads(x[0])['pid']) for x in rows}
    overlaps=[]
    for vid,pid in sorted(pairs):
        products=[r[0] for r in db.execute('SELECT DISTINCT product_id FROM device_identifiers WHERE vid=? AND pid=? AND product_id IS NOT NULL',(vid,pid))]
        if products: overlaps.append({'vid':vid,'pid':pid,'product_ids':products})
    bindings=[json.loads(x[0]) for x in db.execute("""SELECT DISTINCT tf.canonical_value_json FROM typed_facts tf JOIN typed_fact_evidence e
          ON e.typed_fact_id=tf.id WHERE e.lineage_group='akko-official-web-static' AND tf.fact_type='ProductBinding'""")]
    result={'status':'PASS','promoted_device_identities':len(pairs),'exact_registry_product_overlaps':overlaps,
            'exact_product_bindings':bindings,'affected_products':len({x['product_id'] for x in bindings}),
            'incremental_actions': ('targeted ProductBinding retained; no source-family binding or protocol operation exists, so canonical-family/correlation intentionally not run'
                                    if bindings else 'none: no exact registry product edge; product binding/canonical family/correlation intentionally not run'),
            'semantic_correlation':'not_run: no proven protocol-family mapping created',
            'no_global_reingest':True}
    db.close()
    out=ROOT/'reports/vendor_forensics_incremental_recompute.json'; stage=out.with_suffix('.staging.json')
    stage.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8'); stage.replace(out)
    return result

if __name__=='__main__': print(json.dumps(run(),ensure_ascii=True))
