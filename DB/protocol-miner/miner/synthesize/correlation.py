"""UI semantic action to transport packet correlation engine."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from miner.schemas.models import Observation


@dataclass
class CorrelatedPacket:
    method: str
    report_id: int | None
    bytes_hex: str | None
    byte_length: int
    transport: str = "webhid"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CorrelatedSemanticAction:
    action_id: str
    control_id: str
    label: str
    old_value: Any
    new_value: Any
    semantic_context: str
    step_type: str
    packets: list[CorrelatedPacket] = field(default_factory=list)
    changing_byte_offsets: list[int] = field(default_factory=list)
    constant_prefix_hex: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def correlate_actions_with_traces(
    steps: list[dict[str, Any]],
    trace_observations: list[Observation],
) -> list[CorrelatedSemanticAction]:
    """Link UI experiment steps to observed dynamic transport packets by action_id."""
    traces_by_action: dict[str, list[dict[str, Any]]] = {}
    for obs in trace_observations:
        val = obs.value
        act_id = val.get("ui_action_id")
        if act_id:
            traces_by_action.setdefault(act_id, []).append(val)

    correlated_actions: list[CorrelatedSemanticAction] = []

    for step in steps:
        act_id = step.get("action_id", "")
        packets_data = traces_by_action.get(act_id, [])
        packets = [
            CorrelatedPacket(
                method=p.get("method", "sendReport"),
                report_id=p.get("report_id"),
                bytes_hex=p.get("bytes_hex"),
                byte_length=p.get("length", 0),
                transport=p.get("transport", "webhid"),
            )
            for p in packets_data
            if p.get("bytes_hex") is not None
        ]

        action = CorrelatedSemanticAction(
            action_id=act_id,
            control_id=step.get("control_id", ""),
            label=step.get("label", ""),
            old_value=step.get("old_value"),
            new_value=step.get("new_value"),
            semantic_context=step.get("semantic_context", ""),
            step_type=step.get("step_type", "experiment"),
            packets=packets,
        )

        # Analyze changing bytes across packets if write packets exist
        write_packets = [bytes.fromhex(p.bytes_hex) for p in packets if p.bytes_hex]
        if len(write_packets) >= 1:
            # Detect constant prefix (e.g. command opcode)
            first = write_packets[0]
            prefix_len = 0
            for i in range(len(first)):
                if all(len(pkt) > i and pkt[i] == first[i] for pkt in write_packets):
                    prefix_len = i + 1
                else:
                    break
            if prefix_len > 0:
                action.constant_prefix_hex = first[:prefix_len].hex()

        correlated_actions.append(action)

    return correlated_actions
