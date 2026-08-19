"""Tests for 87 canonical brands, brand relationships, non-merging guarantees, and generic adapters."""

import pytest
import tempfile
from pathlib import Path

from ingest.brands.canonical import (
    ALL_CANONICAL_BRANDS, get_brand_by_slug, get_brands_by_batch,
    BrandType, RelationshipType, DiscoveryStatus
)
from ingest.storage.database import RegistryDatabase
from ingest.normalize.evidence import RawProduct, RawSource, SourceType
from ingest.adapters.shopify import ShopifyCatalogAdapter
from ingest.adapters.download_center import DownloadCenterAdapter


def test_all_100_brands_exist_and_unique():
    """Verify that exactly 100 canonical brands are registered with unique slugs."""
    assert len(ALL_CANONICAL_BRANDS) == 100
    slugs = [b.slug for b in ALL_CANONICAL_BRANDS]
    assert len(slugs) == len(set(slugs)), "Duplicate brand slugs found"


def test_brand_batches_partitioning():
    """Verify that batches A, B, C, and pilot correctly partition the brand list."""
    pilot = get_brands_by_batch("pilot")
    batch_a = get_brands_by_batch("A")
    batch_b = get_brands_by_batch("B")
    batch_c = get_brands_by_batch("C")
    all_b = get_brands_by_batch("all")

    assert len(pilot) == 5  # aula, atk, vxe, epomaker, keychron
    assert len(batch_a) == 18
    assert len(batch_b) == 36
    assert len(batch_c) == 41
    assert len(pilot) + len(batch_a) + len(batch_b) + len(batch_c) == 100
    assert len(all_b) == 100


def test_brand_relationships_and_non_merging():
    """Verify that parent/sub-brand and sibling brands remain completely separate entities in DB."""
    with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as f:
        db_file = Path(f.name)

    try:
        db = RegistryDatabase(db_file)
        db.init_db()

        # Check separate identities
        a4tech = db.get_brand_with_details("a4tech")
        bloody = db.get_brand_with_details("bloody")
        assert a4tech is not None and bloody is not None
        assert a4tech["id"] != bloody["id"]
        assert a4tech["canonical_name"] == "A4Tech"
        assert bloody["canonical_name"] == "Bloody"

        # Check relationship exists without product merging
        bloody_rels = bloody.get("relationships", [])
        assert any(r["target_slug"] == "a4tech" and r["relationship_type"] == "parent" for r in bloody_rels)

        # Check VGN / VXE / ATK separation
        vgn = db.get_brand_with_details("vgn")
        vxe = db.get_brand_with_details("vxe")
        atk = db.get_brand_with_details("atk")
        assert vgn["id"] != vxe["id"] != atk["id"]

        # Check Keychron / Lemokey separation
        keychron = db.get_brand_with_details("keychron")
        lemokey = db.get_brand_with_details("lemokey")
        assert keychron["id"] != lemokey["id"]

        # Check Akko / MonsGeek separation
        akko = db.get_brand_with_details("akko")
        monsgeek = db.get_brand_with_details("monsgeek")
        assert akko["id"] != monsgeek["id"]

        # Check WLMOUSE / Scyrox separation
        wlmouse = db.get_brand_with_details("wlmouse")
        scyrox = db.get_brand_with_details("scyrox")
        assert wlmouse["id"] != scyrox["id"]

        # Check Red Square / IO separation
        red_square = db.get_brand_with_details("red_square")
        io = db.get_brand_with_details("io_by_red_square")
        assert red_square["id"] != io["id"]

        # Check Wuque Studio / Meletrix / Chilkey separation
        wuque = db.get_brand_with_details("wuque_studio")
        meletrix = db.get_brand_with_details("meletrix")
        chilkey = db.get_brand_with_details("chilkey")
        assert wuque["id"] != meletrix["id"] != chilkey["id"]

        # Insert products with identical model names for separate brands and verify no merging
        p_a4tech, is_new1 = db.upsert_product(a4tech["id"], "Bloody V8M", "V8M", "mouse", identity_key="v8m")
        p_bloody, is_new2 = db.upsert_product(bloody["id"], "Bloody V8M", "V8M", "mouse", identity_key="v8m")
        assert is_new1 is True and is_new2 is True
        assert p_a4tech != p_bloody, "Products from different brands must never merge!"
    finally:
        if db_file.exists():
            db_file.unlink()


def test_brand_crawl_status_recording():
    """Verify that honest discovery status and metrics are recorded per brand."""
    with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as f:
        db_file = Path(f.name)

    try:
        db = RegistryDatabase(db_file)
        db.init_db()
        razer = db.get_brand_with_details("razer")
        assert razer is not None

        db.record_brand_crawl_status(
            brand_id=razer["id"],
            run_id="test_run",
            status=DiscoveryStatus.METADATA_ONLY.value,
            products_count=50,
            devices_count=45,
            artifacts_count=2,
            artifacts_bytes=10000000,
            vid_pids_count=5,
            hints_count=10,
            tech_evidence_products=5,
            blocking_reason=None
        )

        updated = db.get_brand_with_details("razer")
        assert updated["latest_status"] is not None
        assert updated["latest_status"]["status"] == DiscoveryStatus.METADATA_ONLY.value
        assert updated["latest_status"]["products_count"] == 50
    finally:
        if db_file.exists():
            db_file.unlink()
