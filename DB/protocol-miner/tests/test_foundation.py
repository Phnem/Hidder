import json
from pathlib import Path

from miner.config import default_settings
from miner.orchestrator.ingest import ingest_file
from miner.orchestrator.analyze import analyze_artifact
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


def test_webhid_static_analysis_emits_traceable_identity_and_topology(tmp_path: Path) -> None:
    source = tmp_path / "configurator.js"
    source.write_text("navigator.hid.requestDevice({filters:[{vendorId: 0x372E, productId: 0x103E}]}); device.sendReport(9, new Uint8Array([0x13, 0x00]));")
    settings = default_settings(root=tmp_path / "miner", cas_root=tmp_path / "shared-cas")
    ingested = ingest_file(settings, source)
    analyzed = analyze_artifact(settings, ingested["sha256"])
    evidence = json.loads((settings.workspace_dir / "runs" / analyzed["run_id"] / "evidence.json").read_text())
    candidate = json.loads((settings.reports_dir / analyzed["run_id"] / "protocol_candidate.json").read_text())
    assert any(item["kind"] == "identity.vid_pid" for item in evidence["observations"])
    assert candidate["identity"][0]["vid"] == 0x372E
    assert candidate["topology"][0]["report_id"] == 9
    assert candidate["commands"]["packet_1"]["bytes"] == [19, 0]
