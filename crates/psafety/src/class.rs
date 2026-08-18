//! What a command is allowed to do, and what that costs in time.

/// The classification an opcode carries in `data/protocols/*.toml`.
///
/// Only the first four exist at runtime. `destructive` and `unknown` are
/// spellings the ACL source understands and the generator refuses, so they have
/// no variant here: there is no value of this type that means "not allowed",
/// because a command that is not allowed has no [`SafeCommandId`] and therefore
/// never reaches a place where its class could be inspected.
///
/// [`SafeCommandId`]: crate::SafeCommandId
#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Debug)]
pub enum OpcodeClass {
    /// Reads state and changes nothing.
    SafeRead,
    /// May be sent to a board that has not been identified yet. A production
    /// read, repeatable and paced by the family's measured cadence, whose
    /// purpose happens to be identification.
    ProbeOk,
    /// Read-only, believed safe on a vendor artifact, and never yet sent to
    /// hardware by this project.
    ///
    /// Not a weaker `SafeRead`: a different door. It generates a
    /// [`ProbeCommandId`] rather than a [`SafeCommandId`], reaches
    /// [`ProbeGate`] rather than [`SafetyGate`], and costs one explicit
    /// confirmation per send with no second send available. It exists so that
    /// the first exchange with a new family has somewhere to happen that is not
    /// a hole in the rule that a `safe_read` is earned on hardware evidence.
    ///
    /// A command does not stay here. It is sent once, the answer is reviewed by
    /// a person, and the ACL entry is rewritten as a `safe_read` on `hardware`
    /// evidence -- by hand, in a diff, not by the program.
    ///
    /// [`ProbeCommandId`]: crate::ProbeCommandId
    /// [`SafeCommandId`]: crate::SafeCommandId
    /// [`ProbeGate`]: crate::ProbeGate
    /// [`SafetyGate`]: crate::SafetyGate
    BootstrapProbe,
    /// Writes state that does not survive a power cycle.
    SafeWrite,
    /// Writes state that does survive a power cycle. However it is presented in
    /// a UI, this is a flash write: a brightness slider that keeps its value
    /// between power cycles is one of these, not a volatile register.
    SlowFlash,
}

impl OpcodeClass {
    /// Whether executing this class changes the device.
    ///
    /// The distinction drives backup, journalling and confirmation, so it is
    /// stated once here rather than re-derived by each caller.
    pub fn writes(self) -> bool {
        matches!(self, OpcodeClass::SafeWrite | OpcodeClass::SlowFlash)
    }

    /// Whether this class needs a backup of the device before the first use.
    pub fn needs_backup(self) -> bool {
        self.writes()
    }

    pub fn as_str(self) -> &'static str {
        match self {
            OpcodeClass::SafeRead => "safe_read",
            OpcodeClass::ProbeOk => "probe_ok",
            OpcodeClass::BootstrapProbe => "bootstrap_probe",
            OpcodeClass::SafeWrite => "safe_write",
            OpcodeClass::SlowFlash => "slow_flash",
        }
    }
}

/// A burst allowance measured on hardware: at most `max_operations` within
/// `per_window_ms`.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub struct Burst {
    pub max_operations: u32,
    pub per_window_ms: u64,
}

/// One command's own measured timing, which outranks its class's.
///
/// The reason this exists: `read_model_id` was measured on hardware, and
/// writing that measurement into `[timing.class.safe_read]` would have claimed
/// it for every `safe_read` the family ever gains. A number measured for one
/// command is evidence about that command. The next one gets its own, or falls
/// back to a class number that was itself measured across the class.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub struct CommandTiming {
    pub family: &'static str,
    pub command: &'static str,
    pub min_gap_before_ms: u64,
    pub settle_after_ms: u64,
    pub burst: Option<Burst>,
}

/// Where a cadence came from.
///
/// Carried so that a journal or a report can say which measurement is
/// throttling an operation, rather than leaving a reader to guess whether a
/// number is specific to the command or inherited.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum CadenceSource {
    /// Measured for this exact command.
    Command,
    /// Measured for the class, and inherited by every command in it.
    Class,
}

/// The resolved answer to "how often may this go".
///
/// Resolution order, and there is no fourth step: this command's own measured
/// timing, then its class's, then nothing -- and nothing means a person is
/// asked, never that an interval is invented.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub struct Cadence {
    pub min_gap_before_ms: u64,
    pub settle_after_ms: u64,
    pub burst: Option<Burst>,
    pub source: CadenceSource,
}

impl From<&'static FamilyTiming> for Cadence {
    fn from(timing: &'static FamilyTiming) -> Self {
        Self {
            min_gap_before_ms: timing.min_gap_before_ms,
            settle_after_ms: timing.settle_after_ms,
            burst: timing.burst,
            source: CadenceSource::Class,
        }
    }
}

impl From<&'static CommandTiming> for Cadence {
    fn from(timing: &'static CommandTiming) -> Self {
        Self {
            min_gap_before_ms: timing.min_gap_before_ms,
            settle_after_ms: timing.settle_after_ms,
            burst: timing.burst,
            source: CadenceSource::Command,
        }
    }
}

/// One family's measured timing for one class of command.
///
/// Two numbers, not one, and the second is the one prior art learned the hard
/// way: `settle_after_ms` is the quiet a completed operation requires *after*
/// itself. A board wedged after 39 backlight writes a second apart while
/// breaching no threshold that had been reasoned out as if the setting were a
/// register (docs/prior-art/sharkfin-methods.md).
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub struct FamilyTiming {
    pub family: &'static str,
    pub class: OpcodeClass,
    pub min_gap_before_ms: u64,
    pub settle_after_ms: u64,
    pub burst: Option<Burst>,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_writing_classes_write() {
        assert!(!OpcodeClass::SafeRead.writes());
        assert!(!OpcodeClass::ProbeOk.writes());
        assert!(!OpcodeClass::BootstrapProbe.writes());
        assert!(OpcodeClass::SafeWrite.writes());
        assert!(OpcodeClass::SlowFlash.writes());
    }

    #[test]
    fn every_writing_class_needs_a_backup() {
        for class in [OpcodeClass::SafeWrite, OpcodeClass::SlowFlash] {
            assert!(class.needs_backup(), "{class:?} writes without a backup");
        }
    }
}
