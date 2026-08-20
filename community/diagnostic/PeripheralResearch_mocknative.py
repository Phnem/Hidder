"""Diagnostic Build E: Full UI/workflow but with mock native injection primitives (no CRT/WPM)."""

import sys
from pathlib import Path

cur_dir = Path(__file__).resolve().parent.parent
if str(cur_dir) not in sys.path:
    sys.path.insert(0, str(cur_dir))

parent_dir = cur_dir.parent
if str(parent_dir) not in sys.path:
    sys.path.insert(0, str(parent_dir))

from community.probe.observer import PassiveTransportObserver
from community.probe.wizard import CommunityResearchWizard


class MockNativeObserver(PassiveTransportObserver):
    def attach_native(self, pid: int, process_basename: str) -> bool:
        self.backend_type = "native_mock"
        self.capture_metadata.mechanism = "mock_api_hook_no_injection"
        self.capture_metadata.target_process = process_basename
        self.capture_metadata.target_pid = pid
        self.capture_metadata.observer_attached = True
        self.capture_metadata.device_handle_bound = True
        self.capture_metadata.hooks_installed = ["WriteFile", "HidD_SetFeature"]
        return True


class MockNativeWizard(CommunityResearchWizard):
    def __init__(self, is_demo: bool = False, output_dir: Path | None = None) -> None:
        super().__init__(is_demo=is_demo, output_dir=output_dir)
        self.observer = MockNativeObserver()


if __name__ == "__main__":
    wizard = MockNativeWizard(is_demo="--demo" in sys.argv)
    wizard.run()
