//! The closed set of commands that can exist, and the authorisation a dispatch
//! receives.

use crate::class::{Burst, FamilyTiming, OpcodeClass};

/// What the generated table knows about one command.
///
/// `opcode` is `pub(crate)` and stays that way. The byte is the thing this whole
/// crate exists to control: it is read in exactly one place, when the gate mints
/// an [`AuthorizedCommand`] after every check has passed.
pub(crate) struct CommandRecord {
    pub(crate) id: SafeCommandId,
    pub(crate) family: &'static str,
    pub(crate) name: &'static str,
    pub(crate) opcode: u8,
    pub(crate) class: OpcodeClass,
}

include!(concat!(env!("OUT_DIR"), "/safe_command_id.rs"));

impl SafeCommandId {
    fn record(self) -> &'static CommandRecord {
        let index = self as usize;
        // Total by construction: the generated enum numbers its variants from
        // zero in the order of the same table this indexes.
        &COMMANDS[index]
    }

    /// The protocol family this command belongs to.
    ///
    /// A command is meaningful only inside its family. The same byte is
    /// Debounce in one ROYUAN family and Options in another, and factory reset
    /// swaps numbers between them, so nothing in this crate ever compares
    /// opcodes across families.
    pub fn family(self) -> &'static str {
        self.record().family
    }

    /// The command's name in the ACL source, for logs and the journal.
    pub fn name(self) -> &'static str {
        self.record().name
    }

    pub fn class(self) -> OpcodeClass {
        self.record().class
    }

    pub(crate) fn opcode(self) -> u8 {
        self.record().opcode
    }

    /// Every command that exists, for tests and for the support table.
    pub fn all() -> impl Iterator<Item = SafeCommandId> {
        COMMANDS.iter().map(|record| record.id)
    }

    /// Looks up a command by family and name.
    ///
    /// Deliberately not "by opcode": there is no lookup in this crate that turns
    /// a byte into a command, because that is precisely the operation the
    /// boundary forbids. A caller with a byte and an intention to send it has
    /// nowhere to go.
    pub fn find(family: &str, name: &str) -> Option<SafeCommandId> {
        COMMANDS
            .iter()
            .find(|record| record.family == family && record.name == name)
            .map(|record| record.id)
    }
}

/// Families the ACL knows about, whether or not any of them has a command.
pub fn known_families() -> &'static [&'static str] {
    FAMILIES
}

/// The measured timing for a family and class, if the ACL declares one.
pub(crate) fn timing_for(family: &str, class: OpcodeClass) -> Option<&'static FamilyTiming> {
    TIMINGS
        .iter()
        .find(|timing| timing.family == family && timing.class == class)
}

/// A command that has passed every check and may be put on the wire.
///
/// Constructed only by [`SafetyGate`], never `Clone` and never `Copy`, and
/// consumed by value on dispatch. A sink cannot mint one, and cannot replay the
/// one it was given: authorisation is for a single operation at a single moment,
/// not a permission that can be kept.
///
/// [`SafetyGate`]: crate::SafetyGate
#[derive(Debug)]
pub struct AuthorizedCommand {
    id: SafeCommandId,
    payload: Vec<u8>,
}

impl AuthorizedCommand {
    pub(crate) fn new(id: SafeCommandId, payload: Vec<u8>) -> Self {
        Self { id, payload }
    }

    pub fn id(&self) -> SafeCommandId {
        self.id
    }

    /// The opcode byte to put at the head of the frame.
    ///
    /// This is where the byte leaves the crate, and it is the only place. It
    /// comes from the generated table, never from the caller: a payload cannot
    /// become an opcode, because the opcode is not something the caller
    /// supplied.
    pub fn opcode(&self) -> u8 {
        self.id.opcode()
    }

    /// Parameters for the command. Data, not instructions: whatever is in here,
    /// the device is told to do what [`AuthorizedCommand::opcode`] says.
    pub fn payload(&self) -> &[u8] {
        &self.payload
    }

    pub fn family(&self) -> &'static str {
        self.id.family()
    }

    pub fn class(&self) -> OpcodeClass {
        self.id.class()
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::expect_used, clippy::panic, clippy::unwrap_used)]

    use super::*;

    #[test]
    fn the_board_we_own_has_no_executable_command() {
        // TICKET-08 inventoried the AULA and found two vendor collections and
        // no idea what to say to either. Until an exchange is verified, this
        // must stay empty -- which is also what makes it impossible for this
        // ticket to write to that keyboard.
        let aula: Vec<_> = SafeCommandId::all()
            .filter(|id| id.family() == "aula-hero84-he")
            .collect();
        assert!(aula.is_empty(), "unearned commands for the AULA: {aula:?}");
        assert!(
            known_families().contains(&"aula-hero84-he"),
            "the family should still be known, just empty"
        );
    }

    #[test]
    fn every_command_has_measured_timing_for_its_class() {
        for id in SafeCommandId::all() {
            assert!(
                timing_for(id.family(), id.class()).is_some(),
                "{}::{} has no measured timing, so nothing could throttle it",
                id.family(),
                id.name()
            );
        }
    }

    #[test]
    fn a_command_cannot_be_found_by_opcode() {
        // A compile-time property expressed as a reminder: `find` takes a name.
        // If a `find_by_opcode` ever appears, this test's comment is the note
        // explaining why it must not.
        assert!(SafeCommandId::find("royuan-gen2", "identify").is_some());
        assert!(SafeCommandId::find("royuan-gen2", "erase_display_flash").is_none());
    }

    #[test]
    fn the_payload_cannot_choose_the_opcode() {
        let id = SafeCommandId::find("royuan-gen2", "identify").expect("fixture command");
        let hostile = AuthorizedCommand::new(id, vec![0xAC; 8]);
        assert_eq!(
            hostile.opcode(),
            0x8F,
            "the opcode came from the payload instead of the table"
        );
    }
}
