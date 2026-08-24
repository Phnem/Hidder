"""Mouse mandatory surface helper."""

from __future__ import annotations

MANDATORY_OPS = [
    "mouse.dpi",
    "mouse.polling",
    "mouse.remap",
    "mouse.lod",
    "mouse.motion_sync",
    "mouse.angle_snap",
    "mouse.debounce",
    "mouse.profiles",
    "light.effect",
    "battery.level",
]
OPTIONAL_IF_EXPOSED = {"mouse.lod", "mouse.motion_sync", "mouse.angle_snap", "light.effect", "battery.level"}
