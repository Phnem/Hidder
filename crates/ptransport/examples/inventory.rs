//! Read-only HID inventory tool (TICKET-08).
//!
//! Produces the evidence artifact for a reference board: every top-level
//! collection, whether it opens, its report descriptor, and the report IDs and
//! sizes parsed out of that descriptor. Two devices inventoried by this tool
//! produce reports that compare line by line, which is the point -- TICKET-09
//! runs exactly this against the second board.
//!
//! ```text
//! cargo run -p ptransport --example inventory
//! cargo run -p ptransport --example inventory -- --device 258A:0049 \
//!     --label "AULA Hero 84 HE" --out docs/hardware/aula-hero-84-he
//! ```
//!
//! Read-only, with no exceptions: it opens collections and reads what the OS
//! already knows. It never writes, never sends a feature report, never probes,
//! and never reads an input report. See the module docs of `ptransport::inventory`.
//!
//! The descriptor parser below is a development-tool parser. It is not product
//! code and deliberately does not live in a `p*` crate: the product parser reads
//! untrusted input from a device and needs fuzzing (spec.md § Test seams), and
//! that lands with `pregistry` in TICKET-10.

use std::collections::BTreeMap;
use std::fmt::Write as _;

use ptransport::{CollectionAccess, Hid, HidCollection};
use serde_json::{Value, json};

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let options = match Options::parse(&args) {
        Ok(options) => options,
        Err(message) => {
            eprintln!("{message}\n\n{USAGE}");
            std::process::exit(2);
        }
    };

    let hid = match Hid::open() {
        Ok(hid) => hid,
        Err(error) => {
            eprintln!("could not initialise the HID backend: {error}");
            std::process::exit(1);
        }
    };

    let all = hid.enumerate();
    println!("{} HID collections enumerated\n", all.len());

    let Some((vendor_id, product_id)) = options.device else {
        print_overview(&all);
        println!("\nPass --device VID:PID for a full inventory of one device.");
        return;
    };

    let selected: Vec<HidCollection> = all
        .into_iter()
        .filter(|c| c.vendor_id == vendor_id && c.product_id == product_id)
        .collect();

    if selected.is_empty() {
        eprintln!("no collection matches {vendor_id:04X}:{product_id:04X}; is it plugged in?");
        std::process::exit(1);
    }

    let inspected: Vec<(HidCollection, CollectionAccess)> = selected
        .into_iter()
        .map(|collection| {
            let access = hid.inspect(&collection);
            (collection, access)
        })
        .collect();

    let label = options.label.unwrap_or_else(|| {
        inspected
            .first()
            .and_then(|(c, _)| c.product.clone())
            .unwrap_or_else(|| format!("{vendor_id:04X}:{product_id:04X}"))
    });

    print_detail(&label, &inspected);

    if let Some(out) = options.out {
        let json = build_json(
            &label,
            vendor_id,
            product_id,
            &inspected,
            options.include_serial,
        );
        let markdown = build_markdown(
            &label,
            vendor_id,
            product_id,
            &inspected,
            options.include_serial,
        );
        write_artifact(&format!("{out}.json"), &format!("{json:#}\n"));
        write_artifact(&format!("{out}.md"), &markdown);
    }
}

const USAGE: &str = "\
usage: inventory [--device VID:PID] [--label NAME] [--out PATH-WITHOUT-EXTENSION]

  no arguments    list every HID collection on this machine
  --device        inventory one device in full (hex, e.g. 258A:0049)
  --label         human name for the report
  --out           write PATH.json and PATH.md
  --include-serial  put the unit's serial number in the report (default: only
                    whether one exists, because the report is committed)";

struct Options {
    device: Option<(u16, u16)>,
    label: Option<String>,
    out: Option<String>,
    /// Off by default, and that default is the point.
    ///
    /// The serial number is a globally unique identifier for one physical unit.
    /// This artifact gets committed, and the same shape of artifact is what
    /// community device submissions will eventually be (TICKET-18), so a serial
    /// in it would be a cross-submission tracking identifier attached to a
    /// person's keyboard. Whether the string *exists* is the part that matters
    /// for fingerprinting; its value is not.
    include_serial: bool,
}

impl Options {
    fn parse(args: &[String]) -> Result<Self, String> {
        let mut options = Options {
            device: None,
            label: None,
            out: None,
            include_serial: false,
        };
        let mut rest = args.iter();
        while let Some(flag) = rest.next() {
            let value = || {
                rest.clone()
                    .next()
                    .cloned()
                    .ok_or_else(|| format!("{flag} needs a value"))
            };
            match flag.as_str() {
                "--device" => {
                    let raw = value()?;
                    rest.next();
                    options.device = Some(parse_vid_pid(&raw)?);
                }
                "--label" => {
                    options.label = Some(value()?);
                    rest.next();
                }
                "--out" => {
                    options.out = Some(value()?);
                    rest.next();
                }
                "--include-serial" => options.include_serial = true,
                "--help" | "-h" => return Err("help".into()),
                other => return Err(format!("unknown argument: {other}")),
            }
        }
        Ok(options)
    }
}

fn parse_vid_pid(raw: &str) -> Result<(u16, u16), String> {
    let (vid, pid) = raw
        .split_once(':')
        .ok_or_else(|| format!("expected VID:PID, got {raw}"))?;
    let parse = |s: &str| {
        u16::from_str_radix(s.trim_start_matches("0x").trim_start_matches("0X"), 16)
            .map_err(|_| format!("{s} is not a hex u16"))
    };
    Ok((parse(vid)?, parse(pid)?))
}

fn print_overview(all: &[HidCollection]) {
    let mut by_device: BTreeMap<(u16, u16), Vec<&HidCollection>> = BTreeMap::new();
    for collection in all {
        by_device
            .entry((collection.vendor_id, collection.product_id))
            .or_default()
            .push(collection);
    }
    for ((vendor_id, product_id), collections) in by_device {
        let first = collections[0];
        let vendor_tlc = collections.iter().filter(|c| c.is_vendor_defined()).count();
        println!(
            "{:04X}:{:04X}  {:<44}  {} collection(s), {} vendor-defined",
            vendor_id,
            product_id,
            format!(
                "{} {}",
                first.manufacturer.as_deref().unwrap_or("?"),
                first.product.as_deref().unwrap_or("?")
            )
            .trim(),
            collections.len(),
            vendor_tlc
        );
    }
}

fn print_detail(label: &str, inspected: &[(HidCollection, CollectionAccess)]) {
    println!("=== {label} ===\n");
    for (collection, access) in inspected {
        println!(
            "if {:>2}  usage {:04X}:{:04X}{}  {}",
            collection.interface_number,
            collection.usage_page,
            collection.usage,
            if collection.is_vendor_defined() {
                " [vendor]"
            } else {
                "         "
            },
            match (&access.opened, &access.open_error) {
                (true, _) => "opened".to_string(),
                (false, Some(error)) => format!("NOT OPENED: {error}"),
                (false, None) => "NOT OPENED".to_string(),
            }
        );
        match (&access.report_descriptor, &access.descriptor_error) {
            (Some(bytes), _) => {
                let reports = parse_reports(bytes);
                println!(
                    "        descriptor {} bytes, fnv1a64 {:016x}, {} report(s)",
                    bytes.len(),
                    fnv1a64(bytes),
                    reports.len()
                );
                for report in &reports {
                    println!("          {}", report.describe());
                }
            }
            (None, Some(error)) => println!("        descriptor unavailable: {error}"),
            (None, None) => println!("        descriptor unavailable"),
        }
    }
}

// --- report descriptor parsing (development tool only, see module docs) -----

#[derive(Default, Clone, Copy)]
struct ReportBits {
    input: u32,
    output: u32,
    feature: u32,
}

struct ReportSummary {
    id: u8,
    bits: ReportBits,
    /// True when the descriptor uses report IDs at all. When it does not, a
    /// report on the wire has no leading ID byte, and a reader that assumes one
    /// is off by one for every byte it looks at.
    numbered: bool,
}

impl ReportSummary {
    fn describe(&self) -> String {
        let bytes = |bits: u32| {
            if bits == 0 {
                "-".to_string()
            } else {
                let payload = bits.div_ceil(8);
                format!("{}", payload + u32::from(self.numbered))
            }
        };
        format!(
            "report {:>3}: in {:>4}  out {:>4}  feature {:>4}   (bytes, {} report id)",
            self.id,
            bytes(self.bits.input),
            bytes(self.bits.output),
            bytes(self.bits.feature),
            if self.numbered { "incl." } else { "no" }
        )
    }
}

/// Walk the descriptor and total up report sizes per report ID.
///
/// Only the items needed for that are interpreted: Report ID, Report Size,
/// Report Count, and the Input/Output/Feature main items. Everything else,
/// including collections and usages, is skipped deliberately -- this tool
/// reports shape, not meaning.
fn parse_reports(descriptor: &[u8]) -> Vec<ReportSummary> {
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
        // rather than misparse the rest of the descriptor after one.
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
                report_id = data as u8;
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

    totals
        .into_iter()
        .map(|(id, bits)| ReportSummary { id, bits, numbered })
        .collect()
}

/// FNV-1a, 64 bit.
///
/// A comparison aid for these reports, explicitly **not** the fingerprint hash.
/// The registry's descriptor hash is a product decision that belongs in
/// `pregistry` (TICKET-10) and should be a real digest; picking one here, in a
/// dev tool, would fix it in the wrong place for the wrong reason.
fn fnv1a64(bytes: &[u8]) -> u64 {
    let mut hash: u64 = 0xcbf2_9ce4_8422_2325;
    for &byte in bytes {
        hash ^= u64::from(byte);
        hash = hash.wrapping_mul(0x0000_0100_0000_01b3);
    }
    hash
}

fn hex(bytes: &[u8]) -> String {
    let mut out = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        let _ = write!(out, "{byte:02x}");
    }
    out
}

// --- artifacts --------------------------------------------------------------

fn build_json(
    label: &str,
    vendor_id: u16,
    product_id: u16,
    inspected: &[(HidCollection, CollectionAccess)],
    include_serial: bool,
) -> Value {
    let collections: Vec<Value> = inspected
        .iter()
        .map(|(collection, access)| {
            let reports: Vec<Value> = access
                .report_descriptor
                .as_ref()
                .map(|bytes| {
                    parse_reports(bytes)
                        .into_iter()
                        .map(|report| {
                            json!({
                                "report_id": report.id,
                                "numbered": report.numbered,
                                "input_bits": report.bits.input,
                                "output_bits": report.bits.output,
                                "feature_bits": report.bits.feature,
                            })
                        })
                        .collect()
                })
                .unwrap_or_default();

            json!({
                "interface_number": collection.interface_number,
                "usage_page": format!("0x{:04X}", collection.usage_page),
                "usage": format!("0x{:04X}", collection.usage),
                "vendor_defined": collection.is_vendor_defined(),
                "manufacturer": collection.manufacturer,
                "product": collection.product,
                // Presence is a fingerprint signal; the value is a unique unit
                // identifier and stays out unless explicitly asked for.
                "serial_number_present": collection.serial_number.is_some(),
                "serial_number": if include_serial {
                    json!(collection.serial_number)
                } else {
                    Value::Null
                },
                "release_number": format!("0x{:04X}", collection.release_number),
                // The OS path is recorded for reproducing a run on this machine
                // only. It is not an identity: it changes between reboots.
                "path": collection.path,
                "opened": access.opened,
                "open_error": access.open_error,
                "report_descriptor_len": access.report_descriptor.as_ref().map(Vec::len),
                "report_descriptor_fnv1a64": access
                    .report_descriptor
                    .as_ref()
                    .map(|b| format!("{:016x}", fnv1a64(b))),
                "report_descriptor_hex": access.report_descriptor.as_ref().map(|b| hex(b)),
                "report_descriptor_error": access.descriptor_error,
                "reports": reports,
            })
        })
        .collect();

    json!({
        "schema": "peripheral.hid-inventory/1",
        "label": label,
        "vendor_id": format!("0x{vendor_id:04X}"),
        "product_id": format!("0x{product_id:04X}"),
        "host_os": std::env::consts::OS,
        "captured_unix": std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_secs())
            .unwrap_or_default(),
        "method": {
            "read_only": true,
            "writes": "none",
            "feature_reports_sent": "none",
            "input_reports_read": "none",
        },
        "collections": collections,
    })
}

fn build_markdown(
    label: &str,
    vendor_id: u16,
    product_id: u16,
    inspected: &[(HidCollection, CollectionAccess)],
    include_serial: bool,
) -> String {
    let mut out = String::new();
    let _ = writeln!(out, "# HID inventory: {label}\n");
    let _ = writeln!(
        out,
        "VID:PID `{vendor_id:04X}:{product_id:04X}` | host `{}` | {} collection(s)\n",
        std::env::consts::OS,
        inspected.len()
    );
    let _ = writeln!(
        out,
        "Read-only capture: no writes, no feature reports sent, no input reports read.\n"
    );

    let _ = writeln!(
        out,
        "| if | usage page | usage | vendor | opened | desc bytes | fnv1a64 | reports |"
    );
    let _ = writeln!(out, "|---|---|---|---|---|---|---|---|");
    for (collection, access) in inspected {
        let descriptor_len = access
            .report_descriptor
            .as_ref()
            .map(|b| b.len().to_string())
            .unwrap_or_else(|| "-".into());
        let hash = access
            .report_descriptor
            .as_ref()
            .map(|b| format!("`{:016x}`", fnv1a64(b)))
            .unwrap_or_else(|| "-".into());
        let reports = access
            .report_descriptor
            .as_ref()
            .map(|b| parse_reports(b).len().to_string())
            .unwrap_or_else(|| "-".into());
        let _ = writeln!(
            out,
            "| {} | `0x{:04X}` | `0x{:04X}` | {} | {} | {} | {} | {} |",
            collection.interface_number,
            collection.usage_page,
            collection.usage,
            if collection.is_vendor_defined() {
                "yes"
            } else {
                "no"
            },
            if access.opened { "yes" } else { "**no**" },
            descriptor_len,
            hash,
            reports
        );
    }

    for (collection, access) in inspected {
        let _ = writeln!(
            out,
            "\n## interface {} - usage `0x{:04X}:0x{:04X}`{}\n",
            collection.interface_number,
            collection.usage_page,
            collection.usage,
            if collection.is_vendor_defined() {
                " (vendor-defined)"
            } else {
                ""
            }
        );
        let _ = writeln!(
            out,
            "- manufacturer: {}",
            collection.manufacturer.as_deref().unwrap_or("(none)")
        );
        let _ = writeln!(
            out,
            "- product: {}",
            collection.product.as_deref().unwrap_or("(none)")
        );
        let _ = writeln!(
            out,
            "- serial: {}",
            match (&collection.serial_number, include_serial) {
                (None, _) => "(none)".to_string(),
                (Some(serial), true) => serial.clone(),
                (Some(_), false) => "present, withheld from the report".to_string(),
            }
        );
        let _ = writeln!(out, "- release: `0x{:04X}`", collection.release_number);
        if let Some(error) = &access.open_error {
            let _ = writeln!(out, "- open failed: `{error}`");
        }
        if let Some(error) = &access.descriptor_error {
            let _ = writeln!(out, "- descriptor failed: `{error}`");
        }
        if let Some(bytes) = &access.report_descriptor {
            let _ = writeln!(out, "\n```text");
            for report in parse_reports(bytes) {
                let _ = writeln!(out, "{}", report.describe());
            }
            let _ = writeln!(out, "```\n");
            let _ = writeln!(
                out,
                "<details><summary>report descriptor ({} bytes)</summary>\n",
                bytes.len()
            );
            let _ = writeln!(out, "```text");
            for chunk in bytes.chunks(16) {
                let _ = writeln!(out, "{}", hex(chunk));
            }
            let _ = writeln!(out, "```\n\n</details>");
        }
    }
    out
}

fn write_artifact(path: &str, contents: &str) {
    if let Some(parent) = std::path::Path::new(path).parent() {
        if let Err(error) = std::fs::create_dir_all(parent) {
            eprintln!("could not create {}: {error}", parent.display());
            std::process::exit(1);
        }
    }
    match std::fs::write(path, contents) {
        Ok(()) => println!("wrote {path}"),
        Err(error) => {
            eprintln!("could not write {path}: {error}");
            std::process::exit(1);
        }
    }
}
