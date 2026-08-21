from ingest.ai_model_reconciliation import AIModelReconciliation, parse_ai_sections
from ingest.normalize.evidence import RawSource, SourceType
from ingest.storage.database import RegistryDatabase


def test_parser_keeps_ai_sections_and_categories_separate():
    content = """QWEN:
[AULA]
Мыши: SC620
Клавиатуры: F75
GEMINI:
[AULA]
  КЛАВИАТУРЫ (1 шт.):
    • F75 Max
  МЫШИ (1 шт.):
    • SC800
Claude:
---
AULA
---
[МЫШИ]
- SC620
[КЛАВИАТУРЫ]
- HERO 84 HE
Meta AI:
=== БРЕНД: AULA ===
  МЫШИ (1):
    - SC800
  КЛАВИАТУРЫ (1):
    - F99
"""
    items = parse_ai_sections(content, {"aula"})
    assert {(x.source_ai, x.category, x.raw_model_name) for x in items} == {
        ("QWEN", "MOUSE", "SC620"), ("QWEN", "KEYBOARD", "F75"),
        ("GEMINI", "KEYBOARD", "F75 Max"), ("GEMINI", "MOUSE", "SC800"),
        ("CLAUDE", "MOUSE", "SC620"), ("CLAUDE", "KEYBOARD", "HERO 84 HE"),
        ("META_AI", "MOUSE", "SC800"), ("META_AI", "KEYBOARD", "F99"),
    }


def test_parser_keeps_delimited_unknown_brand_as_new_brand_candidate_input():
    content = """Claude:
----------------
Future Peripheral Co
----------------
[МЫШИ]
- FP-1
"""
    parsed = parse_ai_sections(content, {"aula"})
    assert len(parsed) == 1
    assert parsed[0].raw_brand == "Future Peripheral Co"
    assert parsed[0].raw_model_name == "FP-1"


def test_official_domain_and_tier1_category_override_legacy_metadata(tmp_path):
    db = RegistryDatabase(tmp_path / "registry.sqlite")
    aula_vendor = db.get_or_create_vendor("aula", "AULA")
    attack_vendor = db.get_or_create_vendor("attack_shark", "Attack Shark")
    db.record_source(RawSource(url="https://hub.aulastar.com/config/devices.json", source_type=SourceType.WEB_CONFIGURATOR, vendor="aula"))
    db.upsert_product(aula_vendor, "AULA HERO 84 HE", "HERO 84 HE", "keyboard", "hero84he", product_url="https://www.aulastar.com/product/hero-84-he/")
    db.upsert_product(attack_vendor, "Attack Shark V3 PRO", "V3 PRO Ultra-Light", "keyboard", "v3pro", product_url="https://attackshark.com/products/attack-shark-v3pro-gaming-mouse")
    reconciliation = AIModelReconciliation(tmp_path / "registry.sqlite", tmp_path / "input.txt", tmp_path / "reports")
    with db.connection() as conn:
        reconciliation._load_brand_lookup(conn)
        truth = reconciliation.build_truth_corpus(conn)
    hero = next(item for item in truth if item["normalized_name"] == "hero84he")
    v3 = next(item for item in truth if item["normalized_name"] == "v3pro")
    assert hero["official"] is True
    assert "RECORDED_VENDOR_OWNED_SOURCE" in hero["official_domain_provenance"]
    assert v3["category"] == "MOUSE"
    assert v3["category_data_conflict"] is True
