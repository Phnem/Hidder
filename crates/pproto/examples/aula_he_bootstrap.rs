//! Bootstrap probes for the two `aula-bytech` actuation reads.
//!
//! ```text
//! cargo run -p pproto --example aula_he_bootstrap
//! cargo run -p pproto --example aula_he_bootstrap -- --confirm-probe-travel-precision
//! cargo run -p pproto --example aula_he_bootstrap -- --confirm-probe-key-travel
//! ```
//!
//! Two commands, two flags, two runs. Never both in one invocation, and there is
//! no flag that means "do everything": one explicit act by a person authorises
//! one probe, and this file has no way to express anything else. The gate it
//! builds is consumed by the send, so even within one run there is no second.
//!
//! Order matters. A travel value is a count of steps whose size only the device
//! knows, so the precision read comes first; without it the second read produces
//! numbers that cannot honestly be called millimetres.
//!
//! Developer mechanism. Production confirmation semantics belong to TICKET-16.

use std::time::Duration;

use pcaps::{Confidence, FamilyConfidence};
use pproto::aula_bytech_engine::AulaBytechEngine;
use pproto::aula_bytech_he::{
    TravelPrecisionProbe, TravelScale, WASD_KEY_IDS, WASD_KEY_LABELS, WasdTravelProbe,
};
use pproto::aula_bytech_io::{Exchange, Verdict};
use psafety::MonotonicClock;
use psafety::journal::{JournalEntry, JournalSink};
use psafety::probe::{ProbeError, ProbeGate};
use psafety::rate::UserConfirmation;
use ptransport::{DeviceId, Hid, HidCollection, ProbeChannel, ReportChannel};

const VENDOR_ID: u16 = 0x372E;
const PRODUCT_ID: u16 = 0x103E;
const TIMEOUT: Duration = Duration::from_millis(1500);

const PRECISION_FLAG: &str = "--confirm-probe-travel-precision";
const TRAVEL_FLAG: &str = "--confirm-probe-key-travel";

/// Which single probe this run is authorised to send.
#[derive(Clone, Copy, PartialEq, Eq)]
enum Which {
    Precision,
    KeyTravel,
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let precision = args.iter().any(|a| a == PRECISION_FLAG);
    let travel = args.iter().any(|a| a == TRAVEL_FLAG);

    let which = match (precision, travel) {
        (false, false) => None,
        (true, false) => Some(Which::Precision),
        (false, true) => Some(Which::KeyTravel),
        (true, true) => {
            eprintln!(
                "\n  STOPPED: both flags given. One confirmation authorises one probe, so this \
                 program will not send two in a run. Give one flag, then the other."
            );
            std::process::exit(1);
        }
    };

    if let Err(error) = run(which) {
        eprintln!("\n  STOPPED: {error}");
        std::process::exit(1);
    }
}

fn run(which: Option<Which>) -> Result<(), String> {
    println!("== bootstrap probes awaiting a first exchange ==");
    for id in psafety::ProbeCommandId::all() {
        println!("  {}::{}", id.family(), id.name());
    }

    let Some(which) = which else {
        println!("\n  dry run: no handle opened, nothing sent.");
        println!("  to send one probe, re-run with exactly one of:");
        println!("    {PRECISION_FLAG}");
        println!("    {TRAVEL_FLAG}   (needs the precision first to mean anything)");
        return Ok(());
    };

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
    let mut channel = ProbeChannel::open(&hid, config, device)
        .map_err(|e| format!("cannot open the config endpoint: {e}"))?;
    println!(
        "\n  report id     {}  (from the descriptor)",
        channel.report_id()
    );

    let mut exchange = Exchange::new(&mut channel, TIMEOUT);
    let gate = ProbeGate::new(&mut exchange, PrintingJournal, MonotonicClock::default());
    // Minted once. There is no second one in this program.
    let confirmation = UserConfirmation::given();
    let family = AulaBytechEngine::family();
    let identified = FamilyConfidence::established(Confidence::Verified);

    let report = match which {
        Which::Precision => {
            println!("\n== probe: read_travel_precision (0x82:0x08) ==");
            gate.probe::<TravelPrecisionProbe>(device, family, identified, confirmation)
                .map(describe_precision)
                .map_err(describe_error)
        }
        Which::KeyTravel => {
            println!("\n== probe: read_key_travel (0x93:0x00, layer Normal, system Windows) ==");
            println!(
                "  keys          {}  (protocol key ids {}, NOT HID usages)",
                WASD_KEY_LABELS.join(" "),
                WASD_KEY_IDS
                    .iter()
                    .map(|id| id.raw().to_string())
                    .collect::<Vec<_>>()
                    .join(" ")
            );
            gate.probe::<WasdTravelProbe>(device, family, identified, confirmation)
                .map(describe_travel)
                .map_err(describe_error)
        }
    };

    println!("\n== transcript ==");
    let transcript = exchange.transcript();
    println!("  -> {}", hex(&transcript.sent));
    if transcript.seen.is_empty() {
        println!("  <- nothing within the window");
    }
    for (received, verdict) in &transcript.seen {
        let label = match verdict {
            Verdict::UnsolicitedEvent => "unsolicited event, ignored",
            Verdict::CandidateAnswer => "candidate answer",
        };
        println!(
            "  <- {:>6.1} ms  [{label}]  {}",
            received.after.as_secs_f64() * 1000.0,
            hex(&received.bytes)
        );
    }

    let text = report?;
    println!("\n== decoded ==\n{text}");
    println!("\n  Sent one command. Sending nothing else.");
    Ok(())
}

fn describe_precision(probe: TravelPrecisionProbe) -> String {
    let mut out = format!(
        "  raw byte      {}\n  scale         {}\n  step          {} mm\n  checksum      {}\n",
        probe.raw,
        probe.scale,
        probe.scale.step_mm(),
        checksum_note(probe.checksum_ok),
    );
    if probe.echoed_request {
        out.push_str(
            "\n  NOTE: the answer was byte-identical to the request. On boards in this\n  \
             class that is what an unsupported command looks like, and with an empty\n  \
             payload it is indistinguishable from a genuine \"no precision reported\".\n  \
             Either way the vendor's own software reads this same byte and falls back\n  \
             to 0.01 mm, so its display agrees with ours. This exchange does NOT\n  \
             establish that the device supports this command.",
        );
    }
    out
}

fn describe_travel(probe: WasdTravelProbe) -> String {
    let mut out = String::new();
    out.push_str("  key   raw     mm (vendor fallback scale, 0.01 mm/step)\n");
    for (travel, name) in probe.travels.iter().zip(WASD_KEY_LABELS) {
        out.push_str(&format!(
            "  {name:<5} {:<7} {:.2}\n",
            travel.raw,
            travel.to_millimetres(TravelScale::VendorFallback),
        ));
    }
    out.push_str(&format!(
        "  checksum      {}\n",
        checksum_note(probe.checksum_ok)
    ));
    out.push_str(
        "\n  The scale is the vendor's documented fallback, because this board\n  \
         reported no precision of its own. That is what the official configurator\n  \
         computes with too, so these figures should match what it displays.",
    );
    out
}

fn checksum_note(ok: bool) -> &'static str {
    if ok {
        "matches our computation"
    } else {
        "DIFFERS from our computation (recorded, not enforced)"
    }
}

fn describe_error<R: std::fmt::Display>(error: ProbeError<R>) -> String {
    match error {
        ProbeError::Rejected(rejection) => format!(
            "the device answered and the answer failed validation: {rejection}. \
             Do not adjust the request and do not try a neighbouring subcommand."
        ),
        ProbeError::Transport(error) if error.is_stall() => format!(
            "the endpoint stalled: {error}. Unplug and reconnect the board before anything else."
        ),
        ProbeError::Transport(error) => format!("transport: {error}"),
        ProbeError::Refused(refusal) => format!("refused before dispatch: {refusal:?}"),
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

fn hex(bytes: &[u8]) -> String {
    bytes
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect::<Vec<_>>()
        .join(" ")
}
