//! The read-only `aula-bytech` protocol engine.
//!
//! Minimal on purpose: it reads a model id, and that is the whole of what
//! TICKET-12 earned. A second capability arrives when a second command is
//! verified, which is its own decision and its own ticket.
//!
//! # What this engine does not know
//!
//! **A report id.** It knows it needs this family's *config endpoint*, spelled
//! as a usage page and a usage, and nothing further. Which report id that
//! collection uses is read from the board's own descriptor by the transport, so
//! the same engine works on a board in this family whose descriptor numbers it
//! differently. Nine models already share our board's VID:PID; assuming they
//! also share its report id would be the same class of mistake as assuming they
//! share its layout.
//!
//! **Any byte of any command.** It names [`ModelIdRead`], the gate turns that
//! into an authorised command, and the adapter turns *that* into a frame. There
//! is no point on this path where the engine holds `0x82` or `0x01`.
//!
//! # The path
//!
//! ```text
//!   caller (pcore)
//!     -> AulaBytechEngine::read_model_id
//!        -> SafetyGate::read::<ModelIdRead>      identity, family, cadence,
//!           |                                    quarantine -- all checked here
//!           -> CommandSink (aula_bytech_io::Exchange)
//!              -> ProbeChannel                   report id from the descriptor
//!                 -> the config collection
//!           <- bytes
//!        <- ModelIdRead, or an error; never bytes
//! ```

use psafety::{Clock, CommandSink, JournalSink, ReadError, SafetyGate};
use ptransport::DeviceId;

use crate::aula_bytech::{ModelIdError, ModelIdRead, VendorModelId};
use crate::aula_bytech_he::{KeyTravelError, TravelScale, WasdTravelRead, actuation_capability};

/// A HID top-level collection identified the way a protocol engine thinks about
/// it: by what it is for, not by which numbers it happens to use.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub struct ConfigEndpoint {
    pub usage_page: u16,
    pub usage: u16,
}

/// The family this engine speaks. Shared with the ACL and the registry, and a
/// mismatch is refused by the gate rather than by anything here.
pub const FAMILY: &str = "aula-bytech";

/// The read-only engine for `aula-bytech`.
///
/// Stateless: it owns no handle, no session and no device. Everything it needs
/// arrives as arguments, because device ownership lives in exactly one place
/// and it is not here (spec.md FR10).
pub struct AulaBytechEngine;

impl AulaBytechEngine {
    /// Where this family is spoken to.
    ///
    /// The vendor's own driver filters for this collection, and TICKET-08 found
    /// it on our board. The other vendor collection on the same board,
    /// `0xFF00:0x0001`, carries feature reports only and is not this.
    pub fn config_endpoint() -> ConfigEndpoint {
        ConfigEndpoint {
            usage_page: 0xFF60,
            usage: 0x0061,
        }
    }

    pub fn family() -> &'static str {
        FAMILY
    }

    /// What the exchange of 2026-08-18 established, and no more than that.
    ///
    /// Hand this to `Registry::identify_with` for a board that has actually
    /// answered `read_model_id` here. It says: this endpoint speaks
    /// `aula-bytech`, established by one named command answering correctly,
    /// verified on this project's own hardware.
    ///
    /// Read the scope carefully, because it is narrower than it looks:
    ///
    /// - it is about **this** device, the one that answered. It is not a claim
    ///   about the other eight models that share `0x372E:0x103E`, and a caller
    ///   that attaches it to a board which has not answered is asserting
    ///   something nobody checked;
    /// - it names one command. `Verified` here means "this family's framing,
    ///   checksum and response matching work, as exercised by `read_model_id`".
    ///   It does not mean any other command in the family exists, is safe, or is
    ///   encoded the way the vendor artifact suggests;
    /// - it is deliberately not a constant a caller can attach without having
    ///   done the exchange, which is why it is a function on the engine that
    ///   performs the exchange rather than a value in the registry's data.
    ///
    /// See `docs/hardware/aula-bytech-exchange-001.md` and `-002-timing.md`.
    pub fn verified_exchange_evidence() -> pregistry::ProtocolEvidence {
        pregistry::ProtocolEvidence {
            family: FAMILY,
            confidence: pcaps::Confidence::Verified,
            source: pregistry::ProtocolEvidenceSource::VerifiedExchange,
            command: Some("read_model_id"),
        }
    }

    /// Reads the device's model identifier.
    ///
    /// Goes through the gate, so it inherits every check the gate performs:
    /// the device must be identified, the family must match, the command's
    /// measured cadence must permit it now, and a quarantined device refuses
    /// everything. The answer is validated by [`ModelIdRead`] before it becomes
    /// a value.
    ///
    /// `confirmation` is `None` in the ordinary case: this command has measured
    /// timing, so the limiter has something to throttle against and does not
    /// need to ask anybody. It is a parameter rather than absent so that the
    /// same call works on a device whose family has no measurement yet.
    pub fn read_model_id<S, J, C>(
        gate: &mut SafetyGate<S, J, C>,
        device: DeviceId,
        confirmation: Option<psafety::rate::UserConfirmation>,
    ) -> Result<VendorModelId, ReadError<ModelIdError>>
    where
        S: CommandSink,
        J: JournalSink,
        C: Clock,
    {
        gate.read::<ModelIdRead>(device, confirmation)
            .map(|read| read.model_id)
    }

    /// Reads the actuation point of W, A, S and D as the canonical
    /// `he.actuation` capability.
    ///
    /// Same shape as [`AulaBytechEngine::read_model_id`] and for the same
    /// reasons: it names a response type, the gate turns that into an authorised
    /// command, and no byte of `0x93` passes through this function.
    ///
    /// # The scale
    ///
    /// `scale` is a parameter rather than a constant because a travel value is a
    /// count of steps whose size the device owns. On our board the device
    /// reports none and [`TravelScale::VendorFallback`] applies; on a board that
    /// answers `read_travel_precision` the caller passes what it answered. The
    /// value carries which of the two applied, so a reader is never left to
    /// assume.
    ///
    /// # What it does not do
    ///
    /// It reads four named keys, not the board. The vendor chunks these at
    /// eleven keys per exchange, so a full 84-key read is eight exchanges and
    /// needs multi-packet reassembly this codec deliberately does not have --
    /// it refuses to chunk silently rather than writing twice without being
    /// asked.
    pub fn read_actuation<S, J, C>(
        gate: &mut SafetyGate<S, J, C>,
        device: DeviceId,
        scale: TravelScale,
        confirmation: Option<psafety::rate::UserConfirmation>,
    ) -> Result<pcaps::Capability, ReadError<KeyTravelError>>
    where
        S: CommandSink,
        J: JournalSink,
        C: Clock,
    {
        gate.read::<WasdTravelRead>(device, confirmation)
            .map(|read| actuation_capability(&read, scale))
    }

    /// The scale this board resolves to, and why.
    ///
    /// A named function rather than a constant so that the reason travels with
    /// the value. Our HERO 84 HE answered `read_travel_precision` with our own
    /// frame, which is indistinguishable from reporting nothing, so the vendor's
    /// documented fallback applies -- and exchange 005 confirmed it against four
    /// values a person set in the official configurator.
    ///
    /// Not a family constant. Another `bytech` board may report a precision of
    /// its own, and [`TravelScale`] stays a two-case type so that it can.
    pub fn travel_scale() -> TravelScale {
        TravelScale::VendorFallback
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::expect_used, clippy::panic, clippy::unwrap_used)]

    use super::*;

    #[test]
    fn the_engine_speaks_a_family_the_acl_knows() {
        assert!(psafety::known_families().contains(&AulaBytechEngine::family()));
    }

    #[test]
    fn the_engine_names_an_endpoint_and_never_a_report_id() {
        // The property from TICKET-12 item 9, asserted rather than left to
        // discipline: what the engine publishes is a usage pair. If a report id
        // ever appears in this type, this test is where the reason it must not
        // is written down -- a board in this family with a different descriptor
        // must still work.
        let endpoint = AulaBytechEngine::config_endpoint();
        assert_eq!((endpoint.usage_page, endpoint.usage), (0xFF60, 0x0061));
        let rendered = format!("{endpoint:?}").to_lowercase();
        assert!(
            !rendered.contains("report"),
            "a report id leaked into the endpoint type: {rendered}"
        );
    }

    #[test]
    fn the_evidence_this_engine_offers_names_its_own_scope() {
        // The granularity requirement, as a test: evidence from an exchange
        // must say which command produced it, or a reader is free to take it
        // for a claim about the whole family.
        let evidence = AulaBytechEngine::verified_exchange_evidence();
        assert_eq!(evidence.family, FAMILY);
        assert_eq!(evidence.command, Some("read_model_id"));
        assert_eq!(
            evidence.source,
            pregistry::ProtocolEvidenceSource::VerifiedExchange
        );
        assert_eq!(evidence.confidence, pcaps::Confidence::Verified);
    }

    #[test]
    fn the_command_this_engine_reads_is_the_one_the_acl_earned() {
        use psafety::CommandResponse;
        let id = <ModelIdRead as CommandResponse>::COMMAND;
        assert_eq!(id.family(), AulaBytechEngine::family());
        assert_eq!(id.name(), "read_model_id");
        assert_eq!(id.class(), psafety::OpcodeClass::SafeRead);
    }
}
