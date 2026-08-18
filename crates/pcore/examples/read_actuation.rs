//! One ordinary production read of `he.actuation`.
//!
//! ```text
//! cargo run -p pcore --example read_actuation
//! ```
//!
//! # What this program does not have
//!
//! No flag, no confirmation prompt, no probe gate, no bootstrap type, no opcode,
//! no report id and no key id. It discovers, connects, and asks for a capability
//! by name. Everything else is arranged below it.
//!
//! That is the whole claim TICKET-12 closes on. A read that needed a special
//! entry point would be a read the application cannot make, and the difference
//! between "we got the value once with a tool" and "the product can read it" is
//! exactly this file.

use pcaps::{CapId, CapValue};
use pcore::{CoreError, Peripheral};

fn main() {
    if let Err(error) = run() {
        eprintln!("\n  STOPPED: {error}");
        std::process::exit(1);
    }
}

fn run() -> Result<(), CoreError> {
    let peripheral = Peripheral::open()?;

    println!("== discovered ==");
    let devices = peripheral.discover();
    for device in &devices {
        println!(
            "  {:04X}:{:04X}  {:<28} product {:<10} family {:<10} {}",
            device.vendor_id,
            device.product_id,
            device.product.as_deref().unwrap_or("(no product string)"),
            device.identification.product.confidence.as_str(),
            device.identification.family.confidence.as_str(),
            if device.is_connectable() {
                "config endpoint"
            } else {
                "no config endpoint"
            }
        );
    }

    let Some(target) = devices.iter().find(|d| d.is_connectable()) else {
        return Err(CoreError::NoConfigEndpoint);
    };

    println!(
        "\n== connecting to {} ==",
        target.product.as_deref().unwrap_or("the device")
    );
    let mut session = peripheral.connect(target)?;
    println!("  model id      {}", session.model_id());
    println!(
        "  family        {} [{}]",
        session
            .identification()
            .family
            .family
            .unwrap_or("not established"),
        session.identification().family.confidence.as_str()
    );
    println!(
        "  writes        {} ({} write command(s) exist; family confidence {} a write)",
        if session.is_writable() {
            "possible"
        } else {
            "impossible"
        },
        session.writable_commands(),
        if session.family_permits_write() {
            "would permit"
        } else {
            "would refuse"
        }
    );

    println!("\n== capabilities ==");
    for (id, state) in session.capabilities() {
        match state {
            Ok(()) => println!("  {id}  readable"),
            Err(reason) => println!("  {id}  unavailable: {reason:?}"),
        }
    }

    println!("\n== read {} ==", CapId::HeActuation);
    let capability = peripheral.read(&mut session, CapId::HeActuation)?;
    match &capability.value {
        CapValue::PerKey(keys) => {
            for key in keys {
                println!("  {:<4} {}", key.label, key.measurement.render());
            }
        }
        other => println!("  unexpected shape: {other:?}"),
    }
    println!("\n  origin        {}", capability.origin.as_str());
    println!("  confidence    {}", capability.confidence.as_str());
    println!("  provenance    {}", capability.provenance);

    println!("\n== journal ==");
    for line in session.journal().render() {
        println!("  {line}");
    }

    Ok(())
}
