//! Device emulator, as a thing a person can run and look at.
//!
//! The emulator's real job is done in tests -- `cargo test -p pemu` -- where the
//! production read path is driven against a device that does not exist. This
//! binary exists so that the fixture can be inspected by hand: what it claims to
//! be, what it answers, and where each of its bytes came from.
//!
//! It opens nothing, listens on nothing, and cannot reach a real device.

use std::time::Duration;

use pemu::aula::{CONFIG_USAGE, CONFIG_USAGE_PAGE};
use pemu::{Answer, Aula84He, EmulatedChannel};
use ptransport::ReportChannel;

fn main() {
    println!("pemu -- a fake HERO 84 HE, assembled from recorded exchanges");
    println!("  endpoint      {CONFIG_USAGE_PAGE:#06X}:{CONFIG_USAGE:#06X}");

    let mut channel = EmulatedChannel::new(Aula84He::observed());
    println!("  report id     {}", channel.report_id());
    println!();

    for (label, request) in [
        (
            "read_model_id",
            frame(&[0x82, 0x01, 0x00, 0x01, 0x00, 0x06]),
        ),
        (
            "read_travel_precision",
            frame(&[0x82, 0x08, 0x00, 0x01, 0x00, 0x00]),
        ),
        (
            "read_key_travel W A S D",
            frame(&[
                0x93, 0x00, 0x00, 0x01, 0x00, 0x08, 0x00, 30, 0x00, 43, 0x00, 44, 0x00, 45,
            ]),
        ),
        (
            "read_key_travel, HID usages by mistake",
            frame(&[
                0x93, 0x00, 0x00, 0x01, 0x00, 0x08, 0x00, 0x1A, 0x00, 0x04, 0x00, 0x16, 0x00, 0x07,
            ]),
        ),
        ("read_key_switch_type (never sent)", frame(&[0x95, 0x00])),
    ] {
        println!("== {label} ==");
        match channel.write_report(&request) {
            Ok(sent) => println!("  ->  {}", hex(&sent[..16])),
            Err(error) => {
                println!("  ->  refused by the transport: {error}");
                continue;
            }
        }
        match channel.read_report(Duration::from_millis(1)) {
            Ok(Some(report)) => println!("  <-  {}", hex(&report.bytes[..16])),
            Ok(None) => println!("  <-  nothing"),
            Err(error) => println!("  <-  {error}"),
        }
        if let Some(last) = channel.log().last() {
            let verdict = match last.answer {
                Answer::Recorded(_) => "recorded answer",
                Answer::ReplayedPrevious(_) => "REPLAYED the previous answer -- the lie",
                Answer::EchoedRequest(_) => "echoed the request back",
                Answer::Silence => "said nothing",
                Answer::Stall => "stalled its endpoint",
            };
            println!("      {verdict}");
        }
        println!();
    }

    println!("First sixteen bytes shown per frame; the rest is zero padding and a checksum.");
    println!("Every recorded byte is sourced in docs/hardware/aula-bytech-exchange-*.md.");
}

fn frame(prefix: &[u8]) -> Vec<u8> {
    let mut out = vec![0u8; pemu::aula::FRAME_LEN];
    out[..prefix.len()].copy_from_slice(prefix);
    out
}

fn hex(bytes: &[u8]) -> String {
    bytes
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect::<Vec<_>>()
        .join(" ")
}
