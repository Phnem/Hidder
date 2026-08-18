//! The matcher against the three endpoints this project has actually seen.
//!
//! The observations are parsed from the capture files themselves rather than
//! transcribed into this test. Transcription is how a regression test quietly
//! starts agreeing with the code instead of with the hardware.
//!
//! Nothing here sends anything. The inputs are files on disk, captured by a tool
//! that cannot write to a device.

#![allow(clippy::expect_used, clippy::panic, clippy::unwrap_used)]

use pregistry::{
    CollectionObservation, Confidence, DeviceObservation, FamilyReason, Identification, Registry,
};
use serde_json::Value;

const AULA: &str = include_str!("../../../docs/hardware/aula-hero-84-he.json");
const VXE_WIRED: &str = include_str!("../../../docs/hardware/vxe-dragonfly-r1-se-plus-wired.json");
const VXE_RECEIVER: &str =
    include_str!("../../../docs/hardware/vxe-dragonfly-r1-se-plus-24ghz.json");

fn hex16(value: &Value) -> u16 {
    let text = value.as_str().expect("hex string");
    let digits = text.trim_start_matches("0x").trim_start_matches("0X");
    u16::from_str_radix(digits, 16).expect("hex digits")
}

/// Builds an observation the way the transport would, from one capture.
fn observe(capture: &str) -> DeviceObservation {
    let doc: Value = serde_json::from_str(capture).expect("capture parses");
    let collections = doc["collections"].as_array().expect("collections");
    let first = &collections[0];

    let interfaces = {
        let mut seen: Vec<u64> = collections
            .iter()
            .map(|c| c["interface_number"].as_u64().expect("interface"))
            .collect();
        seen.sort_unstable();
        seen.dedup();
        u8::try_from(seen.len()).expect("few interfaces")
    };

    let observed = collections
        .iter()
        .map(|c| {
            let report = c["reports"].as_array().and_then(|r| r.first());
            let bits = |name: &str| -> u16 {
                report
                    .and_then(|r| r[name].as_u64())
                    .map(|b| u16::try_from(b / 8).expect("report fits"))
                    .unwrap_or(0)
            };
            let numbered = report
                .and_then(|r| r["numbered"].as_bool())
                .unwrap_or(false);
            CollectionObservation {
                interface: u8::try_from(c["interface_number"].as_u64().expect("interface"))
                    .expect("few interfaces"),
                usage_page: hex16(&c["usage_page"]),
                usage: hex16(&c["usage"]),
                descriptor_fnv1a64: u64::from_str_radix(
                    c["report_descriptor_fnv1a64"].as_str().expect("digest"),
                    16,
                )
                .expect("hex digits"),
                report_id: if numbered {
                    Some(
                        u8::try_from(report.and_then(|r| r["report_id"].as_u64()).unwrap_or(0))
                            .expect("report id is a byte"),
                    )
                } else {
                    None
                },
                input_bytes: bits("input_bits"),
                output_bytes: bits("output_bits"),
                feature_bytes: bits("feature_bits"),
            }
        })
        .collect();

    DeviceObservation::from_enumeration(
        hex16(&doc["vendor_id"]),
        hex16(&doc["product_id"]),
        first["manufacturer"].as_str().map(str::to_owned),
        first["product"].as_str().map(str::to_owned),
        hex16(&first["release_number"]),
        first["serial_number_present"].as_bool().unwrap_or(false),
        interfaces,
        observed,
    )
}

fn identify(capture: &str) -> Identification {
    Registry::builtin().identify(&observe(capture))
}

#[test]
fn the_three_captures_parse_into_observations() {
    for capture in [AULA, VXE_WIRED, VXE_RECEIVER] {
        let observation = observe(capture);
        assert_eq!(observation.interfaces, 3);
        assert!(!observation.collections.is_empty());
        assert!(
            observation.serial_present,
            "all three devices report a serial, withheld from the file"
        );
    }
}

/// The regression this ticket exists for.
#[test]
fn the_receiver_and_the_mouse_share_a_structure_and_are_different_products() {
    let wired = identify(VXE_WIRED);
    let receiver = identify(VXE_RECEIVER);

    assert_eq!(
        wired.structural.id, receiver.structural.id,
        "these two really do present byte-identical descriptors"
    );
    assert_eq!(wired.structural.confidence, Confidence::Verified);
    assert_eq!(receiver.structural.confidence, Confidence::Verified);

    assert_eq!(wired.product.entry, Some("vxe-dragonfly-r1se-plus-wired"));
    assert_eq!(
        receiver.product.entry,
        Some("vxe-dragonfly-r1se-plus-receiver")
    );
    assert_ne!(
        wired.product.entry, receiver.product.entry,
        "the receiver and the mouse behind it were identified as one product"
    );
    assert_eq!(wired.product.confidence, Confidence::Verified);
    assert_eq!(receiver.product.confidence, Confidence::Verified);
}

#[test]
fn the_shared_structure_names_both_of_them() {
    let wired = identify(VXE_WIRED);
    let mut shared = wired.structural.matches.clone();
    shared.sort_unstable();
    assert_eq!(
        shared,
        vec![
            "vxe-dragonfly-r1se-plus-receiver",
            "vxe-dragonfly-r1se-plus-wired"
        ],
        "an ambiguity the matcher hides is an ambiguity the caller cannot handle"
    );
}

#[test]
fn the_keyboard_is_distinct_from_both_mice_on_both_axes() {
    let aula = identify(AULA);
    let wired = identify(VXE_WIRED);

    assert_eq!(aula.product.entry, Some("aula-hero84-he"));
    assert_eq!(aula.product.confidence, Confidence::Verified);
    assert_eq!(aula.structural.confidence, Confidence::Verified);
    assert_ne!(aula.structural.id, wired.structural.id);
    assert_eq!(
        aula.structural.matches,
        vec!["aula-hero84-he"],
        "the keyboard's structure is its own"
    );
}

#[test]
fn no_device_we_own_has_a_known_protocol_family() {
    // The honest state, and the one that keeps the write gate shut. No exchange
    // has ever taken place with any of these three.
    for (name, capture) in [
        ("aula", AULA),
        ("vxe wired", VXE_WIRED),
        ("vxe receiver", VXE_RECEIVER),
    ] {
        let identified = identify(capture);
        assert_eq!(identified.family.family, None, "{name} claimed a family");
        assert_eq!(identified.family.confidence.value(), Confidence::Unknown);
        assert_eq!(identified.family.reason, FamilyReason::NotRecorded);
        assert!(
            !identified.permits_write(),
            "{name} would have been writable"
        );
    }
}

#[test]
fn knowing_the_product_exactly_still_does_not_permit_a_write() {
    // Stated as its own test because it is the conclusion most likely to be
    // "simplified" later: product identity is not protocol knowledge.
    let wired = identify(VXE_WIRED);
    assert_eq!(wired.product.confidence, Confidence::Verified);
    assert!(!wired.permits_write());
}

#[test]
fn every_answer_explains_itself() {
    let identified = identify(AULA);
    let signals: Vec<_> = identified.signals().collect();
    assert!(
        signals.len() >= 8,
        "an answer with no explanation is a bool with extra steps: {signals:?}"
    );
}
