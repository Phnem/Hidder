//! What a report descriptor says about the shape of a collection's reports.
//!
//! # Why this is library code rather than a tool's helper
//!
//! It was a helper. The descriptor walk lived in `examples/inventory.rs`, which
//! was the right place while the only consumer was a tool that writes hardware
//! artifacts by hand. It stopped being the right place the moment the running
//! application needed the same numbers to identify a device: a fingerprint
//! computed by one implementation and captured by another agrees only for as
//! long as nobody edits either.
//!
//! So there is one walk, here, and the tool uses it too. The regression tests in
//! `tools/emu` compare the result against the captured artifact, which is what
//! makes "the same numbers" checkable rather than intended.
//!
//! # What it deliberately does not do
//!
//! It reports **shape, not meaning**. Report ids, sizes, counts and the three
//! main item types are interpreted; collections, usages, logical ranges and
//! everything else are skipped. A general report-descriptor parser is a real
//! piece of work with real edge cases, and a half-written one that silently
//! guesses is worse than none -- so a descriptor this cannot make sense of
//! yields nothing rather than a plausible-looking total.

use std::collections::BTreeMap;

/// Total payload bits per direction, for one report id.
#[derive(Clone, Copy, PartialEq, Eq, Debug, Default)]
pub struct ReportBits {
    pub input: u32,
    pub output: u32,
    pub feature: u32,
}

/// One report id and what it carries.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub struct ReportSummary {
    pub id: u8,
    pub bits: ReportBits,
    /// True when the descriptor uses report ids at all.
    ///
    /// Load-bearing: when it does not, a report on the wire has no leading id
    /// byte, and a reader that assumes one is off by one for every byte it
    /// looks at.
    pub numbered: bool,
}

impl ReportSummary {
    /// Payload bytes in one direction, including the report id byte when the
    /// descriptor numbers its reports.
    pub fn bytes(&self, bits: u32) -> u16 {
        if bits == 0 {
            return 0;
        }
        let payload = bits.div_ceil(8) + u32::from(self.numbered);
        u16::try_from(payload).unwrap_or(u16::MAX)
    }
}

/// The whole descriptor's shape, plus the digest used to compare descriptors.
#[derive(Clone, PartialEq, Eq, Debug, Default)]
pub struct ReportShape {
    pub reports: Vec<ReportSummary>,
    pub descriptor_fnv1a64: u64,
    pub descriptor_len: usize,
}

impl ReportShape {
    /// The first report, which is the one a single-report vendor collection has.
    pub fn first(&self) -> Option<&ReportSummary> {
        self.reports.first()
    }

    /// The report id to use for a numbered collection, or `None` when the
    /// descriptor does not number its reports.
    pub fn report_id(&self) -> Option<u8> {
        self.reports
            .first()
            .filter(|report| report.numbered)
            .map(|report| report.id)
    }

    pub fn input_bytes(&self) -> u16 {
        self.first().map_or(0, |r| r.bytes(r.bits.input))
    }

    pub fn output_bytes(&self) -> u16 {
        self.first().map_or(0, |r| r.bytes(r.bits.output))
    }

    pub fn feature_bytes(&self) -> u16 {
        self.first().map_or(0, |r| r.bytes(r.bits.feature))
    }
}

/// Walks a report descriptor and totals report sizes per report id.
pub fn parse(descriptor: &[u8]) -> ReportShape {
    let mut totals: BTreeMap<u8, ReportBits> = BTreeMap::new();
    let mut report_id: u8 = 0;
    let mut report_size: u32 = 0;
    let mut report_count: u32 = 0;
    let mut numbered = false;

    let mut index = 0usize;
    while index < descriptor.len() {
        let prefix = descriptor[index];
        index += 1;

        // Long items exist in the spec and are effectively unused; skip them
        // rather than misparse everything after one.
        if prefix == 0xFE {
            let Some(&size) = descriptor.get(index) else {
                break;
            };
            index = index.saturating_add(2 + usize::from(size));
            continue;
        }

        let size = match prefix & 0x03 {
            0 => 0usize,
            1 => 1,
            2 => 2,
            _ => 4,
        };
        if index + size > descriptor.len() {
            break;
        }
        let data = descriptor[index..index + size]
            .iter()
            .enumerate()
            .fold(0u32, |acc, (i, &b)| acc | (u32::from(b) << (8 * i)));
        index += size;

        let item_type = (prefix >> 2) & 0x03;
        let tag = prefix >> 4;
        match (item_type, tag) {
            // Global items.
            (1, 0x7) => report_size = data,
            (1, 0x8) => {
                #[allow(clippy::cast_possible_truncation)]
                {
                    report_id = data as u8;
                }
                numbered = true;
            }
            (1, 0x9) => report_count = data,
            // Main items: Input, Output, Feature.
            (0, 0x8) | (0, 0x9) | (0, 0xB) => {
                let bits = report_size.saturating_mul(report_count);
                let entry = totals.entry(report_id).or_default();
                match tag {
                    0x8 => entry.input += bits,
                    0x9 => entry.output += bits,
                    _ => entry.feature += bits,
                }
            }
            _ => {}
        }
    }

    ReportShape {
        reports: totals
            .into_iter()
            .map(|(id, bits)| ReportSummary { id, bits, numbered })
            .collect(),
        descriptor_fnv1a64: fnv1a64(descriptor),
        descriptor_len: descriptor.len(),
    }
}

/// FNV-1a, 64 bit.
///
/// A comparison aid, and the same one the hardware captures recorded, which is
/// why it lives here now: the running application and the artifacts on disk have
/// to compute it identically or a fingerprint match means nothing.
pub fn fnv1a64(bytes: &[u8]) -> u64 {
    let mut hash: u64 = 0xcbf2_9ce4_8422_2325;
    for &byte in bytes {
        hash ^= u64::from(byte);
        hash = hash.wrapping_mul(0x0000_0100_0000_01b3);
    }
    hash
}

#[cfg(test)]
mod tests {
    #![allow(clippy::expect_used, clippy::panic, clippy::unwrap_used)]

    use super::*;

    /// Our own board's `0xFF60:0x0061` descriptor, captured in TICKET-08.
    const AULA_VENDOR_COLLECTION: [u8; 36] = [
        0x06, 0x60, 0xFF, 0x09, 0x61, 0xA1, 0x01, 0x85, 0x09, 0x09, 0x62, 0x15, 0x00, 0x26, 0xFF,
        0x00, 0x75, 0x08, 0x95, 0x3F, 0x81, 0x02, 0x09, 0x63, 0x15, 0x00, 0x26, 0xFF, 0x00, 0x75,
        0x08, 0x95, 0x3F, 0x91, 0x02, 0xC0,
    ];

    #[test]
    fn the_config_collection_is_sixty_four_bytes_each_way_on_report_nine() {
        let shape = parse(&AULA_VENDOR_COLLECTION);
        assert_eq!(shape.report_id(), Some(9));
        // 63 payload bytes plus the report id byte, which is what TICKET-08
        // recorded and what the frame length in the codec is derived from.
        assert_eq!(shape.input_bytes(), 64);
        assert_eq!(shape.output_bytes(), 64);
        assert_eq!(shape.feature_bytes(), 0);
    }

    #[test]
    fn an_unnumbered_descriptor_reports_no_report_id() {
        // Without the Report ID item, a report on the wire has no leading id
        // byte. Reporting `Some(0)` here would make a caller prepend one.
        let unnumbered: Vec<u8> = AULA_VENDOR_COLLECTION
            .iter()
            .copied()
            .enumerate()
            .filter(|(i, _)| *i != 7 && *i != 8)
            .map(|(_, b)| b)
            .collect();
        let shape = parse(&unnumbered);
        assert_eq!(shape.report_id(), None);
        assert_eq!(shape.input_bytes(), 63, "no id byte to count");
    }

    #[test]
    fn a_truncated_descriptor_stops_rather_than_inventing_totals() {
        for cut in 0..AULA_VENDOR_COLLECTION.len() {
            let shape = parse(&AULA_VENDOR_COLLECTION[..cut]);
            assert!(
                shape.input_bytes() <= 64,
                "a partial descriptor produced a larger report than the whole one"
            );
        }
    }

    #[test]
    fn the_digest_is_the_one_the_captures_recorded() {
        // The number in docs/hardware/aula-hero-84-he.json for this collection.
        // If this implementation and the captured artifacts ever disagree, a
        // structural fingerprint match stops meaning anything, so it is asserted
        // against the file's own value rather than against itself.
        assert_eq!(
            format!("{:016x}", fnv1a64(&AULA_VENDOR_COLLECTION)),
            "57273a389450efe7"
        );
    }
}
