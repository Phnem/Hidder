"""Web-only diagnostic entry point."""

import sys
from pathlib import Path

cur_dir = Path(__file__).resolve().parent.parent
if str(cur_dir) not in sys.path:
    sys.path.insert(0, str(cur_dir))

parent_dir = cur_dir.parent
if str(parent_dir) not in sys.path:
    sys.path.insert(0, str(parent_dir))

from community.diagnostic.observer_webonly import WebOnlyTransportObserver
from community.probe.wizard import CommunityResearchWizard


class WebOnlyWizard(CommunityResearchWizard):
    def __init__(self, is_demo: bool = False, output_dir: Path | None = None) -> None:
        super().__init__(is_demo=is_demo, output_dir=output_dir)
        self.observer = WebOnlyTransportObserver()


if __name__ == "__main__":
    wizard = WebOnlyWizard(is_demo="--demo" in sys.argv)
    wizard.run()
