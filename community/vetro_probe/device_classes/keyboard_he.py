"""HE keyboard mandatory surface = mechanical + HE."""

from .keyboard_mechanical import MANDATORY_OPS as MECH_OPS

MANDATORY_OPS = [
    *MECH_OPS,
    "he.actuation",
    "he.rt.enabled",
    "he.rt.press",
    "he.rt.release",
    "he.deadzone",
    "he.per_key",
    "he.analog_w",
]
