//! Reads the model id off an AULA `aula-bytech` board through the production
//! path, with no bootstrap tooling anywhere in it.
//!
//! ```text
//! cargo run -p pproto --example aula_read_model_id
//! ```
//!
//! # Why this file exists
//!
//! `pcore` does not orchestrate anything yet, so this stands in for the caller
//! it will eventually be. Everything below it is real: the same engine, the same
//! gate, the same `SafeCommandId`, the same adapter and the same transport the
//! product will use.
//!
//! There is no confirmation flag and no dry-run switch, and that is the point.
//! `read_model_id` is a `safe_read` with measured timing, so it is an ordinary
//! read: the gate throttles it, the journal records it, and nobody has to be
//! asked. Compare the bootstrap tooling it replaced, which needed an explicit
//! human act for a single send -- that tooling is gone, deliberately, because
//! the command it existed to bootstrap no longer needs bootstrapping.

use std::time::Duration;

use pcaps::{Confidence, FamilyConfidence};
use pproto::aula_bytech_engine::AulaBytechEngine;
use pproto::aula_bytech_io::{Exchange, Verdict};
use pregistry::{CollectionObservation, DeviceObservation, Registry};
use psafety::journal::{JournalEntry, JournalSink};
use psafety::{MonotonicClock, ReadError, SafetyGate};
use ptransport::{DeviceId, Hid, HidCollection, ProbeChannel};

/// Only to *find* a candidate board. Nine models share this pair, which is
/// precisely why the read below exists.
const VENDOR_ID: u16 = 0x372E;
const PRODUCT_ID: u16 = 0x103E;

/// Read window for one exchange. Generous: the measured answers came back in
/// under 3 ms, and there is no retry behind this.
const TIMEOUT: Duration = Duration::from_millis(1500);

fn main() {
    if let Err(error) = run() {
        eprintln!("\n  STOPPED: {error}");
        std::process::exit(1);
    }
}

fn run() -> Result<(), String> {
    let hid = Hid::open().map_err(|e| format!("cannot reach the HID backend: {e}"))?;
    let collections = hid.enumerate();

    // The engine says which collection it needs. It does not say which report
    // id: that comes off the board's own descriptor, below.
    let endpoint = AulaBytechEngine::config_endpoint();
    let config: &HidCollection = collections
        .iter()
        .find(|c| {
            c.vendor_id == VENDOR_ID
                && c.product_id == PRODUCT_ID
                && c.usage_page == endpoint.usage_page
                && c.usage == endpoint.usage
        })
        .ok_or_else(|| {
            format!(
                "no {VENDOR_ID:04X}:{PRODUCT_ID:04X} board exposing {:04X}:{:04X} is connected",
                endpoint.usage_page, endpoint.usage
            )
        })?;

    println!("== device ==");
    println!(
        "  {:04X}:{:04X}  {:?} / {:?}",
        config.vendor_id,
        config.product_id,
        config.manufacturer.as_deref().unwrap_or("-"),
        config.product.as_deref().unwrap_or("-"),
    );
    println!(
        "  endpoint      {:04X}:{:04X}  (asked for by the engine)",
        endpoint.usage_page, endpoint.usage
    );

    let device = DeviceId::new(u64::from(VENDOR_ID) << 16 | u64::from(PRODUCT_ID));
    let mut channel = ProbeChannel::open(&hid, config, device)
        .map_err(|e| format!("cannot open the config endpoint: {e}"))?;
    println!(
        "  report id     {}  (derived from this board's descriptor, not configured)",
        channel.report_id()
    );

    let mut exchange = Exchange::new(&mut channel, TIMEOUT);
    let mut gate = SafetyGate::new(&mut exchange, PrintingJournal, MonotonicClock::default());

    // What identified the device. In the product this comes from `pregistry`;
    // here it is stated so the gate has something to check the command's family
    // against. A read does not require a verified family -- only a write does.
    gate.identify_device(
        device,
        AulaBytechEngine::family(),
        FamilyConfidence::established(Confidence::Verified),
    );

    println!("\n== read ==");
    let result = AulaBytechEngine::read_model_id(&mut gate, device, None);

    println!("\n== transcript ==");
    let transcript = exchange.transcript();
    println!("  -> {}", hex(&transcript.sent));
    for (report, verdict) in &transcript.seen {
        let label = match verdict {
            Verdict::UnsolicitedEvent => "unsolicited event, ignored",
            Verdict::CandidateAnswer => "candidate answer",
        };
        println!(
            "  <- {:>6.1} ms  [{label}]  {}",
            report.after.as_secs_f64() * 1000.0,
            hex(&report.bytes)
        );
    }

    match result {
        Ok(model_id) => {
            println!("\n== model id ==");
            println!("  {model_id}");
            println!("  series        {:#04x}", model_id.series());
            println!("  index         {:#04x}", model_id.index());

            // The exchange happened, so the family axis now has real evidence
            // to be identified with. Attached *after* the read, not before:
            // this value asserts that this board answered, and before the read
            // it had not.
            let observation = observe(&hid, VENDOR_ID, PRODUCT_ID, &collections);
            let identified = Registry::builtin().identify_with(
                &observation,
                Some(AulaBytechEngine::verified_exchange_evidence()),
            );
            println!("\n== identity, with the exchange counted ==");
            println!(
                "  structural    {} [{}]",
                identified.structural.id,
                identified.structural.confidence.as_str()
            );
            println!(
                "  product       {} [{}]",
                identified.product.entry.unwrap_or("unrecognised"),
                identified.product.confidence.as_str()
            );
            println!(
                "  family        {} [{}]  via {:?}",
                identified.family.family.unwrap_or("unknown"),
                identified.family.confidence.as_str(),
                identified.family.reason
            );
            println!(
                "\n  The family is verified for this board through one command.\n  \
                 It is not verified for the other eight models sharing this id,\n  \
                 and no other command in the family is verified at all."
            );
            Ok(())
        }
        Err(ReadError::Rejected(rejection)) => {
            Err(format!("the answer failed validation: {rejection}"))
        }
        Err(ReadError::Transport(error)) if error.is_stall() => Err(format!(
            "the endpoint stalled: {error}. Unplug and reconnect the board."
        )),
        Err(ReadError::Transport(error)) => Err(format!("transport: {error}")),
        Err(ReadError::Refused(refusal)) => Err(format!("refused: {refusal:?}")),
    }
}

struct PrintingJournal;

impl JournalSink for PrintingJournal {
    fn record(&mut self, entry: JournalEntry) {
        println!(
            "  journal       {}::{} class={} intent={:?} payload_len={} outcome={:?}",
            entry.family,
            entry.command,
            entry.class.as_str(),
            entry.intent,
            entry.payload_len,
            entry.outcome,
        );
    }
}

/// Builds the enumeration-only observation the registry matches against.
fn observe(
    hid: &Hid,
    vendor_id: u16,
    product_id: u16,
    collections: &[HidCollection],
) -> DeviceObservation {
    let ours: Vec<&HidCollection> = collections
        .iter()
        .filter(|c| c.vendor_id == vendor_id && c.product_id == product_id)
        .collect();
    let first = ours[0];
    let mut interfaces: Vec<i32> = ours.iter().map(|c| c.interface_number).collect();
    interfaces.sort_unstable();
    interfaces.dedup();

    let observed = ours
        .iter()
        .map(|c| CollectionObservation {
            interface: u8::try_from(c.interface_number).unwrap_or(0),
            usage_page: c.usage_page,
            usage: c.usage,
            descriptor_fnv1a64: fnv1a64(&hid.inspect(c).report_descriptor.unwrap_or_default()),
            report_id: None,
            input_bytes: 0,
            output_bytes: 0,
            feature_bytes: 0,
        })
        .collect();

    DeviceObservation::from_enumeration(
        first.vendor_id,
        first.product_id,
        first.manufacturer.clone(),
        first.product.clone(),
        first.release_number,
        first.serial_number.is_some(),
        u8::try_from(interfaces.len()).unwrap_or(0),
        observed,
    )
}

fn fnv1a64(bytes: &[u8]) -> u64 {
    let mut hash: u64 = 0xcbf2_9ce4_8422_2325;
    for &byte in bytes {
        hash ^= u64::from(byte);
        hash = hash.wrapping_mul(0x0000_0100_0000_01b3);
    }
    hash
}

fn hex(bytes: &[u8]) -> String {
    bytes
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect::<Vec<_>>()
        .join(" ")
}
