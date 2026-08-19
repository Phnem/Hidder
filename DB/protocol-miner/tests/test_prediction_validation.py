from miner.schemas.models import ConfidenceClass, Observation
from miner.synthesize.correlation import CorrelatedPacket, CorrelatedSemanticAction, correlate_actions_with_traces
from miner.synthesize.prediction import fit_and_validate_transform


def test_linear_transform_prediction_confirmed() -> None:
    # 0.50mm -> 50 (0x32), 1.00mm -> 100 (0x64), validation: 2.20mm -> 220 (0xdc)
    actions = [
        CorrelatedSemanticAction(
            action_id="act-01",
            control_id="actuation",
            label="Actuation",
            old_value=0.2,
            new_value=0.5,
            semantic_context="actuation:0.5mm",
            step_type="experiment",
            packets=[CorrelatedPacket("sendReport", 9, "09130032", 4)],
        ),
        CorrelatedSemanticAction(
            action_id="act-02",
            control_id="actuation",
            label="Actuation",
            old_value=0.5,
            new_value=1.0,
            semantic_context="actuation:1.0mm",
            step_type="experiment",
            packets=[CorrelatedPacket("sendReport", 9, "09130064", 4)],
        ),
        CorrelatedSemanticAction(
            action_id="act-val",
            control_id="actuation",
            label="Actuation",
            old_value=1.0,
            new_value=2.2,
            semantic_context="actuation:2.2mm",
            step_type="validation_point",
            packets=[CorrelatedPacket("sendReport", 9, "091300dc", 4)],
        ),
    ]

    hyp = fit_and_validate_transform(actions)
    assert hyp.status == "CONFIRMED"
    assert hyp.scale == 100.0
    assert hyp.offset == 0.0
    assert hyp.predicted_raw == 220
    assert hyp.actual_raw == 220


def test_linear_transform_prediction_rejected_on_mismatch() -> None:
    # Expected 220 (0xdc), but actual observed was 180 (0xb4)
    actions = [
        CorrelatedSemanticAction(
            action_id="act-01",
            control_id="actuation",
            label="Actuation",
            old_value=0.2,
            new_value=0.5,
            semantic_context="actuation:0.5mm",
            step_type="experiment",
            packets=[CorrelatedPacket("sendReport", 9, "09130032", 4)],
        ),
        CorrelatedSemanticAction(
            action_id="act-02",
            control_id="actuation",
            label="Actuation",
            old_value=0.5,
            new_value=1.0,
            semantic_context="actuation:1.0mm",
            step_type="experiment",
            packets=[CorrelatedPacket("sendReport", 9, "09130064", 4)],
        ),
        CorrelatedSemanticAction(
            action_id="act-val",
            control_id="actuation",
            label="Actuation",
            old_value=1.0,
            new_value=2.2,
            semantic_context="actuation:2.2mm",
            step_type="validation_point",
            packets=[CorrelatedPacket("sendReport", 9, "091300b4", 4)],
        ),
    ]

    hyp = fit_and_validate_transform(actions)
    assert hyp.status == "REJECTED_CONTRADICTED"
    assert hyp.predicted_raw == 220
    assert hyp.actual_raw == 180
