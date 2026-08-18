// Turns `data/devices/*.toml` into the built-in registry.
//
// Included by `build.rs` and compiled into the crate under `cfg(test)`, the same
// arrangement `psafety` uses, so the generator the tests exercise is the text
// that runs during a build. Module comments are `//` rather than `//!` because
// an inner doc comment cannot appear in the middle of the build script that
// includes this file.
//
// It refuses rather than repairs, for the same reason as the opcode ACL: a
// device entry that is quietly wrong produces a matcher that is quietly wrong,
// and a matcher that is quietly wrong is how the write gate ends up pointed at
// the wrong firmware.

use std::collections::BTreeSet;
use std::fmt::Write as _;

#[derive(Debug, serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct DeviceFile {
    schema: String,
    id: String,
    name: String,
    brands: Vec<String>,
    kind: String,
    note: String,
    product: ProductSection,
    structure: StructureSection,
    /// Absent means unknown, which is the normal state and not an omission.
    #[serde(default)]
    family: Option<FamilySection>,
}

#[derive(Debug, serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct ProductSection {
    vendor_id: i64,
    product_id: i64,
    manufacturer: String,
    product: String,
    release: i64,
}

#[derive(Debug, serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct StructureSection {
    interfaces: u8,
    #[serde(default)]
    collection: Vec<CollectionSection>,
}

#[derive(Debug, serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct CollectionSection {
    interface: u8,
    usage_page: i64,
    usage: i64,
    descriptor_fnv1a64: String,
    /// Absent means the descriptor numbers no reports. Not the same as zero.
    #[serde(default)]
    report_id: Option<u8>,
    input_bytes: u16,
    output_bytes: u16,
    feature_bytes: u16,
}

#[derive(Debug, serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct FamilySection {
    id: String,
    /// What this claim can support on its own. `verified` means this project
    /// established it on this project's hardware -- not that someone is sure.
    evidence: String,
}

const SCHEMA: &str = "peripheral.device-registry/1";
const KINDS: [&str; 4] = ["keyboard", "mouse", "receiver", "other"];
const EVIDENCE: [&str; 3] = ["candidate", "high", "verified"];

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Entry {
    pub id: String,
    pub name: String,
    pub kind: String,
    pub interfaces: u8,
    pub vendor_id: u16,
    pub product_id: u16,
    pub manufacturer: String,
    pub product: String,
    pub release: u16,
    pub collections: Vec<Collection>,
    pub family: Option<(String, String)>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Collection {
    pub interface: u8,
    pub usage_page: u16,
    pub usage: u16,
    pub descriptor: u64,
    pub report_id: Option<u8>,
    pub input_bytes: u16,
    pub output_bytes: u16,
    pub feature_bytes: u16,
}

pub fn parse_device(source: &str) -> Result<Entry, String> {
    let file: DeviceFile = toml::from_str(source).map_err(|e| e.to_string())?;

    if file.schema != SCHEMA {
        return Err(format!(
            "unknown schema {:?}; this generator understands {SCHEMA} only",
            file.schema
        ));
    }
    check_id(&file.id)?;
    if file.name.trim().is_empty() || file.note.trim().is_empty() {
        return Err(format!(
            "device {}: name and note must say something",
            file.id
        ));
    }
    if file.brands.is_empty() {
        return Err(format!("device {}: list at least one brand", file.id));
    }
    if !KINDS.contains(&file.kind.as_str()) {
        return Err(format!(
            "device {}: kind {:?} is not one of {KINDS:?}",
            file.id, file.kind
        ));
    }
    if file.structure.collection.is_empty() {
        return Err(format!(
            "device {}: a structural identity with no collections identifies nothing",
            file.id
        ));
    }

    let vendor_id = u16::try_from(file.product.vendor_id)
        .map_err(|_| format!("device {}: vendor_id does not fit in 16 bits", file.id))?;
    let product_id = u16::try_from(file.product.product_id)
        .map_err(|_| format!("device {}: product_id does not fit in 16 bits", file.id))?;
    let release = u16::try_from(file.product.release)
        .map_err(|_| format!("device {}: release does not fit in 16 bits", file.id))?;
    if file.product.manufacturer.is_empty() || file.product.product.is_empty() {
        return Err(format!(
            "device {}: record the strings the device reports, even if they surprise you -- they are product-identity signals",
            file.id
        ));
    }

    let mut collections = Vec::new();
    let mut seen = BTreeSet::new();
    for c in &file.structure.collection {
        let usage_page = u16::try_from(c.usage_page)
            .map_err(|_| format!("device {}: usage_page does not fit in 16 bits", file.id))?;
        let usage = u16::try_from(c.usage)
            .map_err(|_| format!("device {}: usage does not fit in 16 bits", file.id))?;
        if c.descriptor_fnv1a64.len() != 16
            || !c
                .descriptor_fnv1a64
                .chars()
                .all(|ch| ch.is_ascii_hexdigit())
        {
            return Err(format!(
                "device {}: descriptor_fnv1a64 {:?} is not 16 hex digits",
                file.id, c.descriptor_fnv1a64
            ));
        }
        let descriptor = u64::from_str_radix(&c.descriptor_fnv1a64, 16)
            .map_err(|e| format!("device {}: {e}", file.id))?;
        if c.interface >= file.structure.interfaces {
            return Err(format!(
                "device {}: a collection sits on interface {} but the device declares {} interface(s)",
                file.id, c.interface, file.structure.interfaces
            ));
        }
        if !seen.insert((c.interface, usage_page, usage, descriptor)) {
            return Err(format!(
                "device {}: collection {usage_page:#06x}:{usage:#06x} on interface {} is listed twice",
                file.id, c.interface
            ));
        }
        collections.push(Collection {
            interface: c.interface,
            usage_page,
            usage,
            descriptor,
            report_id: c.report_id,
            input_bytes: c.input_bytes,
            output_bytes: c.output_bytes,
            feature_bytes: c.feature_bytes,
        });
    }

    let family = match &file.family {
        Some(section) => {
            check_id(&section.id)?;
            if !EVIDENCE.contains(&section.evidence.as_str()) {
                return Err(format!(
                    "device {}: family evidence {:?} is not one of {EVIDENCE:?}; there is no way to claim a family without saying how well it is known",
                    file.id, section.evidence
                ));
            }
            Some((section.id.clone(), section.evidence.clone()))
        }
        None => None,
    };

    Ok(Entry {
        id: file.id,
        name: file.name,
        kind: file.kind,
        interfaces: file.structure.interfaces,
        vendor_id,
        product_id,
        manufacturer: file.product.manufacturer,
        product: file.product.product,
        release,
        collections,
        family,
    })
}

pub fn build_registry(sources: &[(String, String)]) -> Result<Vec<Entry>, String> {
    let mut entries = Vec::new();
    let mut ids = BTreeSet::new();
    for (origin, source) in sources {
        let entry = parse_device(source).map_err(|e| format!("{origin}: {e}"))?;
        if !ids.insert(entry.id.clone()) {
            return Err(format!(
                "{origin}: device id {} is declared twice",
                entry.id
            ));
        }
        entries.push(entry);
    }
    // Deterministic output regardless of how the directory was read.
    entries.sort_by(|a, b| a.id.cmp(&b.id));
    Ok(entries)
}

pub fn emit(entries: &[Entry]) -> String {
    let mut out = String::new();
    out.push_str(
        "// @generated by pregistry/build.rs from data/devices/*.toml. Do not edit.\n\
         //\n\
         // A `family: None` below is not a gap waiting to be filled in by whoever\n\
         // notices it. It is the recorded state of knowledge: nothing has established\n\
         // which opcode vocabulary that device speaks, and until something does, the\n\
         // write gate has nothing to permit.\n\n",
    );

    for entry in entries {
        let _ = writeln!(
            out,
            "const {}_STRUCTURE: &[StructuralCollection] = &[",
            const_name(&entry.id)
        );
        for c in &entry.collections {
            let report_id = match c.report_id {
                Some(id) => format!("Some({id})"),
                None => "None".to_owned(),
            };
            let _ = writeln!(
                out,
                "    StructuralCollection {{ interface: {}, usage_page: {:#06x}, usage: {:#06x}, descriptor_fnv1a64: {:#018x}, report_id: {report_id}, input_bytes: {}, output_bytes: {}, feature_bytes: {} }},",
                c.interface,
                c.usage_page,
                c.usage,
                c.descriptor,
                c.input_bytes,
                c.output_bytes,
                c.feature_bytes
            );
        }
        out.push_str("];\n\n");
    }

    out.push_str("pub(crate) const ENTRIES: &[DeviceEntry] = &[\n");
    for entry in entries {
        let family = match &entry.family {
            Some((id, evidence)) => format!(
                "Some(FamilyClaim {{ family: {id:?}, evidence: Confidence::{} }})",
                confidence_variant(evidence)
            ),
            None => "None".to_owned(),
        };
        let _ = writeln!(
            out,
            "    DeviceEntry {{ id: {:?}, name: {:?}, kind: DeviceKind::{}, interfaces: {}, product: ProductIdentity {{ vendor_id: {:#06x}, product_id: {:#06x}, manufacturer: {:?}, product: {:?}, release: {:#06x} }}, structure: {}_STRUCTURE, family: {family} }},",
            entry.id,
            entry.name,
            kind_variant(&entry.kind),
            entry.interfaces,
            entry.vendor_id,
            entry.product_id,
            entry.manufacturer,
            entry.product,
            entry.release,
            const_name(&entry.id),
        );
    }
    out.push_str("];\n");
    out
}

fn kind_variant(kind: &str) -> &'static str {
    match kind {
        "keyboard" => "Keyboard",
        "mouse" => "Mouse",
        "receiver" => "Receiver",
        _ => "Other",
    }
}

fn confidence_variant(evidence: &str) -> &'static str {
    match evidence {
        "verified" => "Verified",
        "high" => "High",
        _ => "Candidate",
    }
}

fn const_name(id: &str) -> String {
    id.chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() {
                c.to_ascii_uppercase()
            } else {
                '_'
            }
        })
        .collect()
}

fn check_id(id: &str) -> Result<(), String> {
    let ok = !id.is_empty()
        && id
            .chars()
            .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '-')
        && !id.starts_with('-')
        && !id.ends_with('-');
    if ok {
        Ok(())
    } else {
        Err(format!("id {id:?} must be lowercase kebab-case"))
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::expect_used, clippy::panic, clippy::unwrap_used)]

    use super::*;

    fn good() -> String {
        r#"
schema = "peripheral.device-registry/1"
id     = "test-device"
name   = "Test device"
brands = ["Test"]
kind   = "mouse"
note   = "fixture"

[product]
vendor_id    = 0x1234
product_id   = 0x5678
manufacturer = "Maker"
product      = "Thing"
release      = 0x0100

[structure]
interfaces = 1

[[structure.collection]]
interface          = 0
usage_page         = 0x0001
usage              = 0x0002
descriptor_fnv1a64 = "d681df95aa1eda7e"
input_bytes        = 7
output_bytes       = 0
feature_bytes      = 0
"#
        .to_owned()
    }

    #[test]
    fn a_device_without_a_family_parses_and_claims_nothing() {
        let entry = parse_device(&good()).expect("valid");
        assert_eq!(entry.family, None, "a family must never be invented");
    }

    #[test]
    fn a_family_without_evidence_is_refused() {
        let source = good() + "\n[family]\nid = \"some-family\"\n";
        assert!(
            parse_device(&source).is_err(),
            "a family claim with no evidence field must not parse"
        );
    }

    #[test]
    fn a_family_with_an_unknown_evidence_level_is_refused() {
        let source = good() + "\n[family]\nid = \"some-family\"\nevidence = \"probably\"\n";
        let error = parse_device(&source).expect_err("no such evidence level");
        assert!(error.contains("probably"), "unhelpful message: {error}");
    }

    #[test]
    fn a_misspelt_field_is_refused() {
        let source = good().replace("usage_page", "usage_pge");
        assert!(parse_device(&source).is_err(), "a typo is not an extension");
    }

    #[test]
    fn a_short_descriptor_hash_is_refused() {
        let source = good().replace("d681df95aa1eda7e", "d681df95");
        let error = parse_device(&source).expect_err("truncated hash");
        assert!(
            error.contains("16 hex digits"),
            "unhelpful message: {error}"
        );
    }

    #[test]
    fn a_collection_on_an_interface_the_device_does_not_have_is_refused() {
        let source = good().replace("interface          = 0", "interface          = 4");
        assert!(parse_device(&source).is_err(), "off-by-one in the topology");
    }

    #[test]
    fn a_device_with_no_collections_is_refused() {
        let source = good();
        let cut = source.find("[[structure.collection]]").expect("fixture");
        assert!(
            parse_device(&source[..cut]).is_err(),
            "a structural identity with nothing in it identifies everything"
        );
    }

    #[test]
    fn a_duplicate_collection_is_refused() {
        let source = good()
            + r#"
[[structure.collection]]
interface          = 0
usage_page         = 0x0001
usage              = 0x0002
descriptor_fnv1a64 = "d681df95aa1eda7e"
input_bytes        = 7
output_bytes       = 0
feature_bytes      = 0
"#;
        assert!(parse_device(&source).is_err(), "listed twice");
    }

    #[test]
    fn a_duplicate_device_id_is_refused() {
        let sources = [("a".to_owned(), good()), ("b".to_owned(), good())];
        assert!(build_registry(&sources).is_err());
    }

    #[test]
    fn output_does_not_depend_on_file_order() {
        let a = good();
        let b = good().replace("test-device", "other-device");
        let forward = build_registry(&[("1".into(), a.clone()), ("2".into(), b.clone())]);
        let backward = build_registry(&[("2".into(), b), ("1".into(), a)]);
        assert_eq!(forward.expect("valid"), backward.expect("valid"));
    }

    #[test]
    fn an_unnumbered_report_survives_generation_as_none() {
        let entries = build_registry(&[("f".into(), good())]).expect("valid");
        let emitted = emit(&entries);
        assert!(
            emitted.contains("report_id: None"),
            "an absent report id became something else:\n{emitted}"
        );
    }
}
