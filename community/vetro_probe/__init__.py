"""Vetro Probe — Physical Validation of B Preview Bundles.

Isolated runtime: community/vetro_probe/* only.
Does not import from community/probe/* to preserve isolation.
Tool version mirrors Hidder but emits vetro.hardware-validation.v1.
"""

from __future__ import annotations

TOOL_VERSION = "0.4.0"
SCHEMA_CERTIFICATE = "vetro.hardware-validation.v1"
SCHEMA_BUNDLE = "vetro.preview-bundle.v1"
