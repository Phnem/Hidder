import json
from pathlib import Path

import pytest

from miner.config import default_settings
from miner.orchestrator.analyze import analyze_artifact
from miner.orchestrator.ingest import ingest_file

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


@pytest.mark.parametrize(("fixture", "expected_status", "expected_kind"), [
    ("webhid_vendor/configurator.js", "WRITE_SEMANTICS_CANDIDATE", "protocol.buffer_builder"),
    ("electron_vendor/package.json", "NO_TECHNICAL_EVIDENCE", "artifact.electron"),
    ("via_keyboard/keyboard.json", "IDENTITY_ONLY", "ecosystem.via_qmk"),
    ("inf_driver/keyboard.inf", "IDENTITY_ONLY", "identity.vid_pid"),
    ("native_vendor/driver.dll", "NO_TECHNICAL_EVIDENCE", "native.transport_hint"),
])
def test_golden_static_fixtures(tmp_path: Path, fixture: str, expected_status: str, expected_kind: str) -> None:
    source = FIXTURES / fixture
    settings = default_settings(root=tmp_path / "miner", cas_root=tmp_path / "cas")
    ingested = ingest_file(settings, source)
    analyzed = analyze_artifact(settings, ingested["sha256"])
    report_dir = settings.reports_dir / analyzed["run_id"]
    evidence = json.loads((report_dir / "evidence.json").read_text())
    run = json.loads((report_dir / "run.json").read_text())
    assert run["status"] == expected_status
    assert any(item["kind"] == expected_kind for item in evidence["observations"])
    assert (report_dir / "unknowns.md").exists()
    assert (report_dir / "registry_patch.json").exists()
