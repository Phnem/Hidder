//! Three answers, computed separately, never averaged.

use crate::identity::{
    Axis, Confidence, DeviceEntry, FamilyClaim, Signal, SignalOutcome, StructuralId,
    structural_digest,
};
use crate::observation::DeviceObservation;

/// What the endpoint's shape turned out to be.
///
/// `id` is always present: it is computed from the observation, so an endpoint
/// nobody has ever seen still has a structural identity. `matches` is what the
/// registry recognised, which is a different question and often empty.
#[derive(Clone, PartialEq, Eq, Debug)]
pub struct StructuralOutcome {
    pub id: StructuralId,
    pub confidence: Confidence,
    pub matches: Vec<&'static str>,
    pub signals: Vec<SignalOutcome>,
}

#[derive(Clone, PartialEq, Eq, Debug)]
pub struct ProductOutcome {
    pub entry: Option<&'static str>,
    pub confidence: Confidence,
    pub signals: Vec<SignalOutcome>,
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum FamilyReason {
    /// No product matched, so there is nothing to look a family up against. A
    /// structural match does not substitute: knowing the shape of an endpoint
    /// says nothing about which opcode vocabulary the firmware behind it uses.
    NoProductMatch,
    /// The product is known and no family has been established for it. The
    /// ordinary state for every device in this registry today.
    NotRecorded,
    FromRegistry,
}

#[derive(Clone, PartialEq, Eq, Debug)]
pub struct FamilyOutcome {
    pub family: Option<&'static str>,
    pub confidence: Confidence,
    pub reason: FamilyReason,
    pub signals: Vec<SignalOutcome>,
}

/// One answer per axis.
#[derive(Clone, PartialEq, Eq, Debug)]
pub struct Identification {
    pub structural: StructuralOutcome,
    pub product: ProductOutcome,
    pub family: FamilyOutcome,
}

impl Identification {
    /// Whether a write may be attempted, which is a question only the
    /// protocol-family axis can answer.
    ///
    /// Deliberately not derived from the other two. Being certain which product
    /// is plugged in is not permission to send it opcodes.
    pub fn permits_write(&self) -> bool {
        self.family.confidence.permits_write()
    }

    /// Every signal that was looked at, on every axis, with what it did.
    pub fn signals(&self) -> impl Iterator<Item = (Axis, SignalOutcome)> + '_ {
        self.structural
            .signals
            .iter()
            .map(|s| (Axis::Structural, *s))
            .chain(self.product.signals.iter().map(|s| (Axis::Product, *s)))
            .chain(
                self.family
                    .signals
                    .iter()
                    .map(|s| (Axis::ProtocolFamily, *s)),
            )
    }
}

/// The device registry.
pub struct Registry {
    entries: &'static [DeviceEntry],
}

impl Registry {
    /// The registry generated from `data/devices/`.
    pub fn builtin() -> Self {
        Self {
            entries: crate::generated::ENTRIES,
        }
    }

    pub fn from_entries(entries: &'static [DeviceEntry]) -> Self {
        Self { entries }
    }

    pub fn entries(&self) -> &'static [DeviceEntry] {
        self.entries
    }

    pub fn entry(&self, id: &str) -> Option<&'static DeviceEntry> {
        self.entries.iter().find(|entry| entry.id == id)
    }

    pub fn identify(&self, observation: &DeviceObservation) -> Identification {
        let structural = self.match_structure(observation);
        let product = self.match_product(observation);
        let family = self.match_family(&product);
        Identification {
            structural,
            product,
            family,
        }
    }

    fn match_structure(&self, observation: &DeviceObservation) -> StructuralOutcome {
        let digest = observation.structural_digest();
        let exact: Vec<&'static str> = self
            .entries
            .iter()
            .filter(|entry| structural_digest(entry.interfaces, entry.structure) == digest)
            .map(|entry| entry.id)
            .collect();

        if !exact.is_empty() {
            return StructuralOutcome {
                id: StructuralId(digest),
                // Every structure in this registry was captured from hardware
                // in this project's hands, so an exact digest match is as good
                // as structural evidence gets.
                confidence: Confidence::Verified,
                matches: exact,
                signals: vec![
                    SignalOutcome::matched(Signal::InterfaceCount),
                    SignalOutcome::matched(Signal::CollectionSet),
                    SignalOutcome::matched(Signal::DescriptorDigest),
                    SignalOutcome::matched(Signal::ReportProfile),
                ],
            };
        }

        // Same collections, different descriptor bytes: what a firmware update
        // looks like. Worth reporting as a candidate rather than discarding,
        // and emphatically not worth calling a match.
        let observed_set = observation.collection_set();
        let near: Vec<&'static str> = self
            .entries
            .iter()
            .filter(|entry| {
                entry.interfaces == observation.interfaces
                    && entry_collection_set(entry) == observed_set
            })
            .map(|entry| entry.id)
            .collect();

        if !near.is_empty() {
            return StructuralOutcome {
                id: StructuralId(digest),
                confidence: Confidence::Candidate,
                matches: near,
                signals: vec![
                    SignalOutcome::matched(Signal::InterfaceCount),
                    SignalOutcome::matched(Signal::CollectionSet),
                    SignalOutcome::differed(Signal::DescriptorDigest),
                    SignalOutcome::differed(Signal::ReportProfile),
                ],
            };
        }

        StructuralOutcome {
            id: StructuralId(digest),
            confidence: Confidence::Unknown,
            matches: Vec::new(),
            signals: vec![
                SignalOutcome::differed(Signal::CollectionSet),
                SignalOutcome::differed(Signal::DescriptorDigest),
            ],
        }
    }

    fn match_product(&self, observation: &DeviceObservation) -> ProductOutcome {
        let mut best: Option<(&'static DeviceEntry, Confidence, Vec<SignalOutcome>)> = None;

        for entry in self.entries {
            let product = &entry.product;
            if product.vendor_id != observation.vendor_id
                || product.product_id != observation.product_id
            {
                continue;
            }

            let mut signals = vec![
                SignalOutcome::matched(Signal::VendorId),
                SignalOutcome::matched(Signal::ProductId),
            ];
            let mut all_agree = true;

            for (signal, expected, actual) in [
                (
                    Signal::ManufacturerString,
                    product.manufacturer,
                    observation.manufacturer.as_deref(),
                ),
                (
                    Signal::ProductString,
                    product.product,
                    observation.product.as_deref(),
                ),
            ] {
                match actual {
                    Some(actual) if actual == expected => {
                        signals.push(SignalOutcome::matched(signal));
                    }
                    Some(_) => {
                        signals.push(SignalOutcome::differed(signal));
                        all_agree = false;
                    }
                    None => {
                        signals.push(SignalOutcome::absent(signal));
                        all_agree = false;
                    }
                }
            }

            if product.release == observation.release {
                signals.push(SignalOutcome::matched(Signal::Release));
            } else {
                signals.push(SignalOutcome::differed(Signal::Release));
                all_agree = false;
            }

            // A vendor and product id pair on its own is an index, not an
            // answer: vendors reuse product ids across genuinely different
            // devices (spec.md § Domain rules). It gets us to a candidate and
            // no further.
            let confidence = if all_agree {
                Confidence::Verified
            } else {
                Confidence::Candidate
            };

            let better = match &best {
                Some((_, current, _)) => confidence > *current,
                None => true,
            };
            if better {
                best = Some((entry, confidence, signals));
            }
        }

        match best {
            Some((entry, confidence, signals)) => ProductOutcome {
                entry: Some(entry.id),
                confidence,
                signals,
            },
            None => ProductOutcome {
                entry: None,
                confidence: Confidence::Unknown,
                signals: vec![
                    SignalOutcome::differed(Signal::VendorId),
                    SignalOutcome::differed(Signal::ProductId),
                ],
            },
        }
    }

    /// The family is looked up through the product and through nothing else.
    ///
    /// This is the rule that keeps a structural match from becoming permission
    /// to write. Two products can share a structure exactly -- ours do -- and
    /// there is no argument from "the shape is familiar" to "the opcodes are
    /// the ones I think they are".
    fn match_family(&self, product: &ProductOutcome) -> FamilyOutcome {
        let Some(entry_id) = product.entry else {
            return FamilyOutcome {
                family: None,
                confidence: Confidence::Unknown,
                reason: FamilyReason::NoProductMatch,
                signals: vec![SignalOutcome::absent(Signal::RegistryClaim)],
            };
        };

        let claim: Option<FamilyClaim> = self.entry(entry_id).and_then(|entry| entry.family);

        match claim {
            Some(claim) => FamilyOutcome {
                family: Some(claim.family),
                // Never more sure of the family than of which product this is:
                // a family is a fact about a product, and an uncertain product
                // makes the family uncertain no matter how good the claim is.
                confidence: claim.evidence.min(product.confidence),
                reason: FamilyReason::FromRegistry,
                signals: vec![SignalOutcome::matched(Signal::RegistryClaim)],
            },
            None => FamilyOutcome {
                family: None,
                confidence: Confidence::Unknown,
                reason: FamilyReason::NotRecorded,
                signals: vec![SignalOutcome::absent(Signal::RegistryClaim)],
            },
        }
    }
}

fn entry_collection_set(entry: &DeviceEntry) -> Vec<(u16, u16)> {
    let mut set: Vec<(u16, u16)> = entry
        .structure
        .iter()
        .map(|c| (c.usage_page, c.usage))
        .collect();
    set.sort_unstable();
    set.dedup();
    set
}

#[cfg(test)]
mod tests {
    #![allow(clippy::expect_used, clippy::panic, clippy::unwrap_used)]

    use super::*;
    use crate::identity::{DeviceKind, ProductIdentity, StructuralCollection};
    use crate::observation::CollectionObservation;

    // --- fixtures ---------------------------------------------------------

    const SHARED_STRUCTURE: &[StructuralCollection] = &[
        StructuralCollection {
            interface: 0,
            usage_page: 0x0001,
            usage: 0x0006,
            descriptor_fnv1a64: 0xd681_df95_aa1e_da7e,
            report_id: None,
            input_bytes: 8,
            output_bytes: 1,
            feature_bytes: 0,
        },
        StructuralCollection {
            interface: 1,
            usage_page: 0xFF02,
            usage: 0x0002,
            descriptor_fnv1a64: 0xc2bb_04bd_91f1_d9b6,
            report_id: Some(8),
            input_bytes: 17,
            output_bytes: 17,
            feature_bytes: 0,
        },
    ];

    const OTHER_STRUCTURE: &[StructuralCollection] = &[StructuralCollection {
        interface: 0,
        usage_page: 0x0001,
        usage: 0x0006,
        descriptor_fnv1a64: 0x109a_9237_0000_0000,
        report_id: None,
        input_bytes: 8,
        output_bytes: 1,
        feature_bytes: 0,
    }];

    const TWINS: &[DeviceEntry] = &[
        DeviceEntry {
            id: "twin-wired",
            name: "Twin (wired)",
            kind: DeviceKind::Mouse,
            interfaces: 3,
            product: ProductIdentity {
                vendor_id: 0x3554,
                product_id: 0xF58F,
                manufacturer: "Compx",
                product: "VXE R1SE+",
                release: 0x0315,
            },
            structure: SHARED_STRUCTURE,
            family: None,
        },
        DeviceEntry {
            id: "twin-receiver",
            name: "Twin (receiver)",
            kind: DeviceKind::Receiver,
            interfaces: 3,
            product: ProductIdentity {
                vendor_id: 0x3554,
                product_id: 0xF58E,
                manufacturer: "Compx",
                product: "VXE Mouse 1K Dongle",
                release: 0x0110,
            },
            structure: SHARED_STRUCTURE,
            family: None,
        },
    ];

    fn observe(
        vendor_id: u16,
        product_id: u16,
        manufacturer: &str,
        product: &str,
        release: u16,
        structure: &[StructuralCollection],
    ) -> DeviceObservation {
        DeviceObservation::from_enumeration(
            vendor_id,
            product_id,
            Some(manufacturer.to_owned()),
            Some(product.to_owned()),
            release,
            true,
            3,
            structure
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

    fn wired() -> DeviceObservation {
        observe(
            0x3554,
            0xF58F,
            "Compx",
            "VXE R1SE+",
            0x0315,
            SHARED_STRUCTURE,
        )
    }

    fn receiver() -> DeviceObservation {
        observe(
            0x3554,
            0xF58E,
            "Compx",
            "VXE Mouse 1K Dongle",
            0x0110,
            SHARED_STRUCTURE,
        )
    }

    // --- the regression this whole design exists for ----------------------

    #[test]
    fn identical_structure_does_not_make_two_products_one_device() {
        let registry = Registry::from_entries(TWINS);
        let wired = registry.identify(&wired());
        let receiver = registry.identify(&receiver());

        assert_eq!(
            wired.structural.id, receiver.structural.id,
            "the structures really are identical and the matcher must say so"
        );
        assert_eq!(wired.product.entry, Some("twin-wired"));
        assert_eq!(receiver.product.entry, Some("twin-receiver"));
        assert_ne!(
            wired.product.entry, receiver.product.entry,
            "a receiver and the mouse behind it were reported as the same product"
        );
        assert_eq!(wired.product.confidence, Confidence::Verified);
        assert_eq!(receiver.product.confidence, Confidence::Verified);
    }

    #[test]
    fn a_shared_structure_names_everything_that_shares_it() {
        let registry = Registry::from_entries(TWINS);
        let identified = registry.identify(&wired());
        assert_eq!(
            identified.structural.matches,
            vec!["twin-wired", "twin-receiver"],
            "hiding the ambiguity would be worse than reporting it"
        );
        assert_eq!(identified.structural.confidence, Confidence::Verified);
    }

    #[test]
    fn a_structural_match_cannot_raise_product_confidence() {
        // The exact failure mode of a single ranking: this endpoint has a
        // structure the registry knows byte for byte, and belongs to a product
        // it has never heard of.
        let registry = Registry::from_entries(TWINS);
        let stranger = observe(
            0x0000,
            0x0000,
            "Nobody",
            "Unheard of",
            0x0001,
            SHARED_STRUCTURE,
        );
        let identified = registry.identify(&stranger);

        assert_eq!(identified.structural.confidence, Confidence::Verified);
        assert_eq!(
            identified.product.confidence,
            Confidence::Unknown,
            "structure leaked into product identity"
        );
        assert_eq!(identified.product.entry, None);
    }

    // --- the domain rule about product ids --------------------------------

    #[test]
    fn the_same_ids_with_a_different_structure_are_a_different_structure() {
        let registry = Registry::from_entries(TWINS);
        let reflashed = observe(
            0x3554,
            0xF58F,
            "Compx",
            "VXE R1SE+",
            0x0315,
            OTHER_STRUCTURE,
        );
        let identified = registry.identify(&reflashed);
        assert_eq!(identified.structural.confidence, Confidence::Unknown);
        assert_ne!(
            identified.structural.id,
            registry.identify(&wired()).structural.id
        );
    }

    #[test]
    fn matching_ids_with_a_different_product_string_is_only_a_candidate() {
        // Vendors reuse product ids. Two matching numbers and a string that
        // disagrees is the shape of that hazard, and it must not read as
        // certainty.
        let registry = Registry::from_entries(TWINS);
        let impostor = observe(
            0x3554,
            0xF58F,
            "Compx",
            "Something else entirely",
            0x0315,
            SHARED_STRUCTURE,
        );
        let identified = registry.identify(&impostor);
        assert_eq!(identified.product.confidence, Confidence::Candidate);
        assert!(
            identified
                .product
                .signals
                .contains(&SignalOutcome::differed(Signal::ProductString))
        );
    }

    #[test]
    fn a_missing_string_is_absent_rather_than_different() {
        let registry = Registry::from_entries(TWINS);
        let mut quiet = wired();
        quiet.product = None;
        let identified = registry.identify(&quiet);
        assert!(
            identified
                .product
                .signals
                .contains(&SignalOutcome::absent(Signal::ProductString))
        );
        assert_eq!(identified.product.confidence, Confidence::Candidate);
    }

    // --- the family axis, and what may not reach it -----------------------

    #[test]
    fn a_family_is_never_reached_through_structure_alone() {
        const WITH_FAMILY: &[DeviceEntry] = &[DeviceEntry {
            id: "known-family",
            name: "Known family",
            kind: DeviceKind::Mouse,
            interfaces: 3,
            product: ProductIdentity {
                vendor_id: 0x3554,
                product_id: 0xF58F,
                manufacturer: "Compx",
                product: "VXE R1SE+",
                release: 0x0315,
            },
            structure: SHARED_STRUCTURE,
            family: Some(FamilyClaim {
                family: "some-family",
                evidence: Confidence::Verified,
            }),
        }];

        let registry = Registry::from_entries(WITH_FAMILY);

        // Same structure, different product: the family must not come along.
        let stranger = observe(0x0000, 0x0000, "Nobody", "Unheard of", 1, SHARED_STRUCTURE);
        let identified = registry.identify(&stranger);
        assert_eq!(identified.structural.confidence, Confidence::Verified);
        assert_eq!(identified.family.family, None);
        assert_eq!(identified.family.reason, FamilyReason::NoProductMatch);
        assert!(!identified.permits_write());
    }

    #[test]
    fn family_confidence_cannot_exceed_product_confidence() {
        const WITH_FAMILY: &[DeviceEntry] = &[DeviceEntry {
            id: "known-family",
            name: "Known family",
            kind: DeviceKind::Mouse,
            interfaces: 3,
            product: ProductIdentity {
                vendor_id: 0x3554,
                product_id: 0xF58F,
                manufacturer: "Compx",
                product: "VXE R1SE+",
                release: 0x0315,
            },
            structure: SHARED_STRUCTURE,
            family: Some(FamilyClaim {
                family: "some-family",
                evidence: Confidence::Verified,
            }),
        }];

        let registry = Registry::from_entries(WITH_FAMILY);
        let uncertain = observe(
            0x3554,
            0xF58F,
            "Compx",
            "Not the same string",
            0x0315,
            SHARED_STRUCTURE,
        );
        let identified = registry.identify(&uncertain);
        assert_eq!(identified.product.confidence, Confidence::Candidate);
        assert_eq!(
            identified.family.confidence,
            Confidence::Candidate,
            "a certain family claim about an uncertain product is not certainty"
        );
        assert!(!identified.permits_write());
    }

    #[test]
    fn an_unrecorded_family_says_so_rather_than_guessing() {
        let registry = Registry::from_entries(TWINS);
        let identified = registry.identify(&wired());
        assert_eq!(identified.product.confidence, Confidence::Verified);
        assert_eq!(identified.family.family, None);
        assert_eq!(identified.family.reason, FamilyReason::NotRecorded);
        assert_eq!(identified.family.confidence, Confidence::Unknown);
        assert!(
            !identified.permits_write(),
            "knowing the product is not permission to write"
        );
    }

    // --- explanation, not a boolean ---------------------------------------

    #[test]
    fn an_answer_explains_which_signals_did_what() {
        let registry = Registry::from_entries(TWINS);
        let identified = registry.identify(&wired());
        let signals: Vec<_> = identified.signals().collect();
        assert!(signals.len() >= 8, "too little explanation: {signals:?}");
        assert!(signals.iter().any(|(axis, s)| *axis == Axis::Structural
            && s.signal == Signal::DescriptorDigest
            && s.state == crate::identity::SignalState::Matched));
        assert!(
            signals
                .iter()
                .any(|(axis, _)| *axis == Axis::ProtocolFamily)
        );
    }

    #[test]
    fn an_entirely_unknown_device_is_unknown_on_every_axis() {
        let registry = Registry::from_entries(TWINS);
        let alien = observe(0x0BAD, 0x0BAD, "Nobody", "Nothing", 0, OTHER_STRUCTURE);
        let identified = registry.identify(&alien);
        assert_eq!(identified.structural.confidence, Confidence::Unknown);
        assert_eq!(identified.product.confidence, Confidence::Unknown);
        assert_eq!(identified.family.confidence, Confidence::Unknown);
        assert!(!identified.permits_write());
    }
}
