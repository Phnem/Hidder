//! The first hardware exchange with the AULA HERO 84 HE: one command, once.
//!
//! ```text
//! cargo run -p pproto --example aula_probe -- --dry-run   # builds and prints, sends nothing
//! cargo run -p pproto --example aula_probe -- --send      # asks, then sends once
//! ```
//!
//! # What this is
//!
//! The bootstrap path from `psafety::probe`, wired to real hardware for the one
//! command the ACL classifies as `bootstrap_probe`. Every constraint that path
//! promises is structural here rather than remembered:
//!
//! - the command comes from `ModelIdProbe::COMMAND`, not from a flag;
//! - the gate is consumed by the probe, so this program cannot send twice;
//! - the confirmation is read from a person before the gate is built;
//! - the report id is derived from the board's own descriptor;
//! - there is no retry, no second command, and no fallback opcode.
//!
//! `--dry-run` is the default and does everything except open a handle: it
//! resolves the command, builds the frame and prints it. Sending requires
//! `--send` *and* a typed confirmation.

use std::time::{Duration, Instant};

use pcaps::{Confidence, FamilyConfidence};
use pproto::aula_bytech::{self, ModelIdProbe};
use pregistry::{CollectionObservation, DeviceObservation, Registry};
use psafety::journal::{JournalEntry, JournalSink};
use psafety::probe::{AuthorizedProbe, ProbeError, ProbeGate, ProbeResponse, ProbeSink};
use psafety::rate::UserConfirmation;
use psafety::{Clock, MonotonicClock};
use ptransport::{DeviceId, Hid, HidCollection, ProbeChannel, TransportError};

/// Our board, from TICKET-08. Used to *find* the collection, never to conclude
/// anything: nine models share this pair, which is the whole reason the command
/// below exists.
const VENDOR_ID: u16 = 0x372E;
const PRODUCT_ID: u16 = 0x103E;
const USAGE_PAGE: u16 = 0xFF60;
const USAGE: u16 = 0x0061;

/// The family the ACL entry belongs to.
const FAMILY: &str = "aula-bytech";

/// One request, then this long, then stop. The vendor's wired transport uses
/// 1000 ms; this is longer because there is no retry behind it, so a marginal
/// timeout costs the reading rather than triggering a resend.
const TIMEOUT: Duration = Duration::from_millis(1500);

fn main() {
    let send = std::env::args().any(|arg| arg == "--send");
    if let Err(error) = run(send) {
        eprintln!("\n  STOPPED: {error}");
        std::process::exit(1);
    }
}

fn run(send: bool) -> Result<(), String> {
    let id = ModelIdProbe::COMMAND;
    println!("== command ==");
    println!("  family        {}", id.family());
    println!("  name          {}", id.name());
    println!("  class         {}", id.class().as_str());

    let hid = Hid::open().map_err(|e| format!("cannot reach the HID backend: {e}"))?;
    let collections = hid.enumerate();

    // --- step 1-2: exactly one known board, and the matcher agrees ---------
    let ours: Vec<&HidCollection> = collections
        .iter()
        .filter(|c| c.vendor_id == VENDOR_ID && c.product_id == PRODUCT_ID)
        .collect();
    if ours.is_empty() {
        return Err(format!(
            "no {VENDOR_ID:04X}:{PRODUCT_ID:04X} device is connected"
        ));
    }
    let serials: Vec<&str> = {
        let mut s: Vec<&str> = ours
            .iter()
            .filter_map(|c| c.serial_number.as_deref())
            .collect();
        s.sort_unstable();
        s.dedup();
        s
    };
    if serials.len() > 1 {
        return Err(format!(
            "{} physically distinct boards share this id; connect exactly one",
            serials.len()
        ));
    }

    let observation = observe(&hid, &ours);
    let identified = Registry::builtin().identify(&observation);
    println!("\n== identity ==");
    println!(
        "  observed      {:04X}:{:04X} rel {:04X}  {:?} / {:?}",
        observation.vendor_id,
        observation.product_id,
        observation.release,
        observation.manufacturer.as_deref().unwrap_or("-"),
        observation.product.as_deref().unwrap_or("-"),
    );
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
        "  family        {} [{}]",
        identified.family.family.unwrap_or("unknown"),
        identified.family.confidence.as_str()
    );

    let product = identified
        .product
        .entry
        .ok_or("the matcher does not recognise this product; nothing may be sent to it")?;
    if identified.product.confidence < Confidence::High {
        return Err(format!(
            "product {product} matched only at {}; the probe wants the product settled first",
            identified.product.confidence.as_str()
        ));
    }

    // --- step 3-4: the right collection, and its own report id ------------
    let config = ours
        .iter()
        .find(|c| c.usage_page == USAGE_PAGE && c.usage == USAGE)
        .ok_or_else(|| format!("this board exposes no {USAGE_PAGE:04X}:{USAGE:04X} collection"))?;
    println!("\n== endpoint ==");
    println!("  usage         {USAGE_PAGE:04X}:{USAGE:04X}");
    println!("  interface     {}", config.interface_number);

    let device_id = DeviceId::new(u64::from(VENDOR_ID) << 16 | u64::from(PRODUCT_ID));

    // The frame can be built without a handle, so build and show it first: a
    // dry run has to be able to show exactly what a real run would send.
    let frame = aula_bytech::encode_request(id_key(), &ModelIdProbe::request_payload(), 9)
        .map_err(|e| format!("cannot build the request: {e}"))?;
    println!("\n== request (report id 9 assumed for this preview) ==");
    print_frame(&frame);

    if !send {
        println!("\n  dry run: no handle opened, nothing sent. Re-run with --send.");
        return Ok(());
    }

    let mut channel = ProbeChannel::open(&hid, config, device_id)
        .map_err(|e| format!("cannot open the config endpoint: {e}"))?;
    let report_id = channel.report_id();
    println!("\n  report id     {report_id} (derived from the descriptor)");
    if report_id != 9 {
        return Err(format!(
            "the descriptor yields report id {report_id}; TICKET-08 recorded 9, so something is not the board this was written for"
        ));
    }

    // --- step 6: a person, once -------------------------------------------
    println!("\n== about to send ==");
    println!("  one output report, {} bytes plus the report id", frame.len());
    println!("  one read window of {} ms, no retry, no second command", TIMEOUT.as_millis());
    println!("\n  Type exactly: send one probe");
    let mut answer = String::new();
    std::io::stdin()
        .read_line(&mut answer)
        .map_err(|e| format!("cannot read the confirmation: {e}"))?;
    if answer.trim() != "send one probe" {
        return Err("not confirmed; nothing was sent".into());
    }
    let confirmation = UserConfirmation::given();

    // --- steps 7-13: one exchange through the probe gate -------------------
    let transcript = Transcript::default();
    let sink = ChannelSink {
        channel: &mut channel,
        report_id,
        transcript: transcript.clone(),
    };
    let journal = PrintingJournal;
    let gate = ProbeGate::new(sink, journal, MonotonicClock::default());

    let started = Instant::now();
    let outcome = gate.probe::<ModelIdProbe>(
        device_id,
        FAMILY,
        // What the matcher concluded about the family from enumeration alone.
        // Deliberately not Verified: verifying it is what this call is for.
        FamilyConfidence::established(Confidence::Candidate),
        confirmation,
    );
    let elapsed = started.elapsed();

    println!("\n== transcript ==");
    transcript.print();
    println!("  elapsed       {:.1} ms", elapsed.as_secs_f64() * 1000.0);

    match outcome {
        Ok(probe) => {
            println!("\n== decoded ==");
            println!("  model id      {}", probe.model_id);
            println!(
                "  bytes         {}",
                hex(probe.model_id.to_be_bytes().as_slice())
            );
            println!("  series        {:#04x}", probe.model_id.series());
            println!("  index         {:#04x}", probe.model_id.index());
            println!(
                "  checksum      {}",
                if probe.checksum_ok {
                    "matches our computation"
                } else {
                    "DIFFERS from our computation (recorded, not enforced)"
                }
            );
            println!("\n== prediction ==");
            if probe.model_id.series() == 0x11 {
                println!("  MET: series 0x11, the wired series in the vendor's own table.");
            } else {
                println!(
                    "  NOT MET: series {:#04x}, expected 0x11. The decode is unproven; stop here.",
                    probe.model_id.series()
                );
            }
            println!("\n  Sent one command. Sending nothing else.");
            Ok(())
        }
        Err(ProbeError::Rejected(rejection)) => Err(format!(
            "the device answered and the answer failed validation: {rejection}. \
             Do not adjust the request, do not try a neighbouring subcommand."
        )),
        Err(ProbeError::Transport(error)) => {
            if error.is_stall() {
                Err(format!(
                    "the endpoint stalled: {error}. Unplug and reconnect the board before anything else."
                ))
            } else {
                Err(format!("transport: {error}"))
            }
        }
        Err(ProbeError::Refused(refusal)) => Err(format!("refused before dispatch: {refusal:?}")),
    }
}

fn id_key() -> psafety::CommandKey {
    psafety::CommandKey::GroupSubcommand {
        group: 0x82,
        subcommand: 0x01,
    }
}

/// Everything that crossed the wire, so the report can quote it rather than
/// paraphrase it.
#[derive(Clone, Default)]
struct Transcript(std::rc::Rc<std::cell::RefCell<TranscriptData>>);

#[derive(Default)]
struct TranscriptData {
    sent: Vec<u8>,
    seen: Vec<(Duration, Vec<u8>, &'static str)>,
}

impl Transcript {
    fn print(&self) {
        let data = self.0.borrow();
        println!("  -> {} bytes", data.sent.len());
        println!("     {}", hex(&data.sent));
        if data.seen.is_empty() {
            println!("  <- nothing within the window");
        }
        for (after, bytes, verdict) in &data.seen {
            println!(
                "  <- {:>7.1} ms  {} bytes  [{verdict}]",
                after.as_secs_f64() * 1000.0,
                bytes.len()
            );
            println!("     {}", hex(bytes));
        }
    }
}

/// The sink: encodes with this family's codec, writes once, and reads until the
/// window closes, handing back the first report that is not an unsolicited
/// event. Deciding whether that report actually answers the request is the
/// decoder's job, not this one's.
struct ChannelSink<'a> {
    channel: &'a mut ProbeChannel,
    report_id: u8,
    transcript: Transcript,
}

impl ProbeSink for ChannelSink<'_> {
    fn dispatch_probe(
        &mut self,
        _device: DeviceId,
        probe: AuthorizedProbe,
    ) -> Result<Vec<u8>, TransportError> {
        let frame = aula_bytech::encode_request(probe.key(), probe.payload(), self.report_id)
            .map_err(|e| TransportError::Backend(e.to_string()))?;

        let sent = self.channel.write_report(&frame)?;
        self.transcript.0.borrow_mut().sent = sent;

        let deadline = Instant::now() + TIMEOUT;
        let mut answer = None;
        while Instant::now() < deadline && answer.is_none() {
            let remaining = deadline.saturating_duration_since(Instant::now());
            let Some(report) = self.channel.read_report(remaining)? else {
                break;
            };
            // The report id is byte zero on a numbered collection. Strip it so
            // the frame offsets mean what the codec says they mean.
            let body: Vec<u8> = match report.bytes.split_first() {
                Some((first, rest)) if *first == self.report_id => rest.to_vec(),
                _ => report.bytes.clone(),
            };
            let verdict = if aula_bytech::is_unsolicited(&body) {
                "unsolicited event, ignored"
            } else {
                answer = Some(body.clone());
                "candidate answer"
            };
            self.transcript
                .0
                .borrow_mut()
                .seen
                .push((report.after, report.bytes, verdict));
        }

        answer.ok_or(TransportError::Backend(
            "no candidate answer within the window".into(),
        ))
    }
}

struct PrintingJournal;

impl JournalSink for PrintingJournal {
    fn record(&mut self, entry: JournalEntry) {
        println!(
            "\n== journal ==\n  {} {}::{} class={} intent={:?} payload_len={} outcome={:?}",
            entry.at_ms,
            entry.family,
            entry.command,
            entry.class.as_str(),
            entry.intent,
            entry.payload_len,
            entry.outcome,
        );
    }
}

fn observe(hid: &Hid, ours: &[&HidCollection]) -> DeviceObservation {
    let first = ours[0];
    let mut interfaces: Vec<i32> = ours.iter().map(|c| c.interface_number).collect();
    interfaces.sort_unstable();
    interfaces.dedup();

    let collections = ours
        .iter()
        .map(|c| {
            let access = hid.inspect(c);
            let descriptor = access.report_descriptor.unwrap_or_default();
            CollectionObservation {
                interface: u8::try_from(c.interface_number).unwrap_or(0),
                usage_page: c.usage_page,
                usage: c.usage,
                descriptor_fnv1a64: fnv1a64(&descriptor),
                report_id: None,
                input_bytes: 0,
                output_bytes: 0,
                feature_bytes: 0,
            }
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
        collections,
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

fn print_frame(frame: &[u8]) {
    for (i, chunk) in frame.chunks(16).enumerate() {
        println!("  {:02}  {}", i * 16, hex(chunk));
    }
}

fn hex(bytes: &[u8]) -> String {
    bytes
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect::<Vec<_>>()
        .join(" ")
}

/// Unused, but it keeps the clock import honest if the example is trimmed.
#[allow(dead_code)]
fn now(clock: &MonotonicClock) -> u64 {
    clock.now_ms()
}
