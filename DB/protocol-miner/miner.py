#!/usr/bin/env python3
"""Peripheral Protocol Miner CLI. Static analysis only; never writes real HID."""

from miner.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
