"""Transform fitting and unseen prediction validation engine."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from miner.synthesize.correlation import CorrelatedSemanticAction


@dataclass
class TransformHypothesis:
    parameter_label: str
    transform_type: str  # "linear", "enum_map", "boolean_bit", "unknown"
    scale: float | None = None
    offset: float | None = None
    mapping: dict[str, int] | None = None
    byte_offset: int = 0
    byte_length: int = 1
    endianness: str = "big"
    status: str = "INSUFFICIENT_DATA"  # "CONFIRMED", "REJECTED_CONTRADICTED", "INSUFFICIENT_DATA"
    validation_point_ui: Any = None
    predicted_raw: int | None = None
    actual_raw: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def fit_and_validate_transform(actions: list[CorrelatedSemanticAction]) -> TransformHypothesis:
    """Infer parameter encoding transform from training points and test against validation point."""
    if not actions:
        return TransformHypothesis("unknown", "unknown")

    label = actions[0].label
    training_steps = [a for a in actions if a.step_type == "experiment" and a.packets]
    validation_steps = [a for a in actions if a.step_type == "validation_point" and a.packets]

    if not training_steps:
        return TransformHypothesis(label, "unknown", status="INSUFFICIENT_DATA")

    # Find the payload byte that changes across training points
    all_packets = [bytes.fromhex(a.packets[0].bytes_hex) for a in training_steps if a.packets[0].bytes_hex]
    if not all_packets or len(all_packets) < 1:
        return TransformHypothesis(label, "unknown", status="INSUFFICIENT_DATA")

    pkt_len = len(all_packets[0])
    differing_offsets: list[int] = []
    for i in range(pkt_len):
        vals = {pkt[i] for pkt in all_packets if len(pkt) > i}
        if len(vals) > 1:
            differing_offsets.append(i)

    byte_idx = differing_offsets[0] if differing_offsets else 0

    # Extract UI and Raw pairs
    pairs: list[tuple[float, int]] = []
    for action in training_steps:
        try:
            ui_num = float(action.new_value)
            raw_pkt = bytes.fromhex(action.packets[0].bytes_hex or "")
            # Check 1-byte or 2-byte integer
            if byte_idx + 1 < len(raw_pkt) and (byte_idx + 1) in differing_offsets:
                raw_val = (raw_pkt[byte_idx] << 8) | raw_pkt[byte_idx + 1]
                byte_len = 2
            else:
                raw_val = raw_pkt[byte_idx]
                byte_len = 1
            pairs.append((ui_num, raw_val))
        except (ValueError, TypeError, IndexError):
            pass

    if len(pairs) < 2:
        return TransformHypothesis(label, "unknown", status="INSUFFICIENT_DATA")

    # Fit linear: raw = scale * ui + offset
    (x1, y1), (x2, y2) = pairs[0], pairs[1]
    if abs(x2 - x1) < 1e-6:
        scale = 1.0
        offset = float(y1 - x1)
    else:
        scale = (y2 - y1) / (x2 - x1)
        offset = y1 - scale * x1

    hyp = TransformHypothesis(
        parameter_label=label,
        transform_type="linear",
        scale=round(scale, 4),
        offset=round(offset, 4),
        byte_offset=byte_idx,
        byte_length=1 if scale <= 255 and (scale * 10 + offset) <= 255 else 2,
    )

    # Validate against unseen validation point if present
    if validation_steps:
        val_action = validation_steps[0]
        try:
            val_ui = float(val_action.new_value)
            expected_raw = int(round(scale * val_ui + offset))
            val_pkt = bytes.fromhex(val_action.packets[0].bytes_hex or "")
            if hyp.byte_length == 2 and byte_idx + 1 < len(val_pkt):
                actual_raw = (val_pkt[byte_idx] << 8) | val_pkt[byte_idx + 1]
            else:
                actual_raw = val_pkt[byte_idx]

            hyp.validation_point_ui = val_ui
            hyp.predicted_raw = expected_raw
            hyp.actual_raw = actual_raw

            if expected_raw == actual_raw:
                hyp.status = "CONFIRMED"
            else:
                hyp.status = "REJECTED_CONTRADICTED"
        except (ValueError, TypeError, IndexError):
            hyp.status = "INSUFFICIENT_DATA"
    else:
        hyp.status = "CONFIRMED"

    return hyp
