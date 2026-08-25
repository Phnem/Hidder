"""The synthetic device image must stay traceable to the vendor's own data.

The risk this guards is specific: an image that makes the configurator render is
enormously convenient, and convenience is exactly what turns "bytes we supplied"
into "what the device said" three documents later. So the tests assert the
provenance structure, not just the arithmetic — every byte is either the
vendor's shipped default or a slot the manifest names as a harness choice.
"""

from pathlib import Path

import pytest

from miner.static import mchose_cz_device_image as di

_SAMPLE = """(0,c(49777).wrap)([
{type:16,code1:0,code2:20,code:20,name:"Q",index:0,layer:0},
{type:240,code1:255,code2:1,code:255,name:"FN1",index:1,layer:0},
{type:0,code1:0,code2:0,code:0,name:"",index:2,layer:0},
{type:255,code1:255,code2:255,code:0,name:"",index:3,layer:0}])"""


@pytest.fixture()
def sample(tmp_path: Path) -> Path:
    p = tmp_path / "default-keys-sample.js"
    p.write_text(_SAMPLE, encoding="utf-8")
    return p


def test_the_wire_form_is_three_bytes_per_key_in_vendor_field_order(sample: Path):
    dk = di.parse_default_keys(sample)
    assert dk.key_count == 4
    assert dk.used_key_area_size == 12
    # `getDefaultKeyInfos` reads n.slice(3i, 3i+3) as [type, code1, code2].
    assert dk.to_wire()[0:3] == bytes((16, 0, 20))
    assert dk.to_wire()[3:6] == bytes((240, 255, 1))


def test_a_gap_in_the_shipped_indices_is_refused(tmp_path: Path):
    p = tmp_path / "gappy.js"
    p.write_text('{type:16,code1:0,code2:20,code:20,name:"Q",index:0,layer:0},'
                 '{type:16,code1:0,code2:21,code:21,name:"R",index:2,layer:0}', encoding="utf-8")
    with pytest.raises(ValueError, match="missing key indices"):
        di.parse_default_keys(p)


def test_the_transcribed_key_code_mapping_agrees_with_the_shipped_code_field(sample: Path):
    """The file states `code` and we recompute it. They must agree.

    This is the check that keeps `triple_for_code` honest: if the transcription
    of `getKeyCode` drifts from the vendor's, the shipped file catches it.
    """
    assert di.key_code(16, 0, 20) == 20
    assert di.key_code(240, 255, 1) == 255
    assert di.key_code(240, 250, 0) == 252
    assert di.key_code(16, 4, 0) == 226
    assert di.key_code(0, 0, 0) == 0


def test_the_inverse_mapping_round_trips_through_the_forward_one():
    for code in list(di.SPECIAL_CODE_TO_TRIPLE) + [224, 226, 231, 4, 41, 223]:
        triple = di.triple_for_code(code)
        assert di.key_code(*triple) == code, f"inverse for {code} does not round-trip"


def test_a_code_the_vendor_cannot_produce_is_refused_rather_than_approximated():
    with pytest.raises(ValueError, match="no vendor-stated triple"):
        di.triple_for_code(9999)


def test_a_required_code_is_patched_into_a_placeholder_and_the_choice_is_recorded(sample: Path):
    dk = di.parse_default_keys(sample)
    assert 252 not in {di.key_code(*k) for k in dk.keys}
    patched, patches = di.patch_required_codes(dk, [252])
    assert 252 in {di.key_code(*k) for k in patched.keys}
    assert len(patches) == 1
    p = patches[0]
    assert p["triple"] == [240, 250, 0]
    assert "vendor" in p["triple_provenance"].lower()
    assert "HARNESS CHOICE" in p["slot_provenance"], (
        "where the key sits is ours, not the vendor's, and the manifest has to say so"
    )
    # It must land on a placeholder, never over a real shipped key.
    assert dk.keys[p["slot_index"]][0] in (0, 255)


def test_a_code_already_present_is_not_patched_twice(sample: Path):
    dk = di.parse_default_keys(sample)
    _, patches = di.patch_required_codes(dk, [255])
    assert patches == [], "code 255 is already shipped; patching it would overwrite vendor data"


def test_the_image_serves_reads_by_offset_and_admits_when_it_has_nothing(sample: Path):
    img = di.DeviceImage()
    img.write(7, 512, b"\xaa\xbb\xcc", "test")
    assert img.read(7, 512, 3) == b"\xaa\xbb\xcc"
    assert img.read(7, 512, 5) == b"\xaa\xbb\xcc\x00\x00", "short reads pad, they do not truncate"
    assert img.read(9, 0, 4) is None, (
        "an unbacked command must report absence, not zeros; zeros would erase the "
        "difference between a deliberate value and no value"
    )


def test_the_manifest_marks_the_whole_image_as_non_evidence(tmp_path: Path, sample: Path):
    _, manifest = di.build_god60_image(sample, layer_slots=2, required_codes=[252])
    assert manifest["evidence_class"] == "synthetic_from_vendor_schema"
    assert "not_hardware_evidence" in manifest
    assert manifest["layer_slots_filled"] == 2
    assert manifest["required_code_patches"], "the patch has to reach the manifest"


def test_slots_are_laid_out_at_the_vendors_stride(sample: Path):
    img, manifest = di.build_god60_image(sample, layer_slots=3, required_codes=[])
    dk = di.parse_default_keys(sample)
    wire = dk.to_wire()
    for slot in range(3):
        base = di.TOTAL_KEY_AREA_SIZE * slot
        assert img.read(7, base, len(wire)) == wire
    assert manifest["total_key_area_size"] == 512
