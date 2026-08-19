import json
from pathlib import Path

from miner.config import default_settings
from miner.orchestrator.ingest import ingest_file
from miner.storage.cas import ContentAddressedStore


def test_ingest_creates_provenance_and_review_outputs(tmp_path: Path) -> None:
    source = tmp_path / "vendor.zip"
    source.write_bytes(b"PK\\x03\\x04fixture")
    settings = default_settings(root=tmp_path / "miner", cas_root=tmp_path / "shared-cas")

    result = ingest_file(settings, source, vendor="fixture-vendor")

    provenance = json.loads((settings.workspace_dir / "artifacts" / result["sha256"] / "provenance.json").read_text())
    candidate = json.loads((settings.workspace_dir / "runs" / result["run_id"] / "protocol_candidate.json").read_text())
    assert provenance["schema"] == "peripheral.artifact/1"
    assert provenance["source_type"] == "user_supplied_vendor_artifact"
    assert candidate["schema"] == "peripheral.protocol-candidate/1"
    assert (settings.reports_dir / result["run_id"] / "summary.md").exists()
    assert ContentAddressedStore(settings.cas_root).path_for(result["sha256"]).exists()
