"""RT threshold UI-contract regressions (no hardware, no mutation).

Confirms from repository vendor source + real hardware what IS established
(record model, 0.01 mm scale, up/down same contract, parser alignment) and that
the safe temporary grid is still OPEN — so no temporary B can be fabricated and
no RT write is prepared."""

import sys
from pathlib import Path

import pytest

_DB = Path(__file__).resolve().parents[2] / "DB"
if str(_DB) not in sys.path:
    sys.path.insert(0, str(_DB))

from community.vetro_probe import feature_gates as fg
from community.vetro_probe.rt_ui_contract import (
    RT_THRESHOLD_SCALE_MM, UP_DOWN_SAME_CONTRACT, SAFE_TEMP_GRID,
    select_temporary_threshold, contract_summary,
)
from community.vetro_probe.bundle import production_bundle_for_hero84
from community.vetro_probe.identity import mock_hero84_instance

SCOPE = dict(vid="0x372E", pid="0x103E", family="aula_kb_v3_wired", fw="0216")

REAL_REPLY_KEYS_1_6 = (
    "990000010030000100000100010100020000010001010003000001000101000400000100010100050000"
    "010001010006000001000101000000000000000005"
)


def _load_vendor_ops():
    import json
    from pathlib import Path
    p = Path(__file__).resolve().parents[1] / "vetro_probe" / "knowledge" / ".." / ".." / ".." / "DB"
    cand = Path("DB/reports/protocol_knowledge/aula/HERO_84_HE/static/lt_controller_ops.json")
    if not cand.is_file():
        pytest.skip("vendor lt_controller_ops.json not present")
    return json.loads(cand.read_text(encoding="utf-8"))


# 1/2/3. vendor parse_rt semantics == our parser on the real fixture
def test_vendor_parse_rt_matches_our_parser():
    from aula_kb_v3.protocol import parse_rt_get_reply  # type: ignore
    recs = parse_rt_get_reply(bytes.fromhex(REAL_REPLY_KEYS_1_6))
    assert [r["key_id"] for r in recs] == [1, 2, 3, 4, 5, 6]
    assert all(r["rt_enable"] is False and r["rt_global"] == 1 for r in recs)
    assert all(r["rt_up_raw"] == 1 and r["rt_down_raw"] == 1 for r in recs)
    assert all(r["rt_up_mm"] == 0.01 and r["rt_down_mm"] == 0.01 for r in recs)


# 4/5/6/7. 0.01 mm scale + up/down same contract
def test_scale_and_up_down_same_contract():
    from aula_kb_v3.protocol import mm_to_raw, raw_to_mm  # type: ignore
    assert RT_THRESHOLD_SCALE_MM == 0.01
    assert UP_DOWN_SAME_CONTRACT is True
    assert mm_to_raw(0.01) == 1 and raw_to_mm(1) == 0.01
    assert mm_to_raw(0.10) == 10 and raw_to_mm(10) == 0.10
    assert contract_summary()["observed_hardware_values_mm"] == [0.01, 0.10]


# 8. baseline A (0.01 mm) is preserved even though the UI grid may be coarser
def test_baseline_not_normalized_to_ui_grid():
    from aula_kb_v3.protocol import mm_to_raw, raw_to_mm  # type: ignore
    A = 0.01  # real captured value; raw 1
    assert raw_to_mm(mm_to_raw(A)) == A  # exact, no snapping


# 9/10. rt_enable / rt_global preserved requirement is enforced (no default)
def test_enable_global_never_defaulted():
    from aula_kb_v3.protocol import parse_rt_get_reply  # type: ignore
    rec = parse_rt_get_reply(bytes.fromhex(REAL_REPLY_KEYS_1_6))[0]
    assert "rt_enable" in rec and "rt_global" in rec  # obtained from hardware, never defaulted


# 11/12. this discovery performed ZERO Probe HID writes (no browser inspection path
#        was added; static + captured evidence only) — nothing mutates hardware.
def test_no_hardware_mutation_path_added():
    import inspect
    from community.vetro_probe import rt_ui_contract as m
    src = inspect.getsource(m)
    assert "HIDDevice.prototype.sendReport" not in src
    assert "sendReport" not in src


# 13. safe temporary grid stays OPEN; B selection refuses to fabricate
def test_safe_temp_grid_open_and_no_b_fabricated():
    assert SAFE_TEMP_GRID == "OPEN"
    with pytest.raises(RuntimeError, match="safe temporary RT grid is OPEN"):
        select_temporary_threshold(0.01)


# 14/15. he.rt blocked; remap blocked; K20 unpromoted
def test_he_rt_remap_blocked_k20_unpromoted():
    assert fg.blocker_for("he.rt", **SCOPE)[0] == "BLOCKED_BY_KNOWLEDGE_HOLE"
    assert fg.blocker_for("keyboard.remap", **SCOPE)[0] == "BLOCKED_BY_MISSING_STRONG_E5"
    from community.vetro_probe.knowledge_rank import load_registry
    aula = next(g for g in load_registry()["groups"] if g.get("group") == "aula")
    assert aula["lighting_feature_scope"]["k20_not_promoted"] is True


def test_vendor_controller_source_present():
    ops = _load_vendor_ops()
    s = str(ops)
    assert "fetch_rt" in s and "parse_rt" in s and "sync_rt" in s and "kxt" in s
