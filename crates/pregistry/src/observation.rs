//! What the matcher is allowed to know about a device.
//!
//! Everything here comes from enumeration: entries the operating system already
//! holds, cached string descriptors, and the report descriptor. Nothing here can
//! come from an exchange with the firmware, and that is a property of the type
//! rather than a rule someone has to remember.
//!
//! The distinction matters because the specification lists a "safe identify
//! opcode" among the fingerprint signals. Sending one is a probe, probes belong
//! behind the safety gate, and this crate has no path to the gate. When a later
//! ticket earns the right to probe, that signal arrives as a new field with its
//! own name, and every reader can then see which answers depended on it.

use crate::identity::{StructuralCollection, structural_digest};

/// One top-level collection as enumeration reported it.
#[derive(Clone, PartialEq, Eq, Debug)]
pub struct CollectionObservation {
    pub interface: u8,
    pub usage_page: u16,
    pub usage: u16,
    pub descriptor_fnv1a64: u64,
    pub report_id: Option<u8>,
    pub input_bytes: u16,
    pub output_bytes: u16,
    pub feature_bytes: u16,
}

impl CollectionObservation {
    pub fn is_vendor_defined(&self) -> bool {
        (0xFF00..=0xFFFF).contains(&self.usage_page)
    }

    fn normalised(&self) -> StructuralCollection {
        StructuralCollection {
            interface: self.interface,
            usage_page: self.usage_page,
            usage: self.usage,
            descriptor_fnv1a64: self.descriptor_fnv1a64,
            report_id: self.report_id,
            input_bytes: self.input_bytes,
            output_bytes: self.output_bytes,
            feature_bytes: self.feature_bytes,
        }
    }
}

/// A device as enumeration saw it.
///
/// The serial number is deliberately reduced to a boolean before it ever
/// reaches this type. Whether a serial exists is a fingerprint signal; its value
/// identifies one physical unit and is of no use in deciding what a device is
/// (spec.md § Domain rules).
#[derive(Clone, PartialEq, Eq, Debug)]
pub struct DeviceObservation {
    pub vendor_id: u16,
    pub product_id: u16,
    pub manufacturer: Option<String>,
    pub product: Option<String>,
    pub release: u16,
    pub serial_present: bool,
    pub interfaces: u8,
    pub collections: Vec<CollectionObservation>,
}

impl DeviceObservation {
    /// The only constructor. Named for where the data has to come from.
    #[allow(clippy::too_many_arguments)]
    pub fn from_enumeration(
        vendor_id: u16,
        product_id: u16,
        manufacturer: Option<String>,
        product: Option<String>,
        release: u16,
        serial_present: bool,
        interfaces: u8,
        collections: Vec<CollectionObservation>,
    ) -> Self {
        Self {
            vendor_id,
            product_id,
            manufacturer,
            product,
            release,
            serial_present,
            interfaces,
            collections,
        }
    }

    pub(crate) fn normalised_collections(&self) -> Vec<StructuralCollection> {
        self.collections.iter().map(|c| c.normalised()).collect()
    }

    pub(crate) fn structural_digest(&self) -> u64 {
        structural_digest(self.interfaces, &self.normalised_collections())
    }

    /// The set of `(usage page, usage)` pairs, sorted and deduplicated. A
    /// weaker structural signal than the digest, and the one that still says
    /// something when a firmware update changes a descriptor byte.
    pub(crate) fn collection_set(&self) -> Vec<(u16, u16)> {
        let mut set: Vec<(u16, u16)> = self
            .collections
            .iter()
            .map(|c| (c.usage_page, c.usage))
            .collect();
        set.sort_unstable();
        set.dedup();
        set
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::expect_used, clippy::panic, clippy::unwrap_used)]

    use super::*;

    fn observation(collections: Vec<CollectionObservation>) -> DeviceObservation {
        DeviceObservation::from_enumeration(
            0x1234,
            0x5678,
            Some("Maker".to_owned()),
            Some("Thing".to_owned()),
            0x0100,
            true,
            2,
            collections,
        )
    }

    fn collection(usage_page: u16, usage: u16) -> CollectionObservation {
        CollectionObservation {
            interface: 0,
            usage_page,
            usage,
            descriptor_fnv1a64: 42,
            report_id: None,
            input_bytes: 8,
            output_bytes: 0,
            feature_bytes: 0,
        }
    }

    #[test]
    fn the_collection_set_is_sorted_and_deduplicated() {
        let obs = observation(vec![
            collection(0x000C, 0x0001),
            collection(0x0001, 0x0006),
            collection(0x0001, 0x0006),
        ]);
        assert_eq!(
            obs.collection_set(),
            vec![(0x0001, 0x0006), (0x000C, 0x0001)]
        );
    }

    #[test]
    fn vendor_defined_pages_are_recognised_by_range() {
        assert!(collection(0xFF00, 1).is_vendor_defined());
        assert!(collection(0xFF60, 0x61).is_vendor_defined());
        assert!(!collection(0x0001, 6).is_vendor_defined());
    }

    #[test]
    fn two_captures_of_one_device_agree_whatever_the_order() {
        let a = observation(vec![collection(0x0001, 0x0006), collection(0x000C, 0x0001)]);
        let b = observation(vec![collection(0x000C, 0x0001), collection(0x0001, 0x0006)]);
        assert_eq!(a.structural_digest(), b.structural_digest());
    }
}
