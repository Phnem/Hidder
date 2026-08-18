//! Capability vocabulary.
//!
//! Skeleton only. Real types arrive with TICKET-10/11/12; this crate exists now
//! so the dependency direction is fixed before anything is written against it.
//!
//! # Why this crate is the one to get right
//!
//! Every other layer depends on it, and changing a capability's type or unit
//! after protocol engines are written against it is expensive
//! (architecture/INITIAL_REVIEW.md §3). Two constraints are already known and
//! must not be designed away:
//!
//! - the value shape must cover scalar, struct (DKS, SOCD), matrix (keymap) and
//!   *stream* (analog travel) from the start, because retrofitting a stream onto
//!   a scalar-only enum breaks every engine at once;
//! - a capability carries where the knowledge came from, and "the device
//!   answered" is not one of the trustworthy origins.
//!
//! # The origin marker
//!
//! A device's answer is not proof: an unsupported command often replays the
//! previous reply or returns plausible fiction. Silence is not proof either --
//! an opcode that goes unanswered on one board proves only that this firmware
//! on this board does not implement it (spec.md § Domain rules). So `Unsupported`
//! needs the same evidence as `Verified(hw)`, and neither is inferred.
//!
//! Open question for the architecture checkpoint before phase 3: prior art shows
//! capabilities that can only be known from the registry, because the firmware
//! answers the query whether or not the hardware exists (a board with no edge
//! light still answers the edge-light read). That is a fact about the product,
//! not about the protocol, and today's origin scale has no slot for it. Do not
//! quietly map it onto `Assumed` -- see docs/prior-art/sharkfin-methods.md.

/// How sure something is, on one axis of identity.
///
/// Lives here rather than in `pregistry` because two crates that must not
/// depend on each other both need the word: `pregistry` produces it and
/// `psafety` gates writes on it. A second enum in the second crate would drift
/// from the first one, and the drift would be silent and safety-relevant.
///
/// # This is one axis's confidence, never a device's
///
/// There is no such value as "how sure we are about this device". Knowing which
/// physical product is plugged in and not knowing what protocol it speaks is a
/// normal, common state, and the two facts carry their own confidence
/// separately. Collapsing them is what produces a matcher that is confidently
/// wrong (TICKET-22: a receiver and the mouse behind it are byte-identical in
/// structure and different products).
#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Debug, Default, serde::Serialize)]
pub enum Confidence {
    /// Nothing matched, or nothing was recorded. The default everywhere.
    #[default]
    Unknown,
    /// Some signals matched and at least one disagreed, or the signals that
    /// matched are too weak to carry the claim alone.
    Candidate,
    /// Every signal on this axis agreed, but the evidence behind them is
    /// second-hand.
    High,
    /// Every signal agreed and the evidence is this project's own hardware.
    Verified,
}

impl Confidence {
    pub fn as_str(self) -> &'static str {
        match self {
            Confidence::Unknown => "unknown",
            Confidence::Candidate => "candidate",
            Confidence::High => "high",
            Confidence::Verified => "verified",
        }
    }
}

/// A confidence that is specifically about a protocol family.
///
/// This type exists so that "may I write" is a question only the family axis
/// can be asked. [`Confidence`] on its own deliberately has no `permits_write`:
/// a bare confidence value carries no record of what it is a confidence *in*,
/// and the one mistake worth making impossible here is authorising a write on
/// the strength of knowing which product is plugged in.
///
/// Constructing one from a confidence that came off another axis is possible
/// and is a defect. It is spelled [`FamilyConfidence::established`] so that
/// doing it is visible in review and greppable in a diff, rather than looking
/// like ordinary plumbing.
#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Debug, Default)]
pub struct FamilyConfidence(Confidence);

impl FamilyConfidence {
    /// Records how well established a device's protocol family is.
    ///
    /// Callers: the protocol-family axis of the registry, and nothing else.
    pub fn established(confidence: Confidence) -> Self {
        Self(confidence)
    }

    pub fn value(self) -> Confidence {
        self.0
    }

    pub fn as_str(self) -> &'static str {
        self.0.as_str()
    }

    /// Whether a write may be attempted on the strength of this.
    ///
    /// Being certain which product is plugged in says nothing about which
    /// opcodes it understands, and it is opcodes that brick boards
    /// (spec.md § Domain rules).
    pub fn permits_write(self) -> bool {
        self.0 == Confidence::Verified
    }
}

impl std::fmt::Display for FamilyConfidence {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(self.as_str())
    }
}

// --- the capability vocabulary ----------------------------------------------

/// The canonical name of something a device can be asked about.
///
/// A closed enum, not a string. The layers above this one -- including the UI --
/// must not be able to invent a capability name, because a capability that does
/// not exist should fail to compile rather than render as an empty panel.
///
/// One variant today. That is not a placeholder for a list this crate is waiting
/// to grow: each variant is a promise that something can produce a value for it,
/// and the way this set expands is by an engine earning the right to read
/// something (spec.md FR1).
#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Debug, serde::Serialize)]
pub enum CapId {
    /// Per-key actuation point: the depth at which a key first registers, as an
    /// absolute distance from the top of travel.
    ///
    /// Deliberately not "how far the key is pressed". Four different numbers
    /// answer to that phrase on a Hall-effect board and only this one is a
    /// setting: the two rapid-trigger sensitivities are deltas from a moving
    /// reference, the calibration extremes are raw sensor readings, and the live
    /// key position is an observation. Keeping them apart in the vocabulary is
    /// what stops them being conflated in a UI
    /// (docs/prior-art/ember-actuation-model.md).
    HeActuation,
}

impl CapId {
    /// The stable string form, for the IPC boundary and for the journal.
    ///
    /// Stable in the sense that matters: this string appears in a user-visible
    /// journal and in saved profiles, so changing one is a compatibility break
    /// rather than a rename.
    pub fn as_str(self) -> &'static str {
        match self {
            CapId::HeActuation => "he.actuation",
        }
    }

    pub fn all() -> &'static [CapId] {
        &[CapId::HeActuation]
    }
}

impl std::fmt::Display for CapId {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(self.as_str())
    }
}

/// Where knowledge about a capability came from.
///
/// Carried with every value, because the difference between "we read this off
/// the board" and "the vendor's software implies boards like this have one" is
/// the difference between a control that is safe to show and one that is not
/// (spec.md FR1, architecture/INITIAL_REVIEW.md section 3).
///
/// Note which origin does **not** exist: "the device answered". A device
/// answering is not evidence -- an unsupported command commonly replays the
/// previous reply -- so an answer becomes [`Origin::VerifiedOnHardware`] only
/// once something checked that it means what it is believed to mean, and the
/// origin records which command did the earning.
#[derive(Clone, Copy, PartialEq, Eq, Debug, serde::Serialize)]
pub enum Origin {
    /// Read from a device by a named command whose meaning was verified against
    /// something outside this project.
    VerifiedOnHardware { command: &'static str },
    /// Recovered from a vendor artifact -- a configurator bundle, firmware, a
    /// capture -- and never confirmed against this device.
    VendorArtifact,
    /// Inferred. Never sufficient to enable a control that writes.
    Assumed,
}

impl Origin {
    /// Whether a value from this origin may be presented as a fact about the
    /// device rather than as an expectation.
    pub fn is_from_hardware(self) -> bool {
        matches!(self, Origin::VerifiedOnHardware { .. })
    }

    pub fn as_str(self) -> &'static str {
        match self {
            Origin::VerifiedOnHardware { .. } => "verified on hardware",
            Origin::VendorArtifact => "from a vendor artifact",
            Origin::Assumed => "assumed",
        }
    }
}

/// The unit a measurement is in, spelled rather than implied.
///
/// The alternative -- millimetres by convention -- is how a raw device count
/// reaches a UI label that says "mm" without anything having converted it. This
/// project has already had a scale go unexamined for want of a unit being
/// written down.
#[derive(Clone, Copy, PartialEq, Eq, Debug, serde::Serialize)]
pub enum Unit {
    Millimetres,
}

impl Unit {
    pub fn suffix(self) -> &'static str {
        match self {
            Unit::Millimetres => "mm",
        }
    }
}

/// A number with its unit and the resolution it was reported at.
#[derive(Clone, Copy, PartialEq, Debug, serde::Serialize)]
pub struct Measurement {
    pub value: f64,
    pub unit: Unit,
    /// How many decimals are meaningful, derived from the device's own step
    /// rather than chosen for looks. Showing more digits than the device can
    /// distinguish invents precision.
    pub decimals: u8,
}

impl Measurement {
    pub fn new(value: f64, unit: Unit, decimals: u8) -> Self {
        Self {
            value,
            unit,
            decimals,
        }
    }

    /// Rendered the way it should be shown, unit included.
    pub fn render(&self) -> String {
        format!(
            "{:.*} {}",
            usize::from(self.decimals),
            self.value,
            self.unit.suffix()
        )
    }
}

/// One key's value, with the key named in terms the UI can use.
///
/// `label` rather than a protocol key id, because which number identifies a key
/// is a protocol detail and this crate sits above the protocol. A UI handed a
/// vendor key id would need the vendor's key table to render it, which is
/// exactly the coupling the capability layer exists to prevent -- and this
/// project has already shipped one defect from treating two key-numbering
/// spaces as interchangeable.
#[derive(Clone, PartialEq, Debug, serde::Serialize)]
pub struct KeyMeasurement {
    pub label: String,
    pub measurement: Measurement,
}

/// How a capability's value is shaped.
///
/// All four exist from the start and three have no producer yet. That is
/// deliberate, and this crate's header says why: retrofitting a stream onto a
/// scalar-only value type breaks every engine at once, and the shapes are
/// already known -- DKS and SOCD are compound, a keymap is per-key, analog
/// travel is a stream (TICKET-15).
///
/// A variant with no producer is not dead code here. It is the shape of a hole,
/// held open so that filling it is an addition rather than a migration.
#[derive(Clone, PartialEq, Debug, serde::Serialize)]
pub enum CapValue {
    /// One number for the whole device.
    Scalar(Measurement),
    /// One number per key.
    PerKey(Vec<KeyMeasurement>),
    /// Several named numbers that only mean anything together.
    Compound(Vec<(&'static str, Measurement)>),
    /// A continuous feed. Carries no samples: a stream's values arrive on their
    /// own channel, and a snapshot of one would be a different thing wearing its
    /// name (TICKET-15).
    Stream { unit: Unit },
}

/// A capability, its value, and where the knowledge came from.
#[derive(Clone, PartialEq, Debug, serde::Serialize)]
pub struct Capability {
    pub id: CapId,
    pub origin: Origin,
    pub confidence: Confidence,
    pub value: CapValue,
    /// Why this value means what it says, in one line a person can read.
    ///
    /// Not a debug string. The Journal screen has to answer "why is this control
    /// enabled" without the reader opening the source, and a capability that
    /// cannot explain itself is one nobody can audit (spec.md FR3).
    pub provenance: String,
}

/// What is known about a capability that has no value.
///
/// Separate from `Option<Capability>` on purpose. "Not read yet", "this board
/// does not have it" and "we have no way to ask" are three different states, and
/// a UI must not render them alike -- collapsing them is how a missing feature
/// and a missing implementation become indistinguishable (spec.md, domain
/// rules).
#[derive(Clone, PartialEq, Eq, Debug, serde::Serialize)]
pub enum Unavailable {
    /// Readable, not read yet.
    NotRead,
    /// This project has no verified command for it on this family. Says nothing
    /// about whether the hardware has the feature.
    NoVerifiedCommand,
    /// Established, with evidence, that this device does not have it.
    ///
    /// Needs the same weight of evidence as a positive claim: an opcode going
    /// unanswered proves only that this firmware did not answer it.
    NotPresent { evidence: String },
}

#[cfg(test)]
mod tests {
    #![allow(clippy::expect_used, clippy::panic, clippy::unwrap_used)]

    use super::*;

    #[test]
    fn only_a_verified_family_permits_a_write() {
        assert!(FamilyConfidence::established(Confidence::Verified).permits_write());
        for lower in [Confidence::Unknown, Confidence::Candidate, Confidence::High] {
            assert!(
                !FamilyConfidence::established(lower).permits_write(),
                "{lower:?} would have allowed a write"
            );
        }
    }

    #[test]
    fn a_bare_confidence_cannot_authorise_anything() {
        // Not an assertion so much as a statement of where the guarantee lives:
        // `Confidence` has no `permits_write`, so a product-axis or
        // structural-axis value has nothing to offer a write path. If that
        // method ever returns to `Confidence`, this comment is the reason it
        // should not.
        assert_eq!(Confidence::Verified.as_str(), "verified");
    }

    #[test]
    fn the_default_is_unknown() {
        assert_eq!(Confidence::default(), Confidence::Unknown);
    }

    #[test]
    fn the_scale_orders_as_written() {
        assert!(Confidence::Unknown < Confidence::Candidate);
        assert!(Confidence::Candidate < Confidence::High);
        assert!(Confidence::High < Confidence::Verified);
    }
}
