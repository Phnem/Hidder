from miner.synthesize.corroboration import (
    cluster_protocol_families,
    corroborate_submissions,
)


def test_multi_user_same_firmware_corroboration() -> None:
    submissions = [
        {
            "submission_id": "user-A",
            "firmware_version": "1.04",
            "facts": {"actuation_report_id": 9, "actuation_scale": 100},
        },
        {
            "submission_id": "user-B",
            "firmware_version": "1.04",
            "facts": {"actuation_report_id": 9, "actuation_scale": 100},
        },
    ]

    corroborated, contradictions = corroborate_submissions(submissions)
    assert len(contradictions) == 0
    assert len(corroborated) == 2

    act_scale = next(c for c in corroborated if c.fact_key == "actuation_scale")
    assert act_scale.corroboration_count == 2
    assert "user-A" in act_scale.corroborating_submissions
    assert "user-B" in act_scale.corroborating_submissions
    assert act_scale.confidence == "InferredStrong"


def test_firmware_branch_contradiction_is_preserved() -> None:
    submissions = [
        {
            "submission_id": "user-A",
            "firmware_version": "1.04",
            "facts": {"max_actuation_mm": 3.8},
        },
        {
            "submission_id": "user-C",
            "firmware_version": "1.05",
            "facts": {"max_actuation_mm": 4.0},
        },
    ]

    corroborated, contradictions = corroborate_submissions(submissions)
    assert len(corroborated) == 0
    assert len(contradictions) == 1
    conflict = contradictions[0]
    assert conflict["fact_key"] == "max_actuation_mm"
    assert len(conflict["variants"]) == 2
    vals = [v["value"] for v in conflict["variants"]]
    assert 3.8 in vals
    assert 4.0 in vals


def test_family_clustering_negative_control_rejects_usage_page_alone() -> None:
    devices = [
        {
            "model": "Vendor Keyboard A",
            "usage_page": 0xFF00,
            "report_ids": [0x08, 0x09],
            "opcode_prefix": "1300",
            "packet_length": 64,
        },
        {
            "model": "Unrelated Brand Mouse B",
            "usage_page": 0xFF00,  # Generic vendor usage page matches
            "report_ids": [0x02, 0x04],  # Different report IDs
            "opcode_prefix": "A55A",  # Different opcode
            "packet_length": 32,  # Different length
        },
    ]

    clusters = cluster_protocol_families(devices)
    # Must NOT cluster together into a single family!
    assert len(clusters) == 2
    assert clusters[0].device_models == ["Vendor Keyboard A"]
    assert clusters[1].device_models == ["Unrelated Brand Mouse B"]


def test_family_clustering_positive_matches_compatible_devices() -> None:
    devices = [
        {
            "model": "Vendor Keyboard Pro 75",
            "usage_page": 0xFF00,
            "report_ids": [0x08, 0x09],
            "opcode_prefix": "1300",
            "packet_length": 64,
        },
        {
            "model": "Vendor Keyboard Max 87",
            "usage_page": 0xFF00,
            "report_ids": [0x08, 0x09],
            "opcode_prefix": "1300",
            "packet_length": 64,
        },
    ]

    clusters = cluster_protocol_families(devices)
    assert len(clusters) == 1
    assert "Vendor Keyboard Pro 75" in clusters[0].device_models
    assert "Vendor Keyboard Max 87" in clusters[0].device_models
