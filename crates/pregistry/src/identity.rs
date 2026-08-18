//! The axes, and the vocabulary for explaining an answer on each of them.

pub use pcaps::Confidence;

/// Which question a signal answers.
///
/// The reason this type exists at all: signals were previously arranged on one
/// scale from strongest to weakest, and TICKET-22 produced hardware where that
/// scale gives a confident wrong answer. A receiver and the mouse behind it
/// present byte-identical descriptors, so the "strongest" signals cannot
/// separate them, while the three signals ranked weakest can. Neither ranking
/// was wrong; they answer different questions, and a single scale had no way to
/// say so.
#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Debug)]
pub enum Axis {
    /// What shape this HID endpoint has, and therefore what it could speak.
    Structural,
    /// Which physical product is on the other end of the wire.
    Product,
    /// Which vocabulary of opcodes it understands.
    ProtocolFamily,
}

impl Axis {
    pub fn as_str(self) -> &'static str {
        match self {
            Axis::Structural => "structural",
            Axis::Product => "product",
            Axis::ProtocolFamily => "protocol-family",
        }
    }
}

/// One observable thing the matcher can compare.
///
/// Every signal belongs to exactly one axis. A signal that would inform two
/// axes is a sign that it has not been thought through, not a convenience.
#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Debug)]
pub enum Signal {
    InterfaceCount,
    CollectionSet,
    DescriptorDigest,
    ReportProfile,
    VendorId,
    ProductId,
    ManufacturerString,
    ProductString,
    Release,
    /// A registry assertion about the family, carrying its own evidence.
    /// Deliberately not "the device answered an identify opcode": that is a
    /// probe, and no probe happens in this crate.
    RegistryClaim,
}

impl Signal {
    pub fn axis(self) -> Axis {
        match self {
            Signal::InterfaceCount
            | Signal::CollectionSet
            | Signal::DescriptorDigest
            | Signal::ReportProfile => Axis::Structural,
            Signal::VendorId
            | Signal::ProductId
            | Signal::ManufacturerString
            | Signal::ProductString
            | Signal::Release => Axis::Product,
            Signal::RegistryClaim => Axis::ProtocolFamily,
        }
    }

    pub fn as_str(self) -> &'static str {
        match self {
            Signal::InterfaceCount => "interface_count",
            Signal::CollectionSet => "collection_set",
            Signal::DescriptorDigest => "descriptor_digest",
            Signal::ReportProfile => "report_profile",
            Signal::VendorId => "vendor_id",
            Signal::ProductId => "product_id",
            Signal::ManufacturerString => "manufacturer_string",
            Signal::ProductString => "product_string",
            Signal::Release => "release",
            Signal::RegistryClaim => "registry_claim",
        }
    }
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum SignalState {
    Matched,
    /// Both sides had a value and the values disagree. Stronger information
    /// than `Absent`: a disagreement is evidence against, while a missing value
    /// is evidence about nothing.
    Differed,
    Absent,
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub struct SignalOutcome {
    pub signal: Signal,
    pub state: SignalState,
}

impl SignalOutcome {
    pub fn matched(signal: Signal) -> Self {
        Self {
            signal,
            state: SignalState::Matched,
        }
    }

    pub fn differed(signal: Signal) -> Self {
        Self {
            signal,
            state: SignalState::Differed,
        }
    }

    pub fn absent(signal: Signal) -> Self {
        Self {
            signal,
            state: SignalState::Absent,
        }
    }
}

/// The identity of an endpoint's *shape*, computed from what it presents.
///
/// Two different physical products can legitimately share one of these, and on
/// this project's hardware two of them do. That is not a defect in the digest;
/// it is the fact the digest is reporting.
#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Debug)]
pub struct StructuralId(pub u64);

impl std::fmt::Display for StructuralId {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{:016x}", self.0)
    }
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum DeviceKind {
    Keyboard,
    Mouse,
    Receiver,
    Other,
}

impl DeviceKind {
    pub fn as_str(self) -> &'static str {
        match self {
            DeviceKind::Keyboard => "keyboard",
            DeviceKind::Mouse => "mouse",
            DeviceKind::Receiver => "receiver",
            DeviceKind::Other => "other",
        }
    }
}

/// The product-identity signals a registry entry records.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub struct ProductIdentity {
    pub vendor_id: u16,
    pub product_id: u16,
    pub manufacturer: &'static str,
    pub product: &'static str,
    pub release: u16,
}

/// One top-level collection, normalised for comparison.
#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Debug)]
pub struct StructuralCollection {
    pub interface: u8,
    pub usage_page: u16,
    pub usage: u16,
    pub descriptor_fnv1a64: u64,
    pub report_id: Option<u8>,
    pub input_bytes: u16,
    pub output_bytes: u16,
    pub feature_bytes: u16,
}

/// What a registry entry claims about the protocol family, and on what evidence.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub struct FamilyClaim {
    pub family: &'static str,
    /// The confidence this claim can support on its own. Capped later by how
    /// sure we are of the product, because a family is a fact about a product.
    pub evidence: Confidence,
}

/// One device the registry knows about.
#[derive(Clone, Copy, Debug)]
pub struct DeviceEntry {
    pub id: &'static str,
    pub name: &'static str,
    pub kind: DeviceKind,
    pub interfaces: u8,
    pub product: ProductIdentity,
    pub structure: &'static [StructuralCollection],
    /// `None` means unknown, and unknown is a legitimate long-term state. It is
    /// never filled in by inference from the product or the structure.
    pub family: Option<FamilyClaim>,
}

/// FNV-1a over 64 bits. Same function the inventory tool uses for descriptors,
/// so a digest computed here is comparable with one printed there.
pub(crate) fn fnv1a64(bytes: &[u8]) -> u64 {
    let mut hash: u64 = 0xcbf2_9ce4_8422_2325;
    for byte in bytes {
        hash ^= u64::from(*byte);
        hash = hash.wrapping_mul(0x0000_0100_0000_01b3);
    }
    hash
}

/// The structural digest of a set of collections.
///
/// Order-independent by construction: the collections are sorted before
/// hashing, so two captures of the same device cannot disagree because the
/// operating system enumerated them in a different order.
pub(crate) fn structural_digest(interfaces: u8, collections: &[StructuralCollection]) -> u64 {
    let mut sorted: Vec<StructuralCollection> = collections.to_vec();
    sorted.sort();
    let mut bytes = Vec::with_capacity(1 + sorted.len() * 17);
    bytes.push(interfaces);
    for c in &sorted {
        bytes.push(c.interface);
        bytes.extend_from_slice(&c.usage_page.to_le_bytes());
        bytes.extend_from_slice(&c.usage.to_le_bytes());
        bytes.extend_from_slice(&c.descriptor_fnv1a64.to_le_bytes());
        // A missing report id is not report id zero: an unnumbered report has
        // no id byte on the wire at all, and conflating the two is an off-by-one
        // in every field that follows it.
        bytes.push(c.report_id.map_or(0xFF, |_| 0x01));
        bytes.push(c.report_id.unwrap_or(0));
        bytes.extend_from_slice(&c.input_bytes.to_le_bytes());
        bytes.extend_from_slice(&c.output_bytes.to_le_bytes());
        bytes.extend_from_slice(&c.feature_bytes.to_le_bytes());
    }
    fnv1a64(&bytes)
}

#[cfg(test)]
mod tests {
    #![allow(clippy::expect_used, clippy::panic, clippy::unwrap_used)]

    use super::*;

    fn collection(usage: u16, descriptor: u64) -> StructuralCollection {
        StructuralCollection {
            interface: 1,
            usage_page: 1,
            usage,
            descriptor_fnv1a64: descriptor,
            report_id: Some(3),
            input_bytes: 8,
            output_bytes: 0,
            feature_bytes: 0,
        }
    }

    #[test]
    fn every_signal_belongs_to_exactly_one_axis() {
        // The compiler enforces the "exactly one" half. This checks the half it
        // cannot: that nothing was quietly filed under the wrong question.
        assert_eq!(Signal::DescriptorDigest.axis(), Axis::Structural);
        assert_eq!(Signal::ProductId.axis(), Axis::Product);
        assert_eq!(Signal::ManufacturerString.axis(), Axis::Product);
        assert_eq!(Signal::RegistryClaim.axis(), Axis::ProtocolFamily);
    }

    #[test]
    fn the_digest_does_not_depend_on_enumeration_order() {
        let a = [collection(6, 111), collection(2, 222)];
        let b = [collection(2, 222), collection(6, 111)];
        assert_eq!(structural_digest(3, &a), structural_digest(3, &b));
    }

    #[test]
    fn a_different_descriptor_is_a_different_structure() {
        let a = [collection(6, 111)];
        let b = [collection(6, 999)];
        assert_ne!(structural_digest(3, &a), structural_digest(3, &b));
    }

    #[test]
    fn an_unnumbered_report_is_not_report_zero() {
        let mut numbered = collection(6, 111);
        numbered.report_id = Some(0);
        let mut unnumbered = collection(6, 111);
        unnumbered.report_id = None;
        assert_ne!(
            structural_digest(3, &[numbered]),
            structural_digest(3, &[unnumbered]),
            "a report with an id of zero and a report with no id must not hash alike"
        );
    }

    #[test]
    fn the_interface_count_is_part_of_the_structure() {
        let c = [collection(6, 111)];
        assert_ne!(structural_digest(2, &c), structural_digest(3, &c));
    }
}
