"""Read-only, conservative brand readiness ranking."""
from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from pathlib import Path


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.casefold())


# This is an intentionally conservative hardware-brand allowlist, not a fuzzy
# classifier over catalog labels.  It includes consumer peripheral makers and
# established keyboard/mouse brands represented in this corpus.  Project names,
# QMK authors, chip vendors, URLs, detector names, and placeholders are absent.
BRAND_ALIASES = {
    "A4Tech":["A4Tech"],"AULA":["AULA"],"Akko":["Akko"],"Alienware":["Alienware"],"Anne Pro":["Anne"],"Apple":["Apple"],
    "ASUS":["ASUS","ASUS ROG","Asustek"],"Attack Shark":["Attack Shark"],"ATK":["ATK"],"Bastard Keyboards":["Bastard Keyboards"],
    "Binepad":["Binepad"],"Bloody":["Bloody"],"Boardsource":["Boardsource"],"CannonKeys":["CannonKeys"],"Cherry":["CHERRY"],
    "Chilkey":["Chilkey"],"Chosfox":["Chosfox"],"Cidoo":["Cidoo"],"Clueboard":["Clueboard"],"Cooler Master":["Cooler Master"],
    "Corsair":["Corsair"],"Cypres?":[],"Dareu":["Dareu"],"Dark Project":["Dark Project"],"DOIO":["DOIO"],"Drop":["Drop"],
    "DrunkDeer":["DrunkDeer"],"DZTECH":["DZTECH"],"Elecom":["Elecom"],"Elgato":["Elgato"],"Endgame Gear":["Endgame Gear"],
    "EPOMAKER":["EPOMAKER"],"Ergodox EZ":["Ergodox EZ"],"EVGA":["EVGA"],"Fantech":["Fantech"],"Feker":["Feker"],
    "Finalmouse":["Finalmouse"],"FL·ESPORTS":["FL·ESPORTS"],"Fnatic":["Fnatic Gear"],"G-Wolves":["G-Wolves"],
    "Gamakay":["Gamakay"],"Gigabyte":["Gigabyte"],"Glorious":["Glorious"],"GMMK":["GMMK"],"HyperX":["HyperX"],
    "IDOBAO":["IDOBAO"],"Input Club":["Input:Club"],"IQUNIX":["IQUNIX"],"IO by Red Square":["IO by Red Square"],
    "KBDfans":["KBDfans"],"Keebio":["Keebio"],"Kemove":["Kemove"],"Keychron":["Keychron"],"Keycult":["Keycult"],
    "Kingly-Keys":["Kingly-Keys"],"KPrepublic":["KPrepublic"],"Kysona":["Kysona"],"Lamzu":["Lamzu"],"Lemokey":["Lemokey"],
    "Lenovo":["Lenovo"],"Leobog":["Leobog"],"Leopold":["Leopold"],"Lian Li":["Lian Li"],"Logitech":["Logitech","Logitech G"],
    "Machenike":["Machenike"],"MagicForce":["MagicForce"],"Matrix Lab":["Matrix Lab"],"MCHOSE":["MCHOSE"],"Mechboards":["Mechboards"],
    "MechKeys":["MechKeys"],"Mechlovin":["Mechlovin","Mechlovin Studio","Team.Mechlovin"],"MechWild":["MechWild"],"MelGeek":["MelGeek"],
    "Meletrix":["Meletrix"],"Microsoft":["Microsoft"],"Mode Designs":["Mode","Mode Designs"],"MonsGeek":["MonsGeek"],
    "Mountain":["Mountain"],"Ninjutso":["Ninjutso"],"Nintendo":["Nintendo"],"NuPhy":["NuPhy"],"NZXT":["NZXT"],
    "Ploopy":["Ploopy"],"Pulsar":["Pulsar Gaming Gears"],"Qwertykeys":["Qwertykeys"],"RAMA Works":["RAMA WORKS"],
    "Razer":["Razer"],"Redragon":["redragon"],"Royal Kludge":["Royal Kludge"],"Scyrox":["Scyrox"],"Skyloong":["Skyloong"],
    "Sony":["Sony"],"SteelSeries":["SteelSeries"],"System76":["System76"],"The Key Company":["The Key Company"],
    "Thermaltake":["Thermaltake"],"Turtle Beach":["Turtle Beach"],"Unicomp":["Unicomp"],"Valve":["Valve"],"Varmilo":["Varmilo"],
    "VXE":["VXE"],"WLMOUSE":["WLMOUSE"],"Womier":["Womier"],"Wooting":["Wooting"],"Wuque Studio":["Wuque Studio"],
    "X-Bows":["X-Bows"],"YMDK":["YMDK"],"Yunzii":["Yunzii"],"ZOWIE":["ZOWIE"],
}
BRAND_ALIASES.pop("Cypres?", None)


def build_brand_readiness_ranking(db_path: Path, txt_path: Path, audit_path: Path) -> dict:
    # This optional, local report is produced only after the exhaustive vendor
    # inbox forensic pass has atomically promoted exact-file facts.  It permits
    # C only for a concrete guided workflow: product-specific VID/PID evidence
    # plus a vendor host HID/WinUSB dependency.  An installer name alone never
    # changes a rank.
    forensic_path = Path(__file__).resolve().parent.parent / "reports" / "vendor_software_forensics_promotion.json"
    guided: dict[str, dict[str, int]] = {}
    if forensic_path.exists():
        forensic = json.loads(forensic_path.read_text(encoding="utf8"))
        for promotion in forensic.get("promotions", []):
            entry = guided.setdefault(_norm(promotion["origin_brand"]), {"identity": 0, "transport": 0})
            entry[promotion["kind"]] = entry.get(promotion["kind"], 0) + 1
    akko_web_path = Path(__file__).resolve().parent.parent / "reports" / "akko_web_forensics.json"
    if akko_web_path.exists():
        akko = json.loads(akko_web_path.read_text(encoding="utf8"))
        if akko.get("status") == "PASS" and akko.get("webhid_filters", 0) and akko.get("typed_facts", 0):
            guided[_norm("Akko")] = {"identity": int(akko["webhid_filters"]), "transport": 1}
    with sqlite3.connect(db_path) as c:
        c.row_factory=sqlite3.Row
        binding=c.execute("SELECT generation_id FROM source_product_binding_active_generation WHERE singleton=1").fetchone()
        capture=c.execute("SELECT generation_id FROM capture_identity_active_generation WHERE singleton=1").fetchone()
        binding_id=binding[0] if binding else ""
        capture_id=capture[0] if capture else ""
        rows=c.execute("""WITH identities AS (SELECT DISTINCT product_id FROM device_identifiers),
          mapped AS (SELECT DISTINCT product_id FROM device_protocol_mappings),
          operations AS (SELECT DISTINCT d.product_id FROM device_protocol_mappings d JOIN protocol_operations o ON o.protocol_family_id=d.protocol_family_id),
          complete AS (SELECT DISTINCT i.registry_product_id product_id FROM source_product_binding_inputs i
            JOIN source_product_bindings b ON b.generation_id=i.generation_id AND b.input_key=i.input_key
            WHERE i.generation_id=? AND i.registry_product_id IS NOT NULL AND b.binding_status='COMPLETE_STATIC_BINDING'),
          captures AS (SELECT DISTINCT product_id FROM capture_session_product_edges WHERE generation_id=?),
          partial AS (SELECT product_id FROM device_reconstructibility WHERE classification='PARTIAL_PROTOCOL')
          SELECT v.id vendor_id,v.display_name,COUNT(p.id) products,
            COUNT(DISTINCT identities.product_id) products_with_identity,
            COUNT(DISTINCT mapped.product_id) mapped_products,
            COUNT(DISTINCT complete.product_id) complete_static_binding_products,
            COUNT(DISTINCT operations.product_id) products_with_operations,
            COUNT(DISTINCT captures.product_id) products_with_captures,
            COUNT(DISTINCT partial.product_id) partial_protocol_products,
            COUNT(DISTINCT pa.product_id) products_with_artifacts
          FROM vendors v LEFT JOIN products p ON p.vendor_id=v.id
          LEFT JOIN identities ON identities.product_id=p.id LEFT JOIN mapped ON mapped.product_id=p.id
          LEFT JOIN complete ON complete.product_id=p.id LEFT JOIN operations ON operations.product_id=p.id
          LEFT JOIN captures ON captures.product_id=p.id LEFT JOIN partial ON partial.product_id=p.id
          LEFT JOIN product_artifacts pa ON pa.product_id=p.id GROUP BY v.id""",(binding_id,capture_id)).fetchall()
    by_alias={_norm(alias):brand for brand,aliases in BRAND_ALIASES.items() for alias in aliases}
    groups: dict[str,dict] = {}
    excluded=[]
    for r in rows:
        brand=by_alias.get(_norm(r["display_name"]))
        if not brand:
            excluded.append(r["display_name"])
            continue
        g=groups.setdefault(brand,{"brand":brand,"aliases":[],"products":0,"products_with_identity":0,"mapped_products":0,
            "complete_static_binding_products":0,"products_with_operations":0,"products_with_captures":0,"partial_protocol_products":0,"products_with_artifacts":0})
        g["aliases"].append(r["display_name"])
        # Case/punctuation aliases are merged only; no company names are inferred equivalent.
        for field in tuple(x for x in g if x.startswith("products") or x in {"mapped_products","complete_static_binding_products","partial_protocol_products"}):
            g[field]+=r[field]
    audits=[]
    for g in groups.values():
        useful=any(g[field] for field in ("products_with_identity","mapped_products","complete_static_binding_products","products_with_operations","products_with_captures","partial_protocol_products","products_with_artifacts"))
        vendor_workflow = guided.get(_norm(g["brand"]), {"identity": 0, "transport": 0})
        # A product-specific identity plus a static vendor HID/WinUSB dependency
        # is enough to define a safe C workflow: attach the matching device and
        # observe its official software while it remains the writer.  This does
        # not certify a protocol, HardwareVerified, or ProductionSafe state.
        if vendor_workflow["identity"] and vendor_workflow["transport"]:
            rank="C"
            blocker="remaining protocol fields require guided observation of the matching device through official vendor software"
        else:
            rank="D" if useful else "E"
            blocker=("no evidence-backed brand-wide guided workflow; existing protocol evidence is sparse or product-specific" if useful
                     else "catalog/product name only; no usable identity, protocol, runtime, or artifact evidence")
        audits.append({"brand":g["brand"],"aliases":sorted(set(g["aliases"])),"rank":rank,**{k:g[k] for k in g if k not in {"brand","aliases"}},
          "guided_device_possible":False,"guided_vendor_app_possible":bool(vendor_workflow["identity"] and vendor_workflow["transport"]),"vendor_forensics":vendor_workflow,"blocker":blocker,
          "rationale":"Conservative brand-level ranking: no A without broad validated plug-and-play coverage; no B/C without a concrete, evidence-backed guided workflow applicable to a representative share of products.",
          "evidence_references":{"registry":"products/device_identifiers/device_protocol_mappings/protocol_operations","bindings_generation":binding_id,"capture_identity_generation":capture_id}})
    audits.sort(key=lambda x:x["brand"].casefold())
    txt_path.parent.mkdir(parents=True,exist_ok=True)
    txt_path.write_text("\n".join(f"{x['brand']} - {x['rank']}" for x in audits)+"\n",encoding="utf8")
    distribution=Counter(x["rank"] for x in audits)
    result={"raw_vendor_like_identities":len(rows),"real_hardware_brands":len(audits),"entries_removed_as_non_brand_or_unverified":len(excluded),
      "aliases_merged":sum(max(0,len(x["aliases"])-1) for x in audits),"excluded_vendor_like_entries":sorted(excluded,key=str.casefold),
      "sanity_audit":{"pass":True,"rules":["curated consumer peripheral hardware maker allowlist","explicit aliases only","no projects/authors/URLs/chip vendors/placeholders/model names"]},
      "distribution":{rank:distribution[rank] for rank in "ABCDE"},"uncertain_brands":sum(x["rank"]=="D" for x in audits),"brands":audits}
    audit_path.write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding="utf8")
    return result
