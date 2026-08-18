//! Prints what the registry makes of each captured device, axis by axis.
//!
//! ```text
//! cargo run -p pregistry --example identify
//! ```
//!
//! Reads the capture files in `docs/hardware/` and nothing else. It touches no
//! hardware, opens no device and sends nothing: the point of this tool is to
//! show what can be concluded from enumeration alone.

use std::path::Path;

use pregistry::{
    CollectionObservation, DeviceObservation, FamilyReason, Identification, Registry, SignalState,
};
use serde_json::Value;

fn main() {
    // No panics in this tool, deliberately: the house style for the dev tools
    // in this workspace is that they report and exit rather than unwind, and a
    // tool that panics on a malformed file teaches nothing about the file.
    let Some(root) = Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(Path::parent)
        .map(|root| root.join("docs/hardware"))
    else {
        eprintln!("cannot locate the repository root from the manifest directory");
        std::process::exit(1);
    };

    let captures = [
        ("AULA Hero 84 HE", "aula-hero-84-he.json"),
        ("VXE wired", "vxe-dragonfly-r1-se-plus-wired.json"),
        ("VXE receiver", "vxe-dragonfly-r1-se-plus-24ghz.json"),
    ];

    let registry = Registry::builtin();
    println!("registry: {} device(s)\n", registry.entries().len());

    for (label, file) in captures {
        let path = root.join(file);
        let text = match std::fs::read_to_string(&path) {
            Ok(text) => text,
            Err(error) => {
                eprintln!("cannot read {}: {error}", path.display());
                std::process::exit(1);
            }
        };
        let observation = match observe(&text) {
            Ok(observation) => observation,
            Err(error) => {
                eprintln!("{}: {error}", path.display());
                std::process::exit(1);
            }
        };
        report(label, &observation, &registry.identify(&observation));
    }
}

fn report(label: &str, observation: &DeviceObservation, identified: &Identification) {
    println!("=== {label} ===");
    println!(
        "  observed          {:04X}:{:04X} rel {:04X}  {:?} / {:?}",
        observation.vendor_id,
        observation.product_id,
        observation.release,
        observation.manufacturer.as_deref().unwrap_or("-"),
        observation.product.as_deref().unwrap_or("-"),
    );

    let structural = &identified.structural;
    println!(
        "  structural        {}  [{}]  shared by: {}",
        structural.id,
        structural.confidence.as_str(),
        if structural.matches.is_empty() {
            "nothing in the registry".to_owned()
        } else {
            structural.matches.join(", ")
        }
    );
    println!("                    {}", signals(&structural.signals));

    let product = &identified.product;
    println!(
        "  product           {}  [{}]",
        product.entry.unwrap_or("unrecognised"),
        product.confidence.as_str()
    );
    println!("                    {}", signals(&product.signals));

    let family = &identified.family;
    println!(
        "  protocol family   {}  [{}]  ({})",
        family.family.unwrap_or("unknown"),
        family.confidence.as_str(),
        match family.reason {
            FamilyReason::NoProductMatch => "no product matched, so nothing to look one up against",
            FamilyReason::NotRecorded => "product known, family never established",
            FamilyReason::FromRegistry => "claimed by the registry",
        }
    );
    println!(
        "  write permitted   {}\n",
        if identified.permits_write() {
            "yes"
        } else {
            "no"
        }
    );
}

fn signals(outcomes: &[pregistry::SignalOutcome]) -> String {
    outcomes
        .iter()
        .map(|outcome| {
            let mark = match outcome.state {
                SignalState::Matched => "=",
                SignalState::Differed => "x",
                SignalState::Absent => "-",
            };
            format!("{mark}{}", outcome.signal.as_str())
        })
        .collect::<Vec<_>>()
        .join("  ")
}

fn hex16(value: &Value) -> u16 {
    let text = value.as_str().unwrap_or("0x0000");
    u16::from_str_radix(text.trim_start_matches("0x").trim_start_matches("0X"), 16).unwrap_or(0)
}

fn observe(capture: &str) -> Result<DeviceObservation, String> {
    let doc: Value = serde_json::from_str(capture).map_err(|e| e.to_string())?;
    let collections = doc["collections"]
        .as_array()
        .ok_or("the capture has no collections array")?;
    let first = collections
        .first()
        .ok_or("the capture has no collections")?;

    let mut interfaces: Vec<u64> = collections
        .iter()
        .filter_map(|c| c["interface_number"].as_u64())
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
                    .map(|bits| u16::try_from(bits / 8).unwrap_or(u16::MAX))
                    .unwrap_or(0)
            };
            CollectionObservation {
                interface: u8::try_from(c["interface_number"].as_u64().unwrap_or(0)).unwrap_or(0),
                usage_page: hex16(&c["usage_page"]),
                usage: hex16(&c["usage"]),
                descriptor_fnv1a64: u64::from_str_radix(
                    c["report_descriptor_fnv1a64"].as_str().unwrap_or("0"),
                    16,
                )
                .unwrap_or(0),
                report_id: report
                    .and_then(|r| r["numbered"].as_bool())
                    .unwrap_or(false)
                    .then(|| {
                        u8::try_from(report.and_then(|r| r["report_id"].as_u64()).unwrap_or(0))
                            .unwrap_or(0)
                    }),
                input_bytes: bytes("input_bits"),
                output_bytes: bytes("output_bits"),
                feature_bytes: bytes("feature_bits"),
            }
        })
        .collect();

    Ok(DeviceObservation::from_enumeration(
        hex16(&doc["vendor_id"]),
        hex16(&doc["product_id"]),
        first["manufacturer"].as_str().map(str::to_owned),
        first["product"].as_str().map(str::to_owned),
        hex16(&first["release_number"]),
        first["serial_number_present"].as_bool().unwrap_or(false),
        u8::try_from(interfaces.len()).unwrap_or(0),
        observed,
    ))
}
