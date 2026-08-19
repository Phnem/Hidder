import io
import zipfile
from pathlib import Path

import pytest

from miner.storage.pevidence import (
    PevidenceIntegrityError,
    PevidenceSecurityError,
    export_pevidence_bundle,
    import_pevidence_bundle,
    validate_pevidence_bundle,
)


def test_pevidence_export_and_import_roundtrip(tmp_path: Path) -> None:
    bundle_path = tmp_path / "aula_f75.pevidence"
    device_info = {"vendorId": 0x258A, "productId": 0x002A, "productName": "AULA F75"}
    software_info = {"name": "AULA Web Hub", "sha256": "f" * 64}
    traces = [
        {"method": "sendReport", "report_id": 9, "bytes_hex": "09130064", "length": 4, "ui_action_id": "act-01"}
    ]
    actions = [{"action_id": "act-01", "label": "Actuation 1.0mm"}]

    export_pevidence_bundle(
        output_path=bundle_path,
        device_info=device_info,
        software_info=software_info,
        traces=traces,
        actions=actions,
        restore_status="RESTORE_CONFIRMED",
    )

    assert bundle_path.is_file()

    # Validate
    validation = validate_pevidence_bundle(bundle_path)
    assert validation["valid"]
    assert len(validation["errors"]) == 0
    assert validation["manifest"]["schema_version"] == "peripheral.pevidence/1"

    # Import
    unpack_dir = tmp_path / "unpacked_pevid"
    observations = import_pevidence_bundle(bundle_path, unpack_dir)
    assert len(observations) >= 2

    # Check classes
    assert any(o.kind == "identity.vid_pid" and o.value["name"] == "AULA F75" for o in observations)
    assert any(o.kind == "dynamic.webhid_call" and o.value["report_id"] == 9 for o in observations)


def test_pevidence_corrupted_hash_rejection(tmp_path: Path) -> None:
    bundle_path = tmp_path / "tampered.pevidence"
    export_pevidence_bundle(
        output_path=bundle_path,
        device_info={"vendorId": 0x1234, "productId": 0x5678},
        software_info={"sha256": "a" * 64},
        traces=[{"method": "open"}],
    )

    # Tamper with device.json content inside the ZIP without updating integrity.json
    corrupted_path = tmp_path / "tampered_mod.pevidence"
    with zipfile.ZipFile(bundle_path, "r") as zin:
        with zipfile.ZipFile(corrupted_path, "w") as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename == "device.json":
                    data = b'{"tampered": true}'
                zout.writestr(item, data)

    validation = validate_pevidence_bundle(corrupted_path)
    assert not validation["valid"]
    assert any("Integrity mismatch" in err for err in validation["errors"])

    with pytest.raises(PevidenceIntegrityError):
        import_pevidence_bundle(corrupted_path, tmp_path / "bad_unpack")


def test_pevidence_path_traversal_rejection(tmp_path: Path) -> None:
    malicious_path = tmp_path / "traversal.pevidence"
    with zipfile.ZipFile(malicious_path, "w") as zf:
        zf.writestr("manifest.json", b'{"schema_version": "peripheral.pevidence/1"}')
        zf.writestr("integrity.json", b"{}")
        zf.writestr("../../../evil.exe", b"malicious payload")

    with pytest.raises(PevidenceSecurityError):
        validate_pevidence_bundle(malicious_path)
