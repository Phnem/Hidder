"""Bounded lexical JS data-flow correlation for simple, explicit HID buffer builders.

This is deliberately conservative: it handles only a function body where a
literal report ID, typed-array buffer, indexed writes, and HID sink occur in
the same lexical scope. Complex/minified control flow remains unknown rather
than being guessed.
"""

from __future__ import annotations

import re
from typing import Any

from miner.schemas.models import ConfidenceClass, Observation
from miner.static.extract import _make, _number

_FUNCTION = re.compile(r"(?:function\s+([A-Za-z_$][\w$]*)\s*\(([^)]*)\)|(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*\(([^)]*)\)\s*=>)\s*\{([^{}]{1,16000})\}", re.DOTALL)
_BUFFER = re.compile(r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*new\s+Uint8Array\s*\(\s*(\d+)\s*\)")
_WRITE = re.compile(r"\b([A-Za-z_$][\w$]*)\s*\[\s*(\d+)\s*\]\s*=\s*([^;\n]+)")
_SINK = re.compile(r"\b(sendReport|sendFeatureReport)\s*\(\s*(0x[0-9a-fA-F]+|\d+)\s*,\s*([A-Za-z_$][\w$]*)\s*\)")
_DANGEROUS_NAME = re.compile(r"firmware|flash|bootloader|erase|factory.?reset|calibrat|dfu", re.IGNORECASE)

_SEMANTICS = {
    "actuation": "he.actuation.write", "rapidtrigger": "he.rapid_trigger.write", "rapid_trigger": "he.rapid_trigger.write",
    "dpi": "mouse.dpi.write", "polling": "mouse.polling.write", "debounce": "keyboard.debounce.write",
    "keymap": "keyboard.keymap.write", "macro": "keyboard.macro.write",
}


def _semantic(name: str) -> str | None:
    compact = name.lower().replace("-", "_")
    return next((value for key, value in _SEMANTICS.items() if key in compact), None)


def scan_simple_buffer_flows(sha256: str, source_path: str, text: str) -> list[Observation]:
    observations: list[Observation] = []
    for function_match in _FUNCTION.finditer(text):
        name = function_match.group(1) or function_match.group(3)
        params = (function_match.group(2) or function_match.group(4) or "").strip()
        body = function_match.group(5)
        buffers = {match.group(1): int(match.group(2)) for match in _BUFFER.finditer(body)}
        for sink in _SINK.finditer(body):
            buffer_name = sink.group(3)
            if buffer_name not in buffers:
                continue
            writes: list[dict[str, Any]] = []
            for write in _WRITE.finditer(body):
                if write.group(1) == buffer_name:
                    writes.append({"offset": int(write.group(2)), "expression": write.group(3).strip()})
            if not writes:
                continue
            value = {
                "function": name, "parameters": [value.strip() for value in params.split(",") if value.strip()],
                "method": sink.group(1), "report_id": _number(sink.group(2)), "buffer_length": buffers[buffer_name],
                "field_writes": sorted(writes, key=lambda item: item["offset"]),
            }
            semantic = _semantic(name)
            if semantic:
                value["semantic_candidate"] = semantic
            observations.append(_make(sha256, f"{source_path}:byte={function_match.start()}", "protocol.buffer_builder", value, ConfidenceClass.VERIFIED_SOURCE_CODE))
            if _DANGEROUS_NAME.search(name):
                observations.append(_make(sha256, f"{source_path}:byte={function_match.start()}", "protocol.dangerous_command_candidate", {
                    "function": name, "report_id": value["report_id"], "reason": "dangerous operation named in proven HID buffer builder",
                }, ConfidenceClass.VERIFIED_SOURCE_CODE))
    return observations
