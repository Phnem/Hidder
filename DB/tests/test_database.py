import pytest
from pathlib import Path
from ingest.storage.database import RegistryDatabase
from ingest.normalize.evidence import (
    RawArtifact, DeviceIdentifierFact, ProtocolHintFact, GenericFact, EvidenceLevel
)


def test_database_lifecycle(tmp_path):
    db_file = tmp_path / "test_reg.sqlite"
    db = RegistryDatabase(db_file)

    # 1. Vendor & Product
    v_id = db.get_or_create_vendor("aula", "AULA", "https://aulastar.com")
    p_id, is_new = db.upsert_product(v_id, "AULA Hero 84 HE", "Hero 84 HE", "keyboard", "https://aula.com/hero84", run_id="run1")
    assert is_new is True

    # Upsert again -> not new
    p_id2, is_new2 = db.upsert_product(v_id, "AULA Hero 84 HE", "Hero 84 HE", "keyboard", "https://aula.com/hero84", run_id="run2")
    assert p_id == p_id2
    assert is_new2 is False

    # 2. Artifact & Link
    art = RawArtifact(
        original_url="https://aula.com/hub.zip",
        filename="hub.zip",
        size=1024,
        sha256="abcd1234ef567890abcd1234ef567890abcd1234ef567890abcd1234ef567890",
        vendor="aula"
    )
    sha, art_is_new, changed = db.upsert_artifact(art, v_id, run_id="run1")
    assert art_is_new is True
    assert changed is False

    db.link_product_artifact(p_id, sha)

    # 3. Identifiers & Protocol Hints
    id_fact = DeviceIdentifierFact(
        product_id=p_id,
        vid=0x372E,
        pid=0x103E,
        vid_hex="0x372E",
        pid_hex="0x103E",
        artifact_sha256=sha,
        evidence_level=EvidenceLevel.LEVEL_2_DEVICE_IDENTITY
    )
    assert db.upsert_device_identifier(id_fact, run_id="run1") is True
    # duplicate check
    assert db.upsert_device_identifier(id_fact, run_id="run1") is False

    hint_fact = ProtocolHintFact(
        product_id=p_id,
        hint_key="sdkModuleName",
        hint_value="bytech",
        artifact_sha256=sha,
        evidence_level=EvidenceLevel.LEVEL_3_PROTOCOL_HINT
    )
    assert db.upsert_protocol_hint(hint_fact, run_id="run1") is True

    # 4. Verification queries
    counts = db.get_summary_counts()
    assert counts["total_products"] == 1
    assert counts["total_artifacts"] == 1
    assert counts["total_vid_pids"] == 1
    assert counts["total_hints"] == 1

    details = db.get_product_with_details("Hero 84 HE")
    assert len(details) == 1
    assert len(details[0]["identifiers"]) == 1
    assert details[0]["identifiers"][0]["vid_hex"] == "0x372E"
    assert len(details[0]["protocol_hints"]) == 1
