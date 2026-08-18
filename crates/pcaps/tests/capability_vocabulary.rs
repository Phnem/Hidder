//! Properties of the capability vocabulary that the layers above rely on.
//!
//! These are not tests of arithmetic. Each one is a boundary that, if it moved
//! quietly, would let a wrong number reach a person looking at a keyboard.

#![allow(clippy::expect_used, clippy::panic, clippy::unwrap_used)]

use pcaps::{
    CapId, CapValue, Capability, Confidence, KeyMeasurement, Measurement, Origin, Unavailable, Unit,
};

#[test]
fn a_capability_id_has_a_stable_string_and_it_is_the_documented_one() {
    // This string reaches the IPC boundary, the journal and saved profiles.
    // Changing one is a compatibility break rather than a rename, which is why
    // it is asserted literally.
    assert_eq!(CapId::HeActuation.as_str(), "he.actuation");
    assert_eq!(CapId::HeActuation.to_string(), "he.actuation");
}

#[test]
fn every_capability_id_is_listed_by_all() {
    // `all()` is what a UI enumerates. An id missing from it is a capability
    // that exists in the type system and can never be shown.
    let listed = CapId::all();
    assert!(listed.contains(&CapId::HeActuation));
    assert_eq!(
        listed.len(),
        1,
        "a capability was added without being listed, or listed without existing"
    );
}

#[test]
fn a_measurement_renders_only_the_digits_the_device_can_distinguish() {
    // At a 0.01 mm step, two decimals are meaningful and a third is invented.
    // The vendor derives its own displayed precision the same way, from the same
    // scalar.
    let value = Measurement::new(0.51, Unit::Millimetres, 2);
    assert_eq!(value.render(), "0.51 mm");

    // A device with a coarser step must not be rendered as if it were finer.
    let coarse = Measurement::new(1.0, Unit::Millimetres, 1);
    assert_eq!(coarse.render(), "1.0 mm");
}

#[test]
fn a_measurement_always_carries_its_unit() {
    // The failure this prevents is a raw device count rendered next to the
    // letters "mm" because the conversion was assumed rather than applied.
    let rendered = Measurement::new(2.0, Unit::Millimetres, 2).render();
    assert!(rendered.ends_with(" mm"), "{rendered}");
}

#[test]
fn the_hardware_origin_names_the_command_that_earned_it() {
    // Evidence that could not say which command produced it invites being read
    // as a claim about a whole family.
    let origin = Origin::VerifiedOnHardware {
        command: "read_key_travel",
    };
    assert!(origin.is_from_hardware());
    match origin {
        Origin::VerifiedOnHardware { command } => assert_eq!(command, "read_key_travel"),
        other => panic!("wrong origin: {other:?}"),
    }
}

#[test]
fn no_origin_below_hardware_claims_to_be_from_hardware() {
    // A vendor artifact describes what a driver does, not what this firmware
    // answers. It must never present as a device fact.
    assert!(!Origin::VendorArtifact.is_from_hardware());
    assert!(!Origin::Assumed.is_from_hardware());
}

#[test]
fn unavailable_keeps_three_states_apart() {
    // "Not read yet", "no verified command" and "the hardware lacks it" are
    // three different statements. A UI that rendered them alike would make a
    // missing implementation indistinguishable from a missing feature.
    let states = [
        Unavailable::NotRead,
        Unavailable::NoVerifiedCommand,
        Unavailable::NotPresent {
            evidence: "firmware dump lists no such register".to_string(),
        },
    ];
    for (i, a) in states.iter().enumerate() {
        for (j, b) in states.iter().enumerate() {
            assert_eq!(i == j, a == b, "two distinct states compared equal");
        }
    }
}

#[test]
fn a_capability_can_explain_itself() {
    // The Journal screen has to answer "why is this enabled" without the reader
    // opening the source.
    let capability = Capability {
        id: CapId::HeActuation,
        origin: Origin::VerifiedOnHardware {
            command: "read_key_travel",
        },
        confidence: Confidence::Verified,
        value: CapValue::PerKey(vec![KeyMeasurement {
            label: "W".to_string(),
            measurement: Measurement::new(0.51, Unit::Millimetres, 2),
        }]),
        provenance: "read with command read_key_travel".to_string(),
    };
    assert!(!capability.provenance.is_empty());
    assert!(capability.provenance.contains("read_key_travel"));
}

#[test]
fn the_value_shapes_that_have_no_producer_yet_still_exist() {
    // Deliberate. Retrofitting a stream onto a scalar-only value type breaks
    // every engine at once, and the shapes are already known: DKS and SOCD are
    // compound, analog travel is a stream. This test is the record that their
    // emptiness is a decision rather than an oversight.
    let _scalar = CapValue::Scalar(Measurement::new(1.0, Unit::Millimetres, 2));
    let _compound =
        CapValue::Compound(vec![("press", Measurement::new(0.2, Unit::Millimetres, 2))]);
    let stream = CapValue::Stream {
        unit: Unit::Millimetres,
    };
    match stream {
        CapValue::Stream { unit } => assert_eq!(unit, Unit::Millimetres),
        other => panic!("wrong shape: {other:?}"),
    }
}

#[test]
fn only_a_verified_family_confidence_authorises_a_write() {
    // Restated here because it is the one rule the capability layer must not
    // quietly soften: being certain which product is plugged in says nothing
    // about which opcodes its firmware understands.
    use pcaps::FamilyConfidence;
    assert!(FamilyConfidence::established(Confidence::Verified).permits_write());
    for lower in [Confidence::Unknown, Confidence::Candidate, Confidence::High] {
        assert!(!FamilyConfidence::established(lower).permits_write());
    }
}
