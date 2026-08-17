// Turns `data/protocols/*.toml` into the closed `SafeCommandId` enum.
//
// Included by `build.rs` and compiled into the crate under `cfg(test)`, so the
// generator the tests exercise is the same text that runs during a build. It
// deliberately depends on nothing from the rest of the crate. Module comments
// here are `//` rather than `//!` for that reason: an inner doc comment cannot
// appear in the middle of the build script that includes this file.
//
// Everything here refuses rather than repairs. A file this module cannot make
// complete sense of fails the build; there is no lenient path, no default
// class, and no downgrade-and-continue. The reason is narrow: the only way an
// opcode can become executable is by appearing in the generated enum, so every
// quiet recovery here is a hole in the one boundary the product's safety rests
// on.

use std::collections::BTreeSet;
use std::fmt::Write as _;

/// A family file as written by a human, before any validation.
#[derive(Debug, serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct AclFile {
    schema: String,
    family: String,
    display_name: String,
    note: String,
    #[serde(default)]
    timing: TimingSection,
    #[serde(default)]
    opcode: Vec<OpcodeEntry>,
}

#[derive(Debug, Default, serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct TimingSection {
    #[serde(default)]
    class: std::collections::BTreeMap<String, ClassTiming>,
}

#[derive(Debug, serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct ClassTiming {
    min_gap_before_ms: u64,
    settle_after_ms: u64,
    evidence: String,
    note: String,
    #[serde(default)]
    max_operations: Option<u32>,
    #[serde(default)]
    per_window_ms: Option<u64>,
}

/// No `#[serde(default)]` on any field: an entry that forgets to say what class
/// it is, or where the knowledge came from, is an error rather than a guess.
#[derive(Debug, serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct OpcodeEntry {
    opcode: i64,
    name: String,
    class: String,
    evidence: String,
    note: String,
}

/// A class that may become a `SafeCommandId`.
///
/// Parsed once, at validation time, so that everything downstream of validation
/// works with a value that cannot be a misspelling. Code generation then has no
/// "this should not happen" branch to write, because the type no longer permits
/// the case.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum ExecutableClass {
    SafeRead,
    ProbeOk,
    SafeWrite,
    SlowFlash,
}

impl ExecutableClass {
    fn parse(class: &str) -> Option<Self> {
        match class {
            "safe_read" => Some(Self::SafeRead),
            "probe_ok" => Some(Self::ProbeOk),
            "safe_write" => Some(Self::SafeWrite),
            "slow_flash" => Some(Self::SlowFlash),
            _ => None,
        }
    }

    fn source_name(self) -> &'static str {
        match self {
            Self::SafeRead => "safe_read",
            Self::ProbeOk => "probe_ok",
            Self::SafeWrite => "safe_write",
            Self::SlowFlash => "slow_flash",
        }
    }

    fn rust_variant(self) -> &'static str {
        match self {
            Self::SafeRead => "SafeRead",
            Self::ProbeOk => "ProbeOk",
            Self::SafeWrite => "SafeWrite",
            Self::SlowFlash => "SlowFlash",
        }
    }

    fn writes(self) -> bool {
        matches!(self, Self::SafeWrite | Self::SlowFlash)
    }
}

const REFUSED_CLASSES: [&str; 2] = ["destructive", "unknown"];
/// Writing needs evidence from this project's own hardware. Prior art and vendor
/// software describe what a byte is *said* to do.
const WRITE_EVIDENCE: [&str; 1] = ["hardware"];
/// Reading tolerates a third party's hardware or a firmware dump, but not
/// someone's JavaScript and not an assumption.
const READ_EVIDENCE: [&str; 3] = ["hardware", "hardware_third_party", "firmware"];
const ALL_EVIDENCE: [&str; 5] = [
    "hardware",
    "hardware_third_party",
    "firmware",
    "vendor_js",
    "assumed",
];
const SCHEMA: &str = "peripheral.opcode-acl/1";

/// One command that survived validation and will exist in the generated enum.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Command {
    pub variant: String,
    pub family: String,
    pub name: String,
    pub opcode: u8,
    pub class: ExecutableClass,
    pub evidence: String,
}

/// One family's measured timing for one class.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Timing {
    pub family: String,
    pub class: ExecutableClass,
    pub min_gap_before_ms: u64,
    pub settle_after_ms: u64,
    pub max_operations: Option<u32>,
    pub per_window_ms: Option<u64>,
}

/// Everything the generator learned from one directory of family files.
#[derive(Debug, Default, PartialEq, Eq)]
pub struct Registry {
    pub commands: Vec<Command>,
    pub timings: Vec<Timing>,
    pub families: Vec<String>,
}

/// Reads one family file. `Err` is a build failure, always.
pub fn parse_family(source: &str) -> Result<(Vec<Command>, Vec<Timing>, String), String> {
    let file: AclFile = toml::from_str(source).map_err(|e| e.to_string())?;

    if file.schema != SCHEMA {
        return Err(format!(
            "unknown schema {:?}; this generator understands {SCHEMA} only",
            file.schema
        ));
    }
    check_family_id(&file.family)?;
    if file.display_name.trim().is_empty() || file.note.trim().is_empty() {
        return Err(format!(
            "family {}: display_name and note must both say something",
            file.family
        ));
    }

    let mut timings = Vec::new();
    let mut timed_classes = BTreeSet::new();
    for (class, timing) in &file.timing.class {
        let Some(parsed_class) = ExecutableClass::parse(class) else {
            return Err(format!(
                "family {}: timing declared for class {class:?}, which is not a class a command can have",
                file.family
            ));
        };
        if !ALL_EVIDENCE.contains(&timing.evidence.as_str()) {
            return Err(format!(
                "family {}: timing for {class} cites unknown evidence {:?}",
                file.family, timing.evidence
            ));
        }
        if timing.note.trim().is_empty() {
            return Err(format!(
                "family {}: timing for {class} must record where the number was measured",
                file.family
            ));
        }
        // A flash write with no quiet period afterwards is the exact shape that
        // wedged a board in prior art: 39 writes a second apart, no threshold
        // breached, endpoint stalled anyway.
        if parsed_class == ExecutableClass::SlowFlash && timing.settle_after_ms == 0 {
            return Err(format!(
                "family {}: slow_flash must declare a settle_after_ms; a flash write with no measured quiet period afterwards is how an endpoint gets wedged",
                file.family
            ));
        }
        if timing.max_operations.is_some() != timing.per_window_ms.is_some() {
            return Err(format!(
                "family {}: timing for {class} declares half a burst limit; max_operations and per_window_ms come as a pair",
                file.family
            ));
        }
        timed_classes.insert(parsed_class);
        timings.push(Timing {
            family: file.family.clone(),
            class: parsed_class,
            min_gap_before_ms: timing.min_gap_before_ms,
            settle_after_ms: timing.settle_after_ms,
            max_operations: timing.max_operations,
            per_window_ms: timing.per_window_ms,
        });
    }

    let mut commands = Vec::new();
    let mut seen_opcodes = BTreeSet::new();
    let mut seen_names = BTreeSet::new();

    for entry in &file.opcode {
        let opcode: u8 = u8::try_from(entry.opcode).map_err(|_| {
            format!(
                "family {}: opcode {:#x} does not fit in a byte",
                file.family, entry.opcode
            )
        })?;
        check_command_name(&file.family, &entry.name)?;
        if entry.note.trim().is_empty() {
            return Err(format!(
                "family {}: opcode {opcode:#04x} has no note; an ACL entry without a reason cannot be reviewed",
                file.family
            ));
        }
        if !ALL_EVIDENCE.contains(&entry.evidence.as_str()) {
            return Err(format!(
                "family {}: opcode {opcode:#04x} cites unknown evidence {:?}",
                file.family, entry.evidence
            ));
        }
        if !seen_opcodes.insert(opcode) {
            return Err(format!(
                "family {}: opcode {opcode:#04x} is listed twice; which classification wins is not something to leave to file order",
                file.family
            ));
        }
        if !seen_names.insert(entry.name.clone()) {
            return Err(format!(
                "family {}: command name {:?} is used twice",
                file.family, entry.name
            ));
        }

        let spelling = entry.class.as_str();
        if REFUSED_CLASSES.contains(&spelling) {
            // Recorded, reviewable, and never generated.
            continue;
        }
        let Some(class) = ExecutableClass::parse(spelling) else {
            return Err(format!(
                "family {}: opcode {opcode:#04x} has class {spelling:?}, which is not a class; there is no default and no nearest match",
                file.family
            ));
        };

        if class.writes() && !WRITE_EVIDENCE.contains(&entry.evidence.as_str()) {
            return Err(format!(
                "family {}: opcode {opcode:#04x} is class {spelling} on {} evidence; a write is earned by verifying it on hardware here, so record it as unknown until then",
                file.family, entry.evidence
            ));
        }
        if !class.writes() && !READ_EVIDENCE.contains(&entry.evidence.as_str()) {
            return Err(format!(
                "family {}: opcode {opcode:#04x} is class {spelling} on {} evidence, which is weaker than a firmware dump",
                file.family, entry.evidence
            ));
        }
        if !timed_classes.contains(&class) {
            return Err(format!(
                "family {}: opcode {opcode:#04x} is class {spelling}, but the family declares no measured timing for that class; there is no global default to fall back on",
                file.family
            ));
        }

        commands.push(Command {
            variant: variant_name(&file.family, &entry.name),
            family: file.family.clone(),
            name: entry.name.clone(),
            opcode,
            class,
            evidence: entry.evidence.clone(),
        });
    }

    Ok((commands, timings, file.family.clone()))
}

/// Merges the family files into one registry, rejecting collisions between them.
pub fn build_registry(sources: &[(String, String)]) -> Result<Registry, String> {
    let mut registry = Registry::default();
    for (origin, source) in sources {
        let (commands, timings, family) =
            parse_family(source).map_err(|e| format!("{origin}: {e}"))?;
        if registry.families.contains(&family) {
            return Err(format!("{origin}: family {family} is declared twice"));
        }
        registry.families.push(family);
        registry.commands.extend(commands);
        registry.timings.extend(timings);
    }
    // Deterministic output: the generated file must not depend on directory
    // iteration order, or CI's regeneration check turns into noise.
    registry.families.sort();
    registry.commands.sort_by(|a, b| {
        (&a.family, &a.name)
            .cmp(&(&b.family, &b.name))
            .then(a.opcode.cmp(&b.opcode))
    });
    registry
        .timings
        .sort_by(|a, b| (&a.family, &a.class).cmp(&(&b.family, &b.class)));

    let mut seen = BTreeSet::new();
    for command in &registry.commands {
        if !seen.insert(command.variant.clone()) {
            return Err(format!(
                "two families generate the same variant name {}",
                command.variant
            ));
        }
    }
    Ok(registry)
}

/// Emits the Rust source included by `command.rs`.
pub fn emit(registry: &Registry) -> String {
    let mut out = String::new();
    out.push_str(
        "// @generated by psafety/build.rs from data/protocols/*.toml. Do not edit.\n\
         //\n\
         // Every variant below is an opcode that a human classified as executable and\n\
         // backed with evidence. Opcodes classified `destructive` or `unknown`, and\n\
         // opcodes nobody classified at all, have no variant here and therefore no\n\
         // representation anywhere in the program.\n\n",
    );

    out.push_str("#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Debug)]\n");
    out.push_str("#[non_exhaustive]\n");
    out.push_str("pub enum SafeCommandId {\n");
    for (index, command) in registry.commands.iter().enumerate() {
        let _ = writeln!(
            out,
            "    /// `{}` on family `{}` ({}, evidence: {}).",
            command.name,
            command.family,
            command.class.source_name(),
            command.evidence
        );
        let _ = writeln!(out, "    {} = {index},", command.variant);
    }
    out.push_str("}\n\n");

    out.push_str("pub(crate) const COMMANDS: &[CommandRecord] = &[\n");
    for command in &registry.commands {
        let _ = writeln!(
            out,
            "    CommandRecord {{ id: SafeCommandId::{}, family: {:?}, name: {:?}, opcode: {:#04x}, class: OpcodeClass::{} }},",
            command.variant,
            command.family,
            command.name,
            command.opcode,
            command.class.rust_variant(),
        );
    }
    out.push_str("];\n\n");

    out.push_str("pub(crate) const TIMINGS: &[FamilyTiming] = &[\n");
    for timing in &registry.timings {
        let burst = match (timing.max_operations, timing.per_window_ms) {
            (Some(max), Some(window)) => {
                format!("Some(Burst {{ max_operations: {max}, per_window_ms: {window} }})")
            }
            _ => "None".to_owned(),
        };
        let _ = writeln!(
            out,
            "    FamilyTiming {{ family: {:?}, class: OpcodeClass::{}, min_gap_before_ms: {}, settle_after_ms: {}, burst: {burst} }},",
            timing.family,
            timing.class.rust_variant(),
            timing.min_gap_before_ms,
            timing.settle_after_ms,
        );
    }
    out.push_str("];\n\n");

    out.push_str("/// Families the ACL knows about, whether or not they have any command.\n");
    out.push_str("pub(crate) const FAMILIES: &[&str] = &[\n");
    for family in &registry.families {
        let _ = writeln!(out, "    {family:?},");
    }
    out.push_str("];\n");
    out
}

fn check_family_id(family: &str) -> Result<(), String> {
    let ok = !family.is_empty()
        && family
            .chars()
            .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '-')
        && !family.starts_with('-')
        && !family.ends_with('-');
    if ok {
        Ok(())
    } else {
        Err(format!(
            "family id {family:?} must be lowercase kebab-case; it is a key shared with the device registry"
        ))
    }
}

fn check_command_name(family: &str, name: &str) -> Result<(), String> {
    let ok = !name.is_empty()
        && name
            .chars()
            .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '_')
        && name.starts_with(|c: char| c.is_ascii_lowercase());
    if ok {
        Ok(())
    } else {
        Err(format!(
            "family {family}: command name {name:?} must be lowercase snake_case"
        ))
    }
}

/// `royuan-gen2` + `read_led_params` -> `RoyuanGen2ReadLedParams`.
fn variant_name(family: &str, name: &str) -> String {
    let mut out = String::new();
    for word in family.split('-').chain(name.split('_')) {
        let mut chars = word.chars();
        if let Some(first) = chars.next() {
            out.extend(first.to_uppercase());
            out.push_str(chars.as_str());
        }
    }
    out
}

#[cfg(test)]
mod tests {
    // A test asserts by failing loudly. The workspace forbids these in product
    // code precisely so that a panic means something; here it is the protocol.
    #![allow(clippy::expect_used, clippy::panic, clippy::unwrap_used)]

    use super::*;

    /// A family file that is correct in every respect, so each test below can
    /// break exactly one thing and attribute the refusal to it.
    fn good_family() -> String {
        r#"
schema       = "peripheral.opcode-acl/1"
family       = "test-family"
display_name = "Test family"
note         = "fixture"

[timing.class.safe_read]
min_gap_before_ms = 12
settle_after_ms   = 0
evidence          = "hardware"
note              = "measured"

[[opcode]]
opcode   = 0x80
name     = "revision"
class    = "safe_read"
evidence = "hardware"
note     = "reads the firmware version"
"#
        .to_owned()
    }

    fn parse(source: &str) -> Result<Vec<Command>, String> {
        parse_family(source).map(|(commands, _, _)| commands)
    }

    // --- the refusals that matter most ----------------------------------

    #[test]
    fn a_destructive_opcode_produces_no_command() {
        let source =
            good_family().replace(r#"class    = "safe_read""#, r#"class    = "destructive""#);
        let commands = parse(&source).expect("a destructive entry is recorded, not an error");
        assert!(
            commands.is_empty(),
            "a destructive opcode must not become a command: {commands:?}"
        );
    }

    #[test]
    fn an_unknown_opcode_produces_no_command() {
        let source = good_family().replace(r#"class    = "safe_read""#, r#"class    = "unknown""#);
        let commands = parse(&source).expect("an unknown entry is recorded, not an error");
        assert!(commands.is_empty(), "unknown must stay unexecutable");
    }

    #[test]
    fn a_destructive_opcode_does_not_appear_in_the_generated_source() {
        // Not the same assertion as the first test: this one is about the text
        // that actually reaches the compiler. An opcode that survives into the
        // emitted file as a constant is one `unsafe` accessor away from being
        // sendable, even with no variant of its own.
        let source =
            good_family().replace(r#"class    = "safe_read""#, r#"class    = "destructive""#);
        let registry = build_registry(&[("fixture".to_owned(), source)]).expect("valid file");
        let emitted = emit(&registry);
        assert!(
            !emitted.contains("0x80"),
            "the refused opcode's value leaked into generated code:\n{emitted}"
        );
        assert!(!emitted.contains("revision"), "and neither should its name");
    }

    #[test]
    fn an_opcode_with_no_class_is_refused() {
        let source = good_family().replace("class    = \"safe_read\"\n", "");
        let error = parse(&source).expect_err("absence of a class is not permission");
        assert!(error.contains("class"), "unhelpful message: {error}");
    }

    #[test]
    fn an_opcode_nobody_listed_simply_does_not_exist() {
        let commands = parse(&good_family()).expect("valid");
        assert!(
            !commands.iter().any(|c| c.opcode == 0xAC),
            "an unlisted opcode must have no command"
        );
    }

    #[test]
    fn a_misspelt_class_is_refused_rather_than_guessed() {
        let source = good_family().replace(r#"class    = "safe_read""#, r#"class    = "saferead""#);
        let error = parse(&source).expect_err("no nearest match");
        assert!(error.contains("saferead"), "unhelpful message: {error}");
    }

    #[test]
    fn a_misspelt_field_name_is_refused() {
        // Without deny_unknown_fields this file would parse, the class field
        // would be missing, and the entry would vanish -- or worse, default.
        let source = good_family().replace(
            "evidence = \"hardware\"\nnote     = \"reads",
            "evidenc = \"hardware\"\nnote     = \"reads",
        );
        let error = parse(&source).expect_err("an unknown field is a typo, not an extension");
        assert!(error.contains("evidenc"), "unhelpful message: {error}");
    }

    // --- evidence has to earn the class ---------------------------------

    #[test]
    fn a_write_on_third_party_evidence_is_refused() {
        let source = good_family()
            .replace(r#"class    = "safe_read""#, r#"class    = "safe_write""#)
            .replace(
                "evidence = \"hardware\"\nnote     = \"reads",
                "evidence = \"hardware_third_party\"\nnote     = \"reads",
            );
        let error = parse(&source).expect_err("someone else's board does not earn our write");
        assert!(
            error.contains("unknown"),
            "the message should say what to do instead: {error}"
        );
    }

    #[test]
    fn a_write_on_vendor_javascript_is_refused() {
        let source = good_family()
            .replace(r#"class    = "slow_flash""#, r#"class    = "safe_write""#)
            .replace(r#"class    = "safe_read""#, r#"class    = "safe_write""#)
            .replace(
                "evidence = \"hardware\"\nnote     = \"reads",
                "evidence = \"vendor_js\"\nnote     = \"reads",
            );
        assert!(parse(&source).is_err(), "vendor JS never earns a write");
    }

    #[test]
    fn a_read_on_vendor_javascript_is_refused() {
        let source = good_family().replace(
            "evidence = \"hardware\"\nnote     = \"reads",
            "evidence = \"vendor_js\"\nnote     = \"reads",
        );
        assert!(parse(&source).is_err(), "a read still needs real evidence");
    }

    #[test]
    fn a_write_verified_here_is_allowed() {
        // The rule must not be "no writes ever", or it would be indistinguishable
        // from a bug the first time a write is genuinely earned.
        let source = good_family()
            .replace("[timing.class.safe_read]", "[timing.class.safe_write]")
            .replace(r#"class    = "safe_read""#, r#"class    = "safe_write""#);
        let commands = parse(&source).expect("hardware evidence earns a write");
        assert_eq!(commands.len(), 1);
        assert_eq!(commands[0].class, ExecutableClass::SafeWrite);
    }

    // --- timing is family data, never a constant ------------------------

    #[test]
    fn a_class_with_no_measured_timing_is_refused() {
        let source = good_family().replace("[timing.class.safe_read]", "[timing.class.probe_ok]");
        let error = parse(&source).expect_err("no timing, no command");
        assert!(
            error.contains("no global default"),
            "the message should rule out inventing one: {error}"
        );
    }

    #[test]
    fn a_flash_class_with_no_settle_period_is_refused() {
        let source = good_family()
            .replace("[timing.class.safe_read]", "[timing.class.slow_flash]")
            .replace(r#"class    = "safe_read""#, r#"class    = "slow_flash""#);
        let error = parse(&source).expect_err("settle_after_ms = 0 on a flash write");
        assert!(
            error.contains("settle_after_ms"),
            "unhelpful message: {error}"
        );
    }

    #[test]
    fn half_a_burst_limit_is_refused() {
        let source = good_family().replace(
            "settle_after_ms   = 0",
            "settle_after_ms   = 0\nmax_operations    = 2",
        );
        assert!(
            parse(&source).is_err(),
            "a window without a count is not a limit"
        );
    }

    // --- per family, not global -----------------------------------------

    #[test]
    fn the_same_opcode_in_two_families_is_two_distinct_commands() {
        let gen2 = good_family()
            .replace("test-family", "royuan-gen2")
            .replace(r#"name     = "revision""#, r#"name     = "debounce""#);
        let yc500 = good_family()
            .replace("test-family", "royuan-yc500")
            .replace(r#"name     = "revision""#, r#"name     = "options""#);
        let registry = build_registry(&[("gen2".to_owned(), gen2), ("yc500".to_owned(), yc500)])
            .expect("two families");

        assert_eq!(registry.commands.len(), 2);
        assert_eq!(registry.commands[0].opcode, registry.commands[1].opcode);
        assert_ne!(registry.commands[0].family, registry.commands[1].family);
        assert_ne!(registry.commands[0].variant, registry.commands[1].variant);
    }

    #[test]
    fn a_duplicate_opcode_within_one_family_is_refused() {
        let source = good_family()
            + r#"
[[opcode]]
opcode   = 0x80
name     = "revision_again"
class    = "safe_read"
evidence = "hardware"
note     = "same byte, second opinion"
"#;
        let error = parse(&source).expect_err("two classifications for one byte");
        assert!(error.contains("listed twice"), "unhelpful message: {error}");
    }

    #[test]
    fn a_duplicate_family_is_refused() {
        let sources = [
            ("a".to_owned(), good_family()),
            ("b".to_owned(), good_family()),
        ];
        assert!(build_registry(&sources).is_err(), "one file per family");
    }

    // --- housekeeping that keeps the generated file reviewable ----------

    #[test]
    fn an_entry_without_a_note_is_refused() {
        let source = good_family().replace(
            r#"note     = "reads the firmware version""#,
            r#"note     = "  ""#,
        );
        assert!(parse(&source).is_err(), "an unreviewable entry is refused");
    }

    #[test]
    fn an_unknown_schema_is_refused() {
        let source = good_family().replace("peripheral.opcode-acl/1", "peripheral.opcode-acl/2");
        assert!(parse(&source).is_err(), "a future schema is not this one");
    }

    #[test]
    fn a_family_with_no_opcodes_is_valid_and_yields_nothing() {
        let source = r#"
schema       = "peripheral.opcode-acl/1"
family       = "known-nothing"
display_name = "Known nothing"
note         = "the board is here, the knowledge is not"
"#;
        let (commands, timings, family) = parse_family(source).expect("a family may know nothing");
        assert!(commands.is_empty());
        assert!(timings.is_empty());
        assert_eq!(family, "known-nothing");
    }

    #[test]
    fn output_does_not_depend_on_file_order() {
        let a = good_family().replace("test-family", "aaa-family");
        let b = good_family().replace("test-family", "bbb-family");
        let forward = build_registry(&[("1".to_owned(), a.clone()), ("2".to_owned(), b.clone())]);
        let backward = build_registry(&[("2".to_owned(), b), ("1".to_owned(), a)]);
        assert_eq!(forward.expect("valid"), backward.expect("valid"));
    }

    #[test]
    fn variant_names_are_derived_predictably() {
        assert_eq!(
            variant_name("royuan-gen2", "read_led_params"),
            "RoyuanGen2ReadLedParams"
        );
    }
}
