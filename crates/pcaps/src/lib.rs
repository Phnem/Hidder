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
#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Debug, Default)]
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
