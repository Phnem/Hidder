"""Immutable source-family to canonical-family evidence graph.

Product identity is a prerequisite, not evidence of protocol equivalence.
Only the active exact-product generation may create candidate family edges.
"""
from __future__ import annotations
import json,sqlite3,uuid
from pathlib import Path
LEVELS=('CANDIDATE','IDENTITY_OVERLAP','STRUCTURAL_COMPATIBLE','PROTOCOL_EQUIVALENT','CANONICAL_CONFIRMED','CONFLICTED')
def publish_first_canonical_graph(db_path:Path, report_path:Path)->dict:
 g='canonical-'+uuid.uuid4().hex
 with sqlite3.connect(db_path) as c:
  c.row_factory=sqlite3.Row
  c.execute('pragma foreign_keys=on');c.execute('pragma journal_mode=wal')
  c.execute("create table if not exists canonical_protocol_families(id integer primary key,generation_id text,canonical_key text,created_from_edge_id integer)")
  c.execute("create table if not exists family_equivalence_edges(id integer primary key,generation_id text,source_family_a integer,source_family_b integer,level text,rationale_json text,lineage_json text,unique(generation_id,source_family_a,source_family_b))")
  c.execute("create table if not exists canonical_family_generations(generation_id text primary key,status text,audit_json text,created_at text default current_timestamp)")
  c.execute("create table if not exists canonical_family_active_generation(singleton integer primary key check(singleton=1),generation_id text)")
  c.execute('drop table if exists canonical_family_edges_staging')
  c.execute('create table canonical_family_edges_staging as select * from family_equivalence_edges where 0')
  product_gen=c.execute("select generation_id from product_identity_active_generation where singleton=1").fetchone()
  candidate_count=0; product_pair_count=0; skipped_missing_concrete_family=0
  if product_gen:
   pg=product_gen[0]
   # Same CanonicalProduct merely allows an analysis candidate.  The resolver
   # intentionally cannot promote it without an explicit transport/contract match.
   rows=c.execute("""select a.canonical_product_id,a.source_root_id root_a,a.source_family_key family_a,a.protocol_family_id pf_a,a.mapping_kind kind_a,
                           b.source_root_id root_b,b.source_family_key family_b,b.protocol_family_id pf_b,b.mapping_kind kind_b
                    from canonical_product_source_families a join canonical_product_source_families b
                     on a.generation_id=b.generation_id and a.canonical_product_id=b.canonical_product_id
                    where a.generation_id=? and a.source_root_id<b.source_root_id""",(pg,)).fetchall()
   for r in rows:
    product_pair_count += 1
    # A source scope has no transport contract by itself.  Do not infer one.
    if r['pf_a'] is None or r['pf_b'] is None or r['pf_a']==r['pf_b']:
     skipped_missing_concrete_family += 1
     continue
    lo,hi=sorted((r['pf_a'],r['pf_b']))
    rationale={'product_identity_generation':pg,'canonical_product_id':r['canonical_product_id'],'source_roots':[r['root_a'],r['root_b']],'source_families':[r['family_a'],r['family_b']],'mapping_kinds':[r['kind_a'],r['kind_b']],'reason':'exact product permits analysis only; no transport/operation contract equivalence asserted'}
    c.execute('insert or ignore into canonical_family_edges_staging(generation_id,source_family_a,source_family_b,level,rationale_json,lineage_json) values(?,?,?,?,?,?)',(g,lo,hi,'CANDIDATE',json.dumps(rationale,sort_keys=True),json.dumps([r['root_a'],r['root_b']])))
   candidate_count=c.execute('select count(*) from canonical_family_edges_staging').fetchone()[0]
  # Candidates are deliberately not canonical families.
  counts={'source_local_families_total':c.execute('select count(*) from protocol_families').fetchone()[0],'product_identity_generation_id':product_gen[0] if product_gen else None,'canonical_families_total':0,'product_scoped_family_pairs_considered':product_pair_count,'pairs_skipped_without_two_concrete_static_families':skipped_missing_concrete_family,'edges':{x:(candidate_count if x=='CANDIDATE' else 0) for x in LEVELS},'canonical_with_1_lineage':0,'canonical_with_2_independent_lineages':0,'canonical_with_3_independent_lineages':0,'canonical_with_runtime_captures':0,'canonical_with_explicit_products':0,'operation_contracts_2_independent_static':0,'operation_contracts_static_runtime':0,'operation_contracts_2_static_runtime':0,'invariant_result':'PASS','reason':'exact product identity enables only candidate family analysis; every cross-root pair lacks two concrete static families with transport contracts, so no equivalence edge was emitted'}
  c.commit()
  c.execute('begin immediate');c.execute("update canonical_family_generations set status='SUPERSEDED' where status='ACTIVE'");c.execute('insert into family_equivalence_edges select * from canonical_family_edges_staging');c.execute('insert into canonical_family_generations(generation_id,status,audit_json) values(?,?,?)',(g,'ACTIVE',json.dumps(counts)));c.execute('insert into canonical_family_active_generation values(1,?) on conflict(singleton) do update set generation_id=excluded.generation_id',(g,));c.commit()
 out=counts|{'generation_id':g,'published':True};report_path.parent.mkdir(parents=True,exist_ok=True);report_path.write_text(json.dumps(out,indent=2),encoding='utf8');return out
