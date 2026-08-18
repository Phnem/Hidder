//! The closed set of commands that can exist, and the authorisation a dispatch
//! receives.

use crate::class::{Burst, Cadence, CommandTiming, FamilyTiming, OpcodeClass};
use crate::key::CommandKey;

/// What the generated table knows about one command.
///
/// `key` is `pub(crate)` and stays that way. The bytes that identify a command
/// are the thing this whole crate exists to control: they are read in exactly
/// one place, when the gate mints an [`AuthorizedCommand`] after every check has
/// passed.
pub(crate) struct CommandRecord {
    pub(crate) id: SafeCommandId,
    pub(crate) family: &'static str,
    pub(crate) name: &'static str,
    pub(crate) key: CommandKey,
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

    pub(crate) fn key(self) -> CommandKey {
        self.record().key
    }

    /// Every command that exists, for tests and for the support table.
    pub fn all() -> impl Iterator<Item = SafeCommandId> {
        COMMANDS.iter().map(|record| record.id)
    }

    /// Looks up a command by family and name.
    ///
    /// Deliberately not "by key": there is no lookup in this crate that turns
    /// bytes into a command, because that is precisely the operation the
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

/// The measured timing for one specific command, if the ACL declares one.
pub(crate) fn command_timing_for(family: &str, command: &str) -> Option<&'static CommandTiming> {
    COMMAND_TIMINGS
        .iter()
        .find(|timing| timing.family == family && timing.command == command)
}

/// How often this operation may go, resolved in the one order that exists.
///
/// A command's own measurement first, then its class's, then `None`. `None` is
/// not a permissive default: it means nothing has been measured, and the caller
/// turns that into a request for a person's confirmation rather than into an
/// invented interval.
///
/// `command` is optional because the limiter is also asked about families that
/// have no command at all -- an unidentified board, in the default-deny case --
/// and those have nothing to look up.
pub(crate) fn cadence_for(
    family: &str,
    command: Option<&str>,
    class: OpcodeClass,
) -> Option<Cadence> {
    command
        .and_then(|command| command_timing_for(family, command))
        .map(Cadence::from)
        .or_else(|| timing_for(family, class).map(Cadence::from))
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

    /// What identifies this command on the wire.
    ///
    /// This is where those bytes leave the crate, and it is the only place. They
    /// come from the generated table, never from the caller: a payload cannot
    /// become a command identity, because the identity is not something the
    /// caller supplied.
    ///
    /// A [`CommandKey`] rather than a byte since TICKET-12, because one family's
    /// commands are group/subcommand pairs and collapsing a pair to its group
    /// would authorise every subcommand in it.
    pub fn key(&self) -> CommandKey {
        self.id.key()
    }

    /// Parameters for the command. Data, not instructions: whatever is in here,
    /// the device is told to do what [`AuthorizedCommand::key`] says.
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
    fn every_command_resolves_to_a_measured_cadence() {
        for id in SafeCommandId::all() {
            assert!(
                cadence_for(id.family(), Some(id.name()), id.class()).is_some(),
                "{}::{} has no measured timing, so nothing could throttle it",
                id.family(),
                id.name()
            );
        }
    }

    #[test]
    fn a_commands_own_timing_outranks_its_classs() {
        // Resolution order, asserted on whatever the ACL currently ships: if a
        // command declares its own numbers, they are the ones that come back,
        // and the class numbers are not consulted.
        for timing in COMMAND_TIMINGS {
            let id = SafeCommandId::find(timing.family, timing.command)
                .expect("command timing names a command that exists");
            let cadence = cadence_for(id.family(), Some(id.name()), id.class())
                .expect("a command with its own timing has a cadence");
            assert_eq!(cadence.source, crate::class::CadenceSource::Command);
            assert_eq!(cadence.min_gap_before_ms, timing.min_gap_before_ms);
        }
    }

    #[test]
    fn a_command_timing_never_leaks_into_a_sibling() {
        // The whole reason command timing exists: one measured command must not
        // speak for the next one in the same class.
        //
        // Stated as "a command with no measurement of its own never resolves to
        // a Command cadence", which is the property that actually matters. The
        // earlier phrasing walked the sibling pairs instead, and that stopped
        // being a test of anything the moment a second command earned its own
        // number: two measured siblings each resolving to their own is the
        // correct outcome, and the old loop called it a leak.
        for id in SafeCommandId::all() {
            let has_own = COMMAND_TIMINGS
                .iter()
                .any(|timing| timing.family == id.family() && timing.command == id.name());
            let cadence = cadence_for(id.family(), Some(id.name()), id.class());
            if has_own {
                let cadence = cadence.expect("a command with its own timing has a cadence");
                assert_eq!(
                    cadence.source,
                    crate::class::CadenceSource::Command,
                    "{}::{} has its own measurement and did not use it",
                    id.family(),
                    id.name()
                );
            } else {
                assert!(
                    cadence.is_none_or(|c| c.source == crate::class::CadenceSource::Class),
                    "{}::{} has no measurement of its own and inherited a command-level one",
                    id.family(),
                    id.name()
                );
            }
        }
    }

    #[test]
    fn a_command_cannot_be_found_by_key() {
        // A compile-time property expressed as a reminder: `find` takes a name.
        // If a `find_by_opcode` ever appears, this test's comment is the note
        // explaining why it must not.
        assert!(SafeCommandId::find("royuan-gen2", "identify").is_some());
        assert!(SafeCommandId::find("royuan-gen2", "erase_display_flash").is_none());
    }

    #[test]
    fn the_payload_cannot_choose_the_command() {
        let id = SafeCommandId::find("royuan-gen2", "identify").expect("fixture command");
        let hostile = AuthorizedCommand::new(id, vec![0xAC; 8]);
        assert_eq!(
            hostile.key(),
            CommandKey::Opcode(0x8F),
            "the command identity came from the payload instead of the table"
        );
    }
}
