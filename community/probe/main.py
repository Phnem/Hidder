"""CLI entry point for Peripheral Community Research Probe."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ensure UTF-8 console output on Windows
if sys.platform == "win32":
    import codecs
    import ctypes
    sys.stdout = codecs.getwriter("utf-8")(sys.stdout.buffer, "replace")
    sys.stderr = codecs.getwriter("utf-8")(sys.stderr.buffer, "replace")
    try:
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        ctypes.windll.kernel32.SetConsoleCP(65001)
    except Exception:
        pass

try:
    from probe.wizard import CommunityResearchWizard
except ImportError:
    from community.probe.wizard import CommunityResearchWizard


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Peripheral Research Probe — Guided Protocol Observation for Keyboards & Mice"
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Run in synthetic demo/smoke test mode without physical device interaction"
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help="Custom output directory for the resulting JSON file (default: current directory)"
    )
    
    args = parser.parse_args()
    out_dir = Path(args.output_dir) if args.output_dir else Path.cwd()
    
    wizard = CommunityResearchWizard(is_demo=args.demo, output_dir=out_dir)
    wizard.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
