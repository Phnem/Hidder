"""The production projection must be a pure function of the compiled registry.

Why this is worth a test of its own: the projection is the boundary between the
research tree and the shipped runtime. If it is not deterministic, a diff of the
generated Rust file stops meaning "the knowledge changed" and starts meaning
"somebody re-ran the generator", and at that point nobody reviews it.

The other half of the boundary is what must NOT cross it. `test_projection_carries_no_research_corpus`
checks that the output holds product facts and no vendor artifacts, no raw
opcode catalogue, and no unreviewed hypotheses -- the projection is what a
consumer is allowed to build a product surface from, and it should not be
possible to reconstruct an unreviewed command out of it.
"""
from __future__ import annotations

import json

from aula_kb_v3 import emit_peripheral_catalog as projection


def test_rust_projection_is_byte_identical_across_runs():
    rows_a = projection._product_rows()
    rows_b = projection._product_rows()
    assert projection.emit_rust(rows_a) == projection.emit_rust(rows_b)


def test_json_projection_is_byte_identical_across_runs():
    rows_a = projection._product_rows()
    rows_b = projection._product_rows()
    assert projection.emit_json(rows_a) == projection.emit_json(rows_b)


def test_the_projection_covers_every_catalogued_product():
    from aula_kb_v3 import registry_data

    rows = projection._product_rows()
    assert len(rows) == len(registry_data.PRODUCTS) == 15
    assert {row["model_id"] for row in rows} == {p.uuid for p in registry_data.PRODUCTS}


def test_only_the_physically_owned_board_carries_per_product_verification():
    rows = projection._product_rows()
    bound = [r["display_name"] for r in rows if r["validation"]["product_binding_verified"]]
    caps = [r["display_name"] for r in rows if r["validation"]["product_caps_verified"]]
    assert bound == ["HERO 84 HE"]
    assert caps == ["HERO 84 HE"]
    # The family fact is a family fact and applies to every row.
    assert all(r["validation"]["family_hardware_verified"] for r in rows)


def test_projection_carries_no_research_corpus():
    """No opcodes, no vendor internals, no hypotheses.

    The projection describes *products*. Which bytes a command is made of is a
    family fact that lives in the ACL, behind review; a consumer holding this
    document should be unable to assemble a frame from it.
    """
    document = json.loads(projection.emit_json(projection._product_rows()))
    text = json.dumps(document).lower()

    for forbidden in (
        "opcode",
        "checksum",
        "0x93",
        "0x13",
        "sync_keys",
        "fetch_keys",
        "bundle",
        ".exe",
        ".js",
        "hypothes",
        "decompil",
    ):
        assert forbidden not in text, f"the projection leaks research detail: {forbidden!r}"

    # And it does carry what a product surface actually needs.
    assert document["schema"] == "peripheral.product-catalog/1"
    assert document["bounds"]["profile_slot_count"] == 3
    assert document["products"][0]["layers"]
    assert document["products"][0]["capabilities"]


def test_the_feature_register_map_is_projected_with_its_writability():
    document = json.loads(projection.emit_json(projection._product_rows()))
    registers = document["feature_registers"]
    # Three registers are read-only in the vendor's own map, and a projection
    # that dropped the flag would present them as settable.
    assert registers["19"] == {"name": "sleep_level", "value_length": 1, "writable": False}
    assert registers["29"]["writable"] is False
    assert registers["30"]["writable"] is False
    assert registers["23"] == {"name": "polling_level", "value_length": 1, "writable": True}


def test_the_polling_map_records_which_values_were_physically_exercised():
    document = json.loads(projection.emit_json(projection._product_rows()))
    bounds = document["bounds"]
    assert bounds["polling_enum_hz"] == {
        "0": 1000,
        "1": 500,
        "2": 250,
        "3": 125,
        "4": 8000,
        "5": 4000,
        "6": 2000,
    }
    # Two of the seven. A consumer that treats the other five as proven is
    # making a claim this project has not earned.
    assert bounds["polling_physically_validated_values"] == [2, 3]
