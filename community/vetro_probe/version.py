"""Vetro Probe build and version metadata."""

from __future__ import annotations

import time
from typing import Any

PROBE_APP_VERSION = "0.3.1"
PROBE_ENGINE_VERSION = "0.3.1"
DEFAULT_KNOWLEDGE_REVISION = "aula_kb_v3_r1"

try:
    from ._build_metadata import (  # type: ignore
        BUILD_COMMIT as _BUILD_COMMIT,
        BUILD_DIRTY as _BUILD_DIRTY,
        BUILD_TIMESTAMP as _BUILD_TIMESTAMP,
    )
except Exception:
    _BUILD_COMMIT = "unknown"
    _BUILD_DIRTY = False
    _BUILD_TIMESTAMP = time.time()


def get_build_commit() -> str:
    """Return immutable build commit embedded at build time (never invokes git at runtime)."""
    return _BUILD_COMMIT


def is_build_dirty() -> bool:
    return _BUILD_DIRTY


def get_build_timestamp() -> float:
    return _BUILD_TIMESTAMP


def get_build_info() -> dict[str, Any]:
    return {
        "probe_app_version": PROBE_APP_VERSION,
        "probe_engine_version": PROBE_ENGINE_VERSION,
        "build_commit": _BUILD_COMMIT,
        "build_dirty": _BUILD_DIRTY,
        "build_timestamp": _BUILD_TIMESTAMP,
        "knowledge_revision": DEFAULT_KNOWLEDGE_REVISION,
    }
