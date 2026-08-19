"""Single-file launcher for PyInstaller one-file executable build."""

import sys
from pathlib import Path

cur_dir = Path(__file__).resolve().parent
if str(cur_dir) not in sys.path:
    sys.path.insert(0, str(cur_dir))

parent_dir = cur_dir.parent
if str(parent_dir) not in sys.path:
    sys.path.insert(0, str(parent_dir))

try:
    from probe.main import main
except ImportError:
    from community.probe.main import main

if __name__ == "__main__":
    sys.exit(main())
