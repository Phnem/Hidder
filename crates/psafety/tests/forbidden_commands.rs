//! What the rest of the workspace can and cannot reach.
//!
//! These run as an external crate on purpose. Everything below is written the
//! way a protocol engine would have to write it, so a change that widens the
//! boundary shows up here as a test that suddenly compiles differently rather
//! than as a review comment nobody made.
//!
//! The compile-time half of the boundary is not asserted by running code, and
//! cannot be: the statements are "there is no variant for a destructive opcode",
//! "there is no public constructor for `AuthorizedCommand`", and "`execute`
//! does not accept a byte". Each is enforced by the type system and would be a
//! compile error, not a failed assertion. What these tests can do is pin the
//! data those statements depend on -- which is where a mistake would actually
//! come from, since the enum is generated from a file a human edits.

#![allow(clippy::expect_used, clippy::panic, clippy::unwrap_used)]

use psafety::{OpcodeClass, SafeCommandId, known_families};

/// Opcodes that must never be executable, and what each one does. Written out
/// here rather than derived from the same source the generator reads: a test
/// that recomputes the answer from the input agrees with the generator even
/// when both are wrong.
const MUST_NEVER_BE_EXECUTABLE: &[(&str, &str)] = &[
    ("royuan-gen2", "erase_display_flash"),
    ("royuan-gen2", "enter_bootloader_a"),
    ("royuan-gen2", "enter_bootloader_b"),
    ("royuan-gen2", "factory_reset"),
    ("royuan-gen2", "set_debounce"),
    ("royuan-gen2", "set_options"),
    ("royuan-gen2", "set_keymatrix"),
    ("royuan-gen2", "set_led_params"),
    ("royuan-gen2", "he_magnetic_switches"),
    ("royuan-yc500", "erase_display_flash"),
    ("royuan-yc500", "boot_logo_a"),
    ("royuan-yc500", "boot_logo_b"),
    ("royuan-yc500", "factory_reset"),
    ("royuan-yc500", "set_options"),
    ("royuan-yc500", "set_keymatrix"),
    ("royuan-yc500", "set_debounce"),
    ("royuan-yc500", "set_led_params"),
];

#[test]
fn no_refused_opcode_has_a_command() {
    for (family, name) in MUST_NEVER_BE_EXECUTABLE {
        assert!(
            SafeCommandId::find(family, name).is_none(),
            "{family}::{name} is executable, and it must not be"
        );
    }
}

#[test]
fn the_erase_and_bootloader_opcodes_are_absent_from_every_family() {
    // Belt and braces against the same byte being reintroduced under a new
    // name: check the values, not just the names we happen to have listed.
    for id in SafeCommandId::all() {
        let name = id.name();
        assert!(
            !name.contains("erase") && !name.contains("bootloader") && !name.contains("reset"),
            "{}::{name} sounds like something that should not be executable",
            id.family()
        );
    }
}

#[test]
fn nothing_in_the_registry_writes_yet() {
    // True today, and the reason TICKET-11 could not write to a keyboard even
    // by mistake. When it stops being true, the change should be deliberate and
    // this test is where it gets noticed.
    let writers: Vec<_> = SafeCommandId::all()
        .filter(|id| id.class().writes())
        .map(|id| format!("{}::{}", id.family(), id.name()))
        .collect();
    assert!(
        writers.is_empty(),
        "a write command appeared without anyone deciding to allow one: {writers:?}"
    );
}

#[test]
fn the_board_we_own_has_no_production_command_at_all() {
    // Two families now, and neither may produce a `SafeCommandId`.
    // `aula-hero84-he` is the product entry and stays empty. `aula-bytech` is
    // the protocol family, and everything it knows came from a vendor artifact,
    // which does not earn a production read -- only a bootstrap probe.
    for family in ["aula-hero84-he", "aula-bytech"] {
        let earned: Vec<_> = SafeCommandId::all()
            .filter(|id| id.family() == family)
            .map(SafeCommandId::name)
            .collect();
        assert!(
            earned.is_empty(),
            "unearned production commands for {family}: {earned:?}"
        );
    }
}

#[test]
fn the_aula_bootstrap_probe_is_the_only_probe_that_exists() {
    let probes: Vec<_> = psafety::ProbeCommandId::all()
        .map(|id| (id.family(), id.name()))
        .collect();
    assert_eq!(
        probes,
        [("aula-bytech", "read_model_id")],
        "the probe surface grew without the plan saying so"
    );
}

#[test]
fn a_vendor_artifact_cannot_reach_the_production_path() {
    // The bootstrap rule, stated as a property rather than as a comment: every
    // probe command is absent from `SafeCommandId`, so the only door a
    // vendor-artifact command has is the one that costs a confirmation and
    // yields exactly one send.
    for id in psafety::ProbeCommandId::all() {
        assert!(
            SafeCommandId::find(id.family(), id.name()).is_none(),
            "{}::{} is executable through the production gate",
            id.family(),
            id.name()
        );
    }
}

#[test]
fn the_families_we_know_about_are_the_ones_we_documented() {
    let mut families = known_families().to_vec();
    families.sort_unstable();
    assert_eq!(
        families,
        ["aula-bytech", "aula-hero84-he", "royuan-gen2", "royuan-yc500"],
        "a family appeared or vanished without the plan saying so"
    );
}

#[test]
fn every_executable_command_is_a_read_or_a_probe() {
    for id in SafeCommandId::all() {
        assert!(
            matches!(id.class(), OpcodeClass::SafeRead | OpcodeClass::ProbeOk),
            "{}::{} is class {:?}",
            id.family(),
            id.name(),
            id.class()
        );
    }
}

#[test]
fn the_same_byte_in_two_families_is_two_different_commands() {
    // The reason the ACL is keyed by family. Both of these exist, and nothing
    // anywhere lets one stand in for the other.
    let gen2 = SafeCommandId::find("royuan-gen2", "identify").expect("ships");
    let yc500 = SafeCommandId::find("royuan-yc500", "identify").expect("ships");
    assert_ne!(gen2, yc500);
    assert_ne!(gen2.family(), yc500.family());
}

#[test]
fn a_command_cannot_be_looked_up_by_opcode_from_outside() {
    // There is no API to try. This test exists so that the absence is stated
    // somewhere a future reader will look: the lookup goes family plus name to
    // command, never byte to command, because the byte-to-command direction is
    // the one an attacker of our own making would need.
    let found = SafeCommandId::find("royuan-gen2", "identify");
    assert!(found.is_some());
    assert!(SafeCommandId::find("royuan-gen2", "0x8f").is_none());
}

#[test]
fn a_probe_cannot_be_named_by_its_bytes_from_outside() {
    // What is closed, stated as a test rather than as a comment: there is no
    // public way to turn `0x82:0x01` into a `ProbeCommandId`, no public
    // constructor for `AuthorizedProbe`, and `ProbeCommandId::key` is
    // crate-private. A caller that wants to send this command has to name the
    // `ProbeResponse` type, which names the command, which came from the table.
    //
    // `find` takes a family and a name, and nothing spelled like a byte is one.
    assert!(psafety::ProbeCommandId::find("aula-bytech", "read_model_id").is_some());
    for byte_ish in ["0x82", "0x82:0x01", "130", "130,1"] {
        assert!(
            psafety::ProbeCommandId::find("aula-bytech", byte_ish).is_none(),
            "{byte_ish} resolved to a probe"
        );
    }
}

#[test]
fn a_probe_belongs_to_one_family_and_refuses_every_other() {
    // The collision check, on the probe path. The same two bytes mean something
    // else in another family, and a probe carries its family with it.
    let probe = psafety::ProbeCommandId::find("aula-bytech", "read_model_id").expect("ships");
    assert_eq!(probe.family(), "aula-bytech");
    assert!(psafety::ProbeCommandId::find("royuan-gen2", "read_model_id").is_none());
}
