//! Fingerprint regression, on a runner with nothing plugged into it.
//!
//! The baseline topology is parsed from `docs/hardware/aula-hero-84-he.json`,
//! the TICKET-08 capture, rather than transcribed into this file. Transcription
//! is how a regression test quietly stops agreeing with the hardware and starts
//! agreeing with the code — and the variations below are then spelled as "the
//! real thing, except this", which is a much smaller thing to have to trust.
//!
//! What these tests are guarding is not "the matcher returns the right answer".
//! It is the two conclusions this project is most likely to reach by accident:
//!
//! - **a product identified from structure alone.** This project owns hardware
//!   that makes it concrete: a VXE receiver and the mouse behind it are
//!   byte-identical in every collection, report id and descriptor hash, and are
//!   two different products (TICKET-22).
//! - **a protocol family concluded from VID:PID.** Nine AULA models share
//!   `372E:103E`. The vendor's own software cannot tell them apart from it
//!   either — it asks the device.

#![allow(clippy::expect_used, clippy::panic, clippy::unwrap_used)]

use pcaps::Confidence;
use pemu::topology::{Collection, Topology};
use pproto::aula_bytech_engine::AulaBytechEngine;
use pregistry::{
    CollectionObservation, DeviceObservation, FamilyReason, Identification, ProtocolEvidence,
    ProtocolEvidenceSource, Registry,
};
use serde_json::Value;

const AULA_CAPTURE: &str = include_str!("../../../docs/hardware/aula-hero-84-he.json");

// --- the capture, parsed rather than transcribed ----------------------------

fn hex16(value: &Value) -> u16 {
    let text = value.as_str().expect("hex string");
    u16::from_str_radix(text.trim_start_matches("0x").trim_start_matches("0X"), 16)
        .expect("hex digits")
}

fn hex64(value: &Value) -> u64 {
    u64::from_str_radix(value.as_str().expect("hex string"), 16).expect("hex digits")
}

/// The AULA HERO 84 HE topology, read out of the capture file.
fn captured_aula() -> Topology {
    let doc: Value = serde_json::from_str(AULA_CAPTURE).expect("the capture parses");
    let collections = doc["collections"].as_array().expect("collections");
    let first = &collections[0];

    let mut interfaces: Vec<u64> = collections
        .iter()
        .map(|c| c["interface_number"].as_u64().expect("interface"))
        .collect();
    interfaces.sort_unstable();
    interfaces.dedup();

    let observed = collections
        .iter()
        .map(|c| {
            let report = c["reports"].as_array().and_then(|r| r.first());
            let bytes = |name: &str| -> u16 {
                report
                    .and_then(|r| r[name].as_u64())
                    .map(|bits| u16::try_from(bits / 8).expect("report fits"))
                    .unwrap_or(0)
            };
            let numbered = report
                .and_then(|r| r["numbered"].as_bool())
                .unwrap_or(false);
            Collection {
                interface: u8::try_from(c["interface_number"].as_u64().expect("interface"))
                    .expect("few interfaces"),
                usage_page: hex16(&c["usage_page"]),
                usage: hex16(&c["usage"]),
                descriptor_fnv1a64: hex64(&c["report_descriptor_fnv1a64"]),
                report_id: numbered
                    .then(|| {
                        u8::try_from(report.and_then(|r| r["report_id"].as_u64()).unwrap_or(0))
                    })
                    .transpose()
                    .expect("a report id is a byte"),
                input_bytes: bytes("input_bits"),
                output_bytes: bytes("output_bits"),
                feature_bytes: bytes("feature_bits"),
            }
        })
        .collect();

    Topology {
        vendor_id: hex16(&doc["vendor_id"]),
        product_id: hex16(&doc["product_id"]),
        manufacturer: first["manufacturer"].as_str().map(str::to_string),
        product: first["product"].as_str().map(str::to_string),
        release: hex16(&first["release_number"]),
        serial_present: !first["serial_number"].is_null(),
        interfaces: u8::try_from(interfaces.len()).expect("few interfaces"),
        collections: observed,
    }
}

/// The bridge from a fake device's shape to what the matcher is allowed to know.
fn observe(topology: &Topology) -> DeviceObservation {
    DeviceObservation::from_enumeration(
        topology.vendor_id,
        topology.product_id,
        topology.manufacturer.clone(),
        topology.product.clone(),
        topology.release,
        topology.serial_present,
        topology.interfaces,
        topology
            .collections
            .iter()
            .map(|c| CollectionObservation {
                interface: c.interface,
                usage_page: c.usage_page,
                usage: c.usage,
                descriptor_fnv1a64: c.descriptor_fnv1a64,
                report_id: c.report_id,
                input_bytes: c.input_bytes,
                output_bytes: c.output_bytes,
                feature_bytes: c.feature_bytes,
            })
            .collect(),
    )
}

fn identify(topology: &Topology) -> Identification {
    Registry::builtin().identify(&observe(topology))
}

// --- the fixture agrees with the hardware -----------------------------------

#[test]
fn the_captured_topology_is_the_board_we_own() {
    let aula = captured_aula();
    assert_eq!((aula.vendor_id, aula.product_id), (0x372E, 0x103E));
    assert_eq!(aula.product.as_deref(), Some("HERO 84 HE"));
    assert_eq!(aula.manufacturer.as_deref(), Some("BY Tech"));
    assert_eq!(aula.collections.len(), 7, "seven top-level collections");
    assert_eq!(aula.interfaces, 3);
    assert_eq!(
        aula.collections
            .iter()
            .filter(|c| c.is_vendor_defined())
            .count(),
        2,
        "two vendor collections, only one of which is the config channel"
    );
}

#[test]
fn the_config_collection_is_the_one_the_engine_asks_for() {
    // The engine names a usage pair and nothing else. This asserts that the rule
    // the vendor's driver uses -- the vendor collection carrying both input and
    // output reports -- picks that same collection out of the real capture.
    let aula = captured_aula();
    let config = aula
        .config_collection()
        .expect("exactly one bidirectional vendor collection");
    let endpoint = AulaBytechEngine::config_endpoint();
    assert_eq!(config.usage_page, endpoint.usage_page);
    assert_eq!(config.usage, endpoint.usage);
    assert_eq!(config.report_id, Some(9), "derived, never hardcoded");
}

#[test]
fn the_other_vendor_collection_is_not_a_candidate() {
    // 0xFF00:0x0001 carries feature reports only. Nothing may be written to it,
    // and the reason it is excluded is structural rather than a special case.
    let aula = captured_aula();
    let feature_only = aula
        .collections
        .iter()
        .find(|c| c.usage_page == 0xFF00)
        .expect("the other vendor collection");
    assert_eq!(feature_only.output_bytes, 0);
    assert!(!feature_only.is_bidirectional_vendor_channel());
}

// --- the fixtures that are supposed to fail ---------------------------------

#[test]
fn a_board_with_no_config_collection_yields_nothing_rather_than_a_guess() {
    let stripped = captured_aula().without_config_collection();
    assert_eq!(stripped.config_candidate_count(), 0);
    assert!(stripped.config_collection().is_none());
}

#[test]
fn a_board_with_two_plausible_channels_is_ambiguous_and_says_so() {
    // The rule for picking a config collection has only ever been exercised
    // against hardware where it happens to be unambiguous. On a board where it
    // is not, "no answer" is the correct answer: two plausible channels means
    // the rule does not decide this board, and picking the first would put a
    // frame on an endpoint nobody has established anything about.
    let ambiguous = captured_aula().with_duplicate_config_collection();
    assert_eq!(ambiguous.config_candidate_count(), 2);
    assert!(
        ambiguous.config_collection().is_none(),
        "an ambiguous board must not be resolved by ordering"
    );
}

// --- what must never be inferred --------------------------------------------

#[test]
fn a_structural_match_alone_does_not_name_a_product() {
    // The receiver-and-mouse finding, applied to a keyboard: the same shape
    // under a product id and name nobody has heard of. The structure still
    // matches -- that is the honest part -- and the product axis must not
    // inherit that confidence.
    let impostor = captured_aula().as_unknown_product(0x9999, "NOT A HERO 84");
    let identified = identify(&impostor);

    assert_eq!(
        identified.structural.confidence,
        Confidence::Verified,
        "the shape genuinely is our board's shape"
    );
    assert!(
        !identified.structural.matches.is_empty(),
        "and the registry recognises that shape"
    );
    assert!(
        identified.product.entry.is_none() || identified.product.confidence < Confidence::Verified,
        "but that is not permission to name the product: {:?}",
        identified.product
    );
}

#[test]
fn a_shared_vid_pid_does_not_establish_a_family() {
    // Nine models share 372E:103E. A sibling with a different shape and name is
    // still that VID:PID, and must not inherit our board's protocol family.
    let sibling = captured_aula().as_sibling_model("HERO 68 HE");
    let identified = identify(&sibling);

    assert_eq!(
        (sibling.vendor_id, sibling.product_id),
        (0x372E, 0x103E),
        "same numbers"
    );
    assert!(
        !identified.permits_write(),
        "a shared product id authorised a write"
    );
    assert_ne!(identified.family.reason, FamilyReason::FromProtocolEvidence);
}

#[test]
fn our_own_board_has_no_family_until_something_talks_to_it() {
    // The default-deny position, asserted on the real capture. Enumeration alone
    // never establishes a protocol family, however well it identifies the
    // product.
    let identified = identify(&captured_aula());
    assert_eq!(identified.family.family, None);
    assert_eq!(identified.family.confidence.value(), Confidence::Unknown);
    assert!(!identified.permits_write());
}

#[test]
fn an_exchange_establishes_the_family_that_enumeration_could_not() {
    // The other half: evidence from a verified exchange does establish it, and
    // says which command earned it.
    let evidence = AulaBytechEngine::verified_exchange_evidence();
    let identified = Registry::builtin().identify_with(&observe(&captured_aula()), Some(evidence));

    assert_eq!(identified.family.family, Some("aula-bytech"));
    assert_eq!(identified.family.reason, FamilyReason::FromProtocolEvidence);
    assert!(identified.permits_write());
    assert_eq!(evidence.command, Some("read_model_id"));
    assert_eq!(evidence.source, ProtocolEvidenceSource::VerifiedExchange);
}

#[test]
fn evidence_from_a_vendor_artifact_does_not_permit_a_write() {
    // A configurator bundle describes what a family's driver does. It is not a
    // statement about what this firmware answers, so it must not reach the bar
    // that authorises sending opcodes.
    let artifact = ProtocolEvidence {
        family: "aula-bytech",
        confidence: Confidence::High,
        source: ProtocolEvidenceSource::VendorArtifact,
        command: None,
    };
    let identified = Registry::builtin().identify_with(&observe(&captured_aula()), Some(artifact));

    assert_eq!(identified.family.family, Some("aula-bytech"));
    assert!(
        !identified.permits_write(),
        "a vendor artifact authorised a write"
    );
}

#[test]
fn a_firmware_update_that_changes_a_descriptor_weakens_the_structural_claim_only() {
    // A descriptor byte changes, the digest stops matching, and the collection
    // set still does. The structural answer should degrade rather than vanish,
    // and nothing else should move.
    let updated = captured_aula().with_changed_descriptor();
    let before = identify(&captured_aula());
    let after = identify(&updated);

    assert!(
        after.structural.confidence <= before.structural.confidence,
        "a changed descriptor cannot make the structural claim stronger"
    );
    assert_ne!(
        after.structural.id, before.structural.id,
        "the digest is supposed to notice"
    );
    assert_eq!(after.family.confidence.value(), Confidence::Unknown);
}

#[test]
fn every_answer_names_the_signals_it_used() {
    // Not a matcher property so much as a product one: the Journal screen has to
    // be able to say why a control is enabled, and it can only do that if the
    // identification carries its reasons.
    let identified = identify(&captured_aula());
    let signals: Vec<_> = identified.signals().collect();
    assert!(
        !signals.is_empty(),
        "an identification with no stated reasons is not reviewable"
    );
}
