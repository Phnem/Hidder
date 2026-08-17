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
    /// May be sent to a board that has not been identified yet.
    ProbeOk,
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
