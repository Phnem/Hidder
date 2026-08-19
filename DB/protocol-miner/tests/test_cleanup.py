from pathlib import Path

from miner.config import default_settings
from miner.storage.cleanup import clean_workspace


def test_cleanup_removes_only_derived_data_not_shared_cas(tmp_path: Path) -> None:
    settings = default_settings(root=tmp_path / "miner", cas_root=tmp_path / "cas")
    settings.workspace_dir.mkdir(parents=True)
    settings.reports_dir.mkdir(parents=True)
    cas_file = settings.cas_root / "aa" / ("a" * 64)
    cas_file.parent.mkdir(parents=True)
    cas_file.write_text("source bytes")
    removed = clean_workspace(settings)
    assert str(settings.workspace_dir) in removed
    assert not settings.workspace_dir.exists()
    assert cas_file.exists()
