//! Timing characterisation for `read_key_travel`, as a fixed script.
//!
//! ```text
//! cargo run -p pproto --example aula_key_travel_timing -- --confirm-timing-run
//! ```
//!
//! # Why this exists and why it is not a capability
//!
//! A `safe_read` must resolve to a measured cadence -- `psafety` has a test that
//! refuses one that cannot be throttled -- and a cadence is a measurement, so
//! something has to do the measuring. This program is that something, and it is
//! deliberately shaped as a script rather than as a feature:
//!
//! - the number of exchanges, the interval and the command are all fixed here;
//! - it stops at the first answer that differs from the first one;
//! - it sends nothing else, and there is no flag that makes it send anything
//!   else.
//!
//! What a successful run licenses is one sentence, and specifically not a claim
//! about the device's real minimum interval, which nobody is measuring and
//! nobody is looking for: **Peripheral allows this command at most once per
//! second, and that regime has been exercised on this board.**
//!
//! Modelled on the run that earned `read_model_id` its number
//! (`docs/hardware/aula-bytech-exchange-002-timing.md`). Developer mechanism;
//! production confirmation semantics belong to TICKET-16.

use std::time::{Duration, Instant};

use pcaps::{Confidence, FamilyConfidence};
use pproto::aula_bytech_engine::AulaBytechEngine;
use pproto::aula_bytech_he::{TravelScale, WASD_KEY_LABELS, WasdTravelRead};
use pproto::aula_bytech_io::Exchange;
use psafety::journal::{JournalEntry, JournalSink};
use psafety::{MonotonicClock, SafetyGate};
use ptransport::{DeviceId, Hid, HidCollection, ProbeChannel, ReportChannel};

const VENDOR_ID: u16 = 0x372E;
const PRODUCT_ID: u16 = 0x103E;
const TIMEOUT: Duration = Duration::from_millis(1500);

/// Fixed. Five is what `read_model_id` was characterised with, and matching it
/// keeps the two measurements comparable.
const EXCHANGES: usize = 5;

/// The ceiling being exercised, not a minimum being searched for.
const INTERVAL: Duration = Duration::from_millis(1000);

const FLAG: &str = "--confirm-timing-run";

fn main() {
    if !std::env::args().any(|a| a == FLAG) {
        println!("Timing characterisation for aula-bytech::read_key_travel.");
        println!(
            "  {EXCHANGES} read-only exchanges, at least {} ms apart, stopping on any mismatch.",
            INTERVAL.as_millis()
        );
        println!("  Nothing is written to the device, here or anywhere in this program.");
        println!("\n  dry run: no handle opened, nothing sent.");
        println!("  to run it: cargo run -p pproto --example aula_key_travel_timing -- {FLAG}");
        return;
    }
    if let Err(error) = run() {
        eprintln!("\n  STOPPED: {error}");
        std::process::exit(1);
    }
}

fn run() -> Result<(), String> {
    let hid = Hid::open().map_err(|e| format!("cannot reach the HID backend: {e}"))?;
    let collections = hid.enumerate();
    let endpoint = AulaBytechEngine::config_endpoint();
    let config: &HidCollection = collections
        .iter()
        .find(|c| {
            c.vendor_id == VENDOR_ID
                && c.product_id == PRODUCT_ID
                && c.usage_page == endpoint.usage_page
                && c.usage == endpoint.usage
        })
        .ok_or("no HERO 84 HE exposing the aula-bytech config endpoint is connected")?;

    let device = DeviceId::new(u64::from(VENDOR_ID) << 16 | u64::from(PRODUCT_ID));
    let channel = ProbeChannel::open(&hid, config, device)
        .map_err(|e| format!("cannot open the config endpoint: {e}"))?;
    println!(
        "  report id     {}  (from the descriptor)",
        channel.report_id()
    );
    println!(
        "\n== {EXCHANGES} exchanges of read_key_travel at >= {} ms ==",
        INTERVAL.as_millis()
    );

    let family = AulaBytechEngine::family();
    let identified = FamilyConfidence::established(Confidence::Verified);
    let mut first: Option<Vec<(u16, u16)>> = None;

    // One gate for the whole run, owning the channel. That is not tidiness: a
    // gate rebuilt per exchange would start each one with an empty cadence
    // history, and the limiter this program exists to characterise would be
    // permitting everything while the numbers were being taken.
    let mut gate = SafetyGate::new(
        Exchange::new(channel, TIMEOUT),
        SilentJournal,
        MonotonicClock::default(),
    );
    gate.identify_device(device, family, identified);

    for exchange in 1..=EXCHANGES {
        if exchange > 1 {
            // Slightly over the declared interval, so the run measures the
            // device rather than racing our own limiter.
            std::thread::sleep(INTERVAL + Duration::from_millis(100));
        }
        let started = Instant::now();
        let read = gate
            .read::<WasdTravelRead>(device, None)
            .map_err(|e| format!("exchange {exchange} failed: {e}"))?;
        let elapsed = started.elapsed();

        let observed: Vec<(u16, u16)> = read
            .travels
            .iter()
            .map(|t| (t.key_id.raw(), t.raw))
            .collect();

        let rendered = read
            .travels
            .iter()
            .zip(WASD_KEY_LABELS)
            .map(|(t, name)| {
                format!(
                    "{name}={:.2}",
                    t.to_millimetres(TravelScale::VendorFallback)
                )
            })
            .collect::<Vec<_>>()
            .join(" ");
        println!(
            "  {exchange}/{EXCHANGES}  {:>5.1} ms  checksum {}  {rendered}",
            elapsed.as_secs_f64() * 1000.0,
            if read.checksum_ok { "ok" } else { "DIFFERS" },
        );

        match &first {
            None => first = Some(observed),
            Some(expected) if *expected == observed => {}
            Some(expected) => {
                return Err(format!(
                    "exchange {exchange} disagrees with the first: {observed:?} vs {expected:?}. \
                     Stopping rather than averaging -- a board that answers differently to the \
                     same question at the same cadence has not been characterised."
                ));
            }
        }
    }

    println!("\n  All {EXCHANGES} answers identical, no stall, nothing written.");
    println!("  This licenses one sentence: Peripheral allows read_key_travel at most");
    println!("  once per second, and that regime was exercised here. It is NOT a claim");
    println!("  about the device's real minimum interval.");
    Ok(())
}

/// The journal is exercised elsewhere; five identical entries here would only
/// bury the numbers this program exists to print.
struct SilentJournal;

impl JournalSink for SilentJournal {
    fn record(&mut self, _entry: JournalEntry) {}
}
