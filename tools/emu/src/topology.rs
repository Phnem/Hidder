//! The shape of a device, as enumeration would report it.
//!
//! Separate from [`crate::aula`] because a device's *shape* and a device's
//! *answers* are two different kinds of evidence, and this project has hardware
//! proving they can disagree: a VXE receiver and the mouse behind it are
//! byte-identical in every collection, every report id and every descriptor hash,
//! and are two different products (TICKET-22). Anything that bundled the two
//! into one "device fixture" would make it awkward to write the test that says
//! so, and that test is one of the reasons this ticket exists.
//!
//! # Why these are builders rather than constants
//!
//! The interesting fixtures are the *wrong* ones — a board missing its config
//! collection, one with two plausible candidates, one whose descriptor changed
//! under a firmware update. Those are variations on a real capture, and a
//! variation is much easier to trust when it is spelled as "the real thing,
//! except this" than when it is a second hand-written table that has to be
//! checked against the first.
//!
//! The baseline itself is not written down here at all. It is parsed from
//! `docs/hardware/aula-hero-84-he.json`, the TICKET-08 capture, so this module
//! cannot drift away from the hardware by being edited.

/// One top-level collection, in the terms enumeration reports.
///
/// Deliberately not `pregistry::CollectionObservation`: this crate is below the
/// tests that use it in dependency terms but the conversion belongs to the
/// caller, and keeping the fixture free of `pregistry` means the emulator is
/// usable by anything that wants a fake device rather than only by the matcher.
#[derive(Clone, PartialEq, Eq, Debug)]
pub struct Collection {
    pub interface: u8,
    pub usage_page: u16,
    pub usage: u16,
    pub descriptor_fnv1a64: u64,
    pub report_id: Option<u8>,
    pub input_bytes: u16,
    pub output_bytes: u16,
    pub feature_bytes: u16,
}

impl Collection {
    pub fn is_vendor_defined(&self) -> bool {
        (0xFF00..=0xFFFF).contains(&self.usage_page)
    }

    /// Whether this collection could carry a config channel: vendor-defined,
    /// numbered, and bidirectional.
    ///
    /// A search heuristic and not a claim. The board this is modelled on has two
    /// vendor collections and only one of them is the config channel; the other
    /// carries feature reports only and would fail the `output_bytes` test.
    pub fn is_bidirectional_vendor_channel(&self) -> bool {
        self.is_vendor_defined()
            && self.report_id.is_some_and(|id| id != 0)
            && self.input_bytes > 0
            && self.output_bytes > 0
    }
}

/// A device's whole enumerated shape.
#[derive(Clone, PartialEq, Eq, Debug)]
pub struct Topology {
    pub vendor_id: u16,
    pub product_id: u16,
    pub manufacturer: Option<String>,
    pub product: Option<String>,
    pub release: u16,
    pub serial_present: bool,
    pub interfaces: u8,
    pub collections: Vec<Collection>,
}

impl Topology {
    /// The config collection, by the rule the vendor's own driver uses: the
    /// vendor collection carrying both input and output reports.
    ///
    /// Returns `None` when there is no such collection **and** when there is
    /// more than one. An ambiguous device is not one to pick a favourite from:
    /// two plausible channels means the rule that has been relied on so far does
    /// not decide this board, and guessing would put a frame on an endpoint
    /// nobody has established anything about.
    pub fn config_collection(&self) -> Option<&Collection> {
        let mut candidates = self
            .collections
            .iter()
            .filter(|c| c.is_bidirectional_vendor_channel());
        let first = candidates.next()?;
        match candidates.next() {
            None => Some(first),
            Some(_) => None,
        }
    }

    /// How many collections could plausibly be a config channel. `1` is the
    /// answer that lets a caller proceed; this exists so a test can tell "none"
    /// from "too many", which [`Topology::config_collection`] deliberately does
    /// not.
    pub fn config_candidate_count(&self) -> usize {
        self.collections
            .iter()
            .filter(|c| c.is_bidirectional_vendor_channel())
            .count()
    }

    // --- variations, for the fixtures that are supposed to fail -------------

    /// The same device with its config collection removed.
    pub fn without_config_collection(mut self) -> Self {
        self.collections
            .retain(|c| !c.is_bidirectional_vendor_channel());
        self
    }

    /// The same device with a second, equally plausible vendor channel.
    ///
    /// Models a board this project has not seen. That is the point: the rule for
    /// picking a config collection has only ever been exercised against hardware
    /// where it happens to be unambiguous.
    pub fn with_duplicate_config_collection(mut self) -> Self {
        if let Some(existing) = self
            .collections
            .iter()
            .find(|c| c.is_bidirectional_vendor_channel())
            .cloned()
        {
            let mut twin = existing;
            twin.interface = twin.interface.wrapping_add(1);
            twin.usage_page = 0xFF01;
            twin.usage = 0x0002;
            twin.descriptor_fnv1a64 ^= 0xFFFF_FFFF;
            self.collections.push(twin);
        }
        self
    }

    /// The same product with one descriptor byte different, as a firmware
    /// update would leave it.
    pub fn with_changed_descriptor(mut self) -> Self {
        if let Some(first) = self.collections.first_mut() {
            first.descriptor_fnv1a64 ^= 1;
        }
        self
    }

    /// The same shape under a different product id and name.
    ///
    /// The registry must not conclude "this is that product" from structure
    /// alone, and this project owns hardware that proves why: a receiver and the
    /// mouse behind it are structurally identical and are not the same thing.
    pub fn as_unknown_product(mut self, product_id: u16, product: &str) -> Self {
        self.product_id = product_id;
        self.product = Some(product.to_string());
        self
    }

    /// The same VID:PID with a different shape and name entirely.
    ///
    /// Nine models share this board's VID:PID -- the vendor's own software
    /// cannot tell them apart from it either and asks the device instead.
    pub fn as_sibling_model(mut self, product: &str) -> Self {
        self.product = Some(product.to_string());
        self.collections.truncate(self.collections.len().max(1) - 1);
        self.interfaces = self.interfaces.max(1) - 1;
        self
    }
}
