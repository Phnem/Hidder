"""Invariants that turn two expensive mistakes into test failures.

Both were real. Driving the bootloader product id produced days of empty
captures that read as "the vendor ignores this device". And an identity graph
with a collision would mean the app cannot tell two products apart without
asking a human, which is the one thing §0.2 names explicitly -- so "zero
collisions" is asserted rather than remembered.

These read the artifacts the CZ SDK sweep produced, so they fail if the vendor
changes its tables, which is the point: the drift detector already fired once on
this vendor within a day.
"""

import json
from pathlib import Path

import pytest

from miner.dynamic.mchose_oracle import PROFILES, assert_not_boot_identity

_REPORTS = Path(__file__).parent.parent.parent / "reports" / "protocol_knowledge" / "mchose"
_GRAPH = _REPORTS / "static" / "identity_graph.json"
_CZ_TABLE = _REPORTS / "static" / "cz_identity_table.json"


def _graph() -> dict:
    if not _GRAPH.exists():
        pytest.skip("identity_graph.json not generated in this checkout")
    return json.loads(_GRAPH.read_text(encoding="utf-8"))


def _boot_ids() -> set[str]:
    return {r["id"].lower() for r in _graph()["boot_mode_identities"]}


def test_no_oracle_profile_points_at_the_bootloader():
    boot = _boot_ids()
    for name, p in PROFILES.items():
        key = f"{p['vendorId']:#06x}:{p['productId']:#06x}".lower()
        assert key not in boot, (
            f"profile {name!r} drives {key}, which the CZ SDK reports as the DFU interface; "
            "the app opens a firmware dialog instead of the configurator and captures nothing"
        )


def test_each_profile_records_its_boot_twin_and_that_twin_really_is_one():
    boot = _boot_ids()
    for name, p in PROFILES.items():
        twin = p.get("boot_mode_pid")
        assert twin is not None, f"profile {name!r} does not record its DFU product id"
        key = f"{p['vendorId']:#06x}:{twin:#06x}".lower()
        assert key in boot, (
            f"profile {name!r} claims {key} is the DFU twin, but the CZ SDK does not agree"
        )


def test_the_guard_actually_refuses_a_bootloader_profile():
    boot = sorted(_boot_ids())
    if not boot:
        pytest.skip("no boot identities recorded")
    vid, pid = (int(x, 16) for x in boot[0].split(":"))
    with pytest.raises(SystemExit):
        assert_not_boot_identity({"label": "deliberate DFU profile",
                                  "vendorId": vid, "productId": pid})


def test_the_guard_lets_a_normal_identity_through():
    assert_not_boot_identity(PROFILES["god60"])


def test_the_identity_graph_closes_without_manual_selection():
    g = _graph()
    assert g["selector_collisions"] == {}, (
        "two products answer to the same vid:pid+usage; the app would need a human to pick, "
        f"which §0.2 forbids: {g['selector_collisions']}"
    )
    assert g["vid_pid_collisions"] == {}, g["vid_pid_collisions"]
    assert g["identity_graph_closed_without_manual_selection"] is True


def test_the_sdk_fallback_string_is_never_counted_as_a_name():
    if not _CZ_TABLE.exists():
        pytest.skip("cz_identity_table.json not generated in this checkout")
    rows = json.loads(_CZ_TABLE.read_text(encoding="utf-8"))["rows"]
    for r in rows:
        if r.get("name_link") == "RESOLVED":
            assert r.get("fullName"), (
                f"{r['vendorId']:#06x}:{r['productId']:#06x} is marked RESOLVED with an empty "
                "fullName, which is the shape of the SDK's fallback rather than a name"
            )
    fallbacks = [r for r in rows if r.get("name_link") != "RESOLVED"]
    assert all(not r.get("fullName") for r in fallbacks), (
        "a row with a real fullName was filed as a fallback"
    )
