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
fn the_product_entry_for_our_board_stays_empty() {
    // `aula-hero84-he` is a *product* entry. The protocol family our board
    // speaks is `aula-bytech`, and commands live there. This one must never
    // acquire any: a product is not a protocol.
    let earned: Vec<_> = SafeCommandId::all()
        .filter(|id| id.family() == "aula-hero84-he")
        .map(SafeCommandId::name)
        .collect();
    assert!(earned.is_empty(), "commands on a product entry: {earned:?}");
}

#[test]
fn the_aula_family_has_exactly_one_earned_command() {
    // One command, verified on our own hardware, and read-only. This is the
    // whole of what TICKET-12 earned, and the assertion is here so that the
    // second one has to be added deliberately.
    let earned: Vec<_> = SafeCommandId::all()
        .filter(|id| id.family() == "aula-bytech")
        .map(|id| (id.name(), id.class()))
        .collect();
    assert_eq!(earned, [("read_model_id", OpcodeClass::SafeRead)]);
}

#[test]
fn the_commands_awaiting_a_first_exchange_are_the_ones_the_plan_names() {
    // An empty probe surface is the steady state, and this list should return to
    // empty. While it is not empty, each entry is a command deliberately
    // mid-bootstrap: recovered from a vendor artifact, never sent from here,
    // reachable only through a gate that costs one confirmation per send.
    //
    // Asserted as an exact set rather than as "non-empty", so a third entry
    // appearing has to be a deliberate edit that shows up in review.
    let mut probes: Vec<_> = psafety::ProbeCommandId::all()
        .map(|id| (id.family(), id.name()))
        .collect();
    probes.sort_unstable();
    assert_eq!(
        probes,
        [
            ("aula-bytech", "read_key_travel"),
            ("aula-bytech", "read_travel_precision"),
        ],
        "the probe surface changed without the plan saying so"
    );
}

#[test]
fn nothing_is_executable_through_both_doors() {
    // The bootstrap rule as a property: a command mid-bootstrap is absent from
    // `SafeCommandId`, so its only door is the one that costs a confirmation
    // and yields exactly one send. Vacuous while nothing is bootstrapping, and
    // that is fine -- it is here to fail the moment both are true at once.
    for id in psafety::ProbeCommandId::all() {
        assert!(
            SafeCommandId::find(id.family(), id.name()).is_none(),
            "{}::{} is executable through the production gate as well",
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
        [
            "aula-bytech",
            "aula-hero84-he",
            "royuan-gen2",
            "royuan-yc500"
        ],
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
fn a_command_cannot_be_named_by_its_bytes_from_outside() {
    // What is closed, stated as a test rather than as a comment: there is no
    // public way to turn `0x82:0x01` into a command id, no public constructor
    // for `AuthorizedCommand` or `AuthorizedProbe`, and both `key` accessors are
    // crate-private. A caller that wants to send this has to name it.
    assert!(SafeCommandId::find("aula-bytech", "read_model_id").is_some());
    for byte_ish in ["0x82", "0x82:0x01", "130", "130,1", "0x82,0x01"] {
        assert!(
            SafeCommandId::find("aula-bytech", byte_ish).is_none(),
            "{byte_ish} resolved to a command"
        );
        assert!(psafety::ProbeCommandId::find("aula-bytech", byte_ish).is_none());
    }
}

#[test]
fn the_aula_command_belongs_to_one_family_and_no_other() {
    // The collision check. The same two bytes mean something else in another
    // family, and a command carries its family with it.
    let id = SafeCommandId::find("aula-bytech", "read_model_id").expect("ships");
    assert_eq!(id.family(), "aula-bytech");
    for other in ["royuan-gen2", "royuan-yc500", "aula-hero84-he"] {
        assert!(SafeCommandId::find(other, "read_model_id").is_none());
    }
}
