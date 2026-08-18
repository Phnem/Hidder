//! Three answers, computed separately, never averaged.

use crate::identity::{
    Axis, Confidence, DeviceEntry, FamilyClaim, FamilyConfidence, Signal, SignalOutcome,
    StructuralId, structural_digest,
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

/// Evidence about a family that does not depend on knowing the product.
///
/// The reason this exists: an unrecognised SKU is not the same thing as an
/// unrecognised protocol. Vendors ship the same firmware under a new product id
/// routinely, and when a later ticket establishes a family by talking to the
/// device -- or from a vendor artifact tied to that exchange -- the conclusion
/// stands on its own. It is not weakened by the registry never having heard of
/// the SKU, and a family must not be unreachable just because a catalogue is
/// incomplete.
///
/// What still cannot produce one of these: a structural match. Two products with
/// byte-identical descriptors may speak different opcodes, and this project owns
/// a byte-identical pair already (TICKET-22).
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub struct ProtocolEvidence {
    pub family: &'static str,
    pub confidence: Confidence,
    pub source: ProtocolEvidenceSource,
    /// Which command established this, when one did.
    ///
    /// Present so that the evidence names its own scope. "This board speaks
    /// `aula-bytech`" is a claim earned by one command answering correctly; it
    /// is not a claim that every command in the family works, and an evidence
    /// value that could not say which command it came from would invite exactly
    /// that reading. `None` for evidence that is not from an exchange -- a
    /// vendor artifact describes a family without any command having been sent.
    pub command: Option<&'static str>,
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum ProtocolEvidenceSource {
    /// An exchange with this device established it, and the answer was verified
    /// rather than merely received. Available from TICKET-12 onwards.
    VerifiedExchange,
    /// A vendor artifact -- configurator, firmware, captured traffic -- tied to
    /// this endpoint rather than to a product name.
    VendorArtifact,
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum FamilyReason {
    /// Neither a product match nor independent protocol evidence. A structural
    /// match does not substitute: knowing the shape of an endpoint says nothing
    /// about which opcode vocabulary the firmware behind it uses.
    NoEvidence,
    /// The product is known, no family has been established for it, and no
    /// protocol evidence was offered. The ordinary state for every device in
    /// this registry today.
    NotRecorded,
    /// Looked up through the product, and capped by how sure that product is.
    FromRegistry,
    /// Established independently of which product this is.
    FromProtocolEvidence,
}

#[derive(Clone, PartialEq, Eq, Debug)]
pub struct FamilyOutcome {
    pub family: Option<&'static str>,
    /// Deliberately its own type. A write is authorised from this value and
    /// from nothing else, and a bare `Confidence` off another axis cannot be
    /// substituted for it.
    pub confidence: FamilyConfidence,
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

    /// The only confidence a write may be authorised from.
    pub fn family_confidence(&self) -> FamilyConfidence {
        self.family.confidence
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

    /// Identifies a device from enumeration alone.
    pub fn identify(&self, observation: &DeviceObservation) -> Identification {
        self.identify_with(observation, None)
    }

    /// Identifies a device, taking into account protocol evidence obtained
    /// elsewhere.
    ///
    /// The caller supplies the evidence because obtaining it means talking to
    /// the device, and this crate does not talk to devices. What this crate
    /// decides is what such evidence is worth once it exists.
    pub fn identify_with(
        &self,
        observation: &DeviceObservation,
        protocol: Option<ProtocolEvidence>,
    ) -> Identification {
        let structural = self.match_structure(observation);
        let product = self.match_product(observation);
        let family = self.match_family(&product, protocol);
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

    /// Resolves the family from the product, from independent protocol
    /// evidence, or from both -- and never from the structure.
    ///
    /// Two routes, deliberately not one:
    ///
    /// - *through the product*, capped by how sure the product is, because a
    ///   registry claim is a fact recorded about a product;
    /// - *independently*, from protocol evidence, which is not capped, because
    ///   an exchange that established a family established it about the thing on
    ///   the wire rather than about a name in a table.
    ///
    /// The second route is what keeps an unknown SKU from being a dead end: a
    /// device nobody has catalogued can still be known to speak a family, and
    /// the product axis stays honestly unknown while that happens.
    fn match_family(
        &self,
        product: &ProductOutcome,
        protocol: Option<ProtocolEvidence>,
    ) -> FamilyOutcome {
        let via_product: Option<(&'static str, Confidence)> = product
            .entry
            .and_then(|id| self.entry(id))
            .and_then(|entry| entry.family)
            .map(|claim: FamilyClaim| {
                // Never more sure of the family than of which product this is:
                // a registry claim is a fact about a product, so an uncertain
                // product makes the claim uncertain however good it is.
                (claim.family, claim.evidence.min(product.confidence))
            });
        let via_protocol = protocol.map(|evidence| (evidence.family, evidence.confidence));

        let (family, confidence, reason, signal) = match (via_product, via_protocol) {
            (Some((_, from_product)), Some((family, from_protocol)))
                if from_protocol >= from_product =>
            {
                (
                    Some(family),
                    from_protocol,
                    FamilyReason::FromProtocolEvidence,
                    SignalOutcome::matched(Signal::ProtocolEvidence),
                )
            }
            (None, Some((family, from_protocol))) => (
                Some(family),
                from_protocol,
                FamilyReason::FromProtocolEvidence,
                SignalOutcome::matched(Signal::ProtocolEvidence),
            ),
            (Some((family, from_product)), _) => (
                Some(family),
                from_product,
                FamilyReason::FromRegistry,
                SignalOutcome::matched(Signal::RegistryClaim),
            ),
            (None, None) => {
                let reason = if product.entry.is_some() {
                    FamilyReason::NotRecorded
                } else {
                    FamilyReason::NoEvidence
                };
                (
                    None,
                    Confidence::Unknown,
                    reason,
                    SignalOutcome::absent(Signal::RegistryClaim),
                )
            }
        };

        FamilyOutcome {
            family,
            confidence: FamilyConfidence::established(confidence),
            reason,
            signals: vec![signal],
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
        assert_eq!(identified.family.reason, FamilyReason::NoEvidence);
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
            identified.family.confidence.value(),
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
        assert_eq!(identified.family.confidence.value(), Confidence::Unknown);
        assert!(
            !identified.permits_write(),
            "knowing the product is not permission to write"
        );
    }

    // --- an unknown SKU is not a dead end ---------------------------------

    #[test]
    fn an_unknown_product_can_still_have_a_known_family() {
        // The correction this section exists for. A vendor ships known firmware
        // under a product id nobody has catalogued; the protocol was established
        // by talking to the thing on the wire, and that conclusion does not
        // depend on the catalogue having caught up.
        let registry = Registry::from_entries(TWINS);
        let unknown_sku = observe(
            0xBEEF,
            0xBEEF,
            "Nobody",
            "Not in any table",
            0x0001,
            OTHER_STRUCTURE,
        );
        let identified = registry.identify_with(
            &unknown_sku,
            Some(ProtocolEvidence {
                family: "some-family",
                confidence: Confidence::Verified,
                source: ProtocolEvidenceSource::VerifiedExchange,
                command: Some("some-command"),
            }),
        );

        assert_eq!(identified.family.family, Some("some-family"));
        assert_eq!(identified.family.confidence.value(), Confidence::Verified);
        assert_eq!(identified.family.reason, FamilyReason::FromProtocolEvidence);
        assert!(
            identified.permits_write(),
            "protocol evidence that was verified is what a write is for"
        );

        // And the product axis stays honest about knowing nothing.
        assert_eq!(identified.product.entry, None);
        assert_eq!(identified.product.confidence, Confidence::Unknown);
        assert_eq!(identified.structural.confidence, Confidence::Unknown);
    }

    #[test]
    fn protocol_evidence_is_not_capped_by_the_product() {
        // Unlike a registry claim, which is a fact recorded *about a product*
        // and therefore only as good as the product match.
        let registry = Registry::from_entries(TWINS);
        let uncertain_product = observe(
            0x3554,
            0xF58F,
            "Compx",
            "A string that disagrees",
            0x0315,
            SHARED_STRUCTURE,
        );
        let identified = registry.identify_with(
            &uncertain_product,
            Some(ProtocolEvidence {
                family: "some-family",
                confidence: Confidence::Verified,
                source: ProtocolEvidenceSource::VerifiedExchange,
                command: Some("some-command"),
            }),
        );
        assert_eq!(identified.product.confidence, Confidence::Candidate);
        assert_eq!(identified.family.confidence.value(), Confidence::Verified);
    }

    #[test]
    fn weak_protocol_evidence_does_not_beat_a_solid_registry_claim() {
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
                family: "registry-family",
                evidence: Confidence::Verified,
            }),
        }];

        let registry = Registry::from_entries(WITH_FAMILY);
        let identified = registry.identify_with(
            &wired(),
            Some(ProtocolEvidence {
                family: "guessed-family",
                confidence: Confidence::Candidate,
                source: ProtocolEvidenceSource::VendorArtifact,
                command: None,
            }),
        );
        assert_eq!(identified.family.family, Some("registry-family"));
        assert_eq!(identified.family.reason, FamilyReason::FromRegistry);
    }

    #[test]
    fn structure_alone_still_confirms_no_family_whatsoever() {
        // Unchanged by the new route, and the reason the new route takes
        // evidence from a caller rather than inferring anything.
        let registry = Registry::from_entries(TWINS);
        let stranger = observe(0x0000, 0x0000, "Nobody", "Unheard of", 1, SHARED_STRUCTURE);
        let identified = registry.identify(&stranger);
        assert_eq!(identified.structural.confidence, Confidence::Verified);
        assert_eq!(identified.family.family, None);
        assert_eq!(identified.family.reason, FamilyReason::NoEvidence);
        assert!(!identified.permits_write());
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
        assert_eq!(identified.family.confidence.value(), Confidence::Unknown);
        assert!(!identified.permits_write());
    }
}
