"""Single-file launcher for VetroProbe (en) PyInstaller build."""

import sys
from pathlib import Path

cur_dir = Path(__file__).resolve().parent
if str(cur_dir) not in sys.path:
    sys.path.insert(0, str(cur_dir))
community_dir = cur_dir.parent
if str(community_dir) not in sys.path:
    sys.path.insert(0, str(community_dir))
parent_dir = community_dir.parent
if str(parent_dir) not in sys.path:
    sys.path.insert(0, str(parent_dir))

try:
    from vetro_probe.cli import main
except ImportError:
    from community.vetro_probe.cli import main

if __name__ == "__main__":
    sys.exit(main())
