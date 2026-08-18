// Turns `data/protocols/*.toml` into the closed `SafeCommandId` and
// `ProbeCommandId` enums.
//
// Included by `build.rs` and compiled into the crate under `cfg(test)`, so the
// generator the tests exercise is the same text that runs during a build. It
// deliberately depends on nothing from the rest of the crate. Module comments
// here are `//` rather than `//!` for that reason: an inner doc comment cannot
// appear in the middle of the build script that includes this file.
//
// Everything here refuses rather than repairs. A file this module cannot make
// complete sense of fails the build; there is no lenient path, no default
// class, and no downgrade-and-continue. The reason is narrow: the only way a
// command can become executable is by appearing in a generated enum, so every
// quiet recovery here is a hole in the one boundary the product's safety rests
// on.
//
// Two enums, not one, as of TICKET-12. The new class `bootstrap_probe` produces
// a `ProbeCommandId` rather than a `SafeCommandId`: a different type reaching a
// different gate under much tighter rules. The reason is the bootstrap problem
// -- a `safe_read` must be backed by hardware evidence, and the only way to get
// hardware evidence is to send the command once. That first send is the probe
// path, and keeping it a separate type is what stops it from becoming a
// permanent weakening of the read rule.
//
// `probe_ok` is deliberately NOT that class and keeps its existing meaning: a
// production read, repeatable and paced, whose purpose is identifying a board
// that has not been identified yet. It still generates a `SafeCommandId` and
// still costs hardware evidence. Merging the two would have quietly turned
// ROYUAN's `identify` and `revision` into one-shot confirmed probes.

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
    command: Vec<CommandEntry>,
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

/// No `#[serde(default)]` on any field that carries meaning: an entry that
/// forgets to say what class it is, or where the knowledge came from, is an
/// error rather than a guess.
///
/// The three key fields are optional individually because the shapes are
/// mutually exclusive, and which of them is present is checked by hand below
/// rather than by the deserialiser, so the error message can say what the two
/// legal shapes are.
#[derive(Debug, serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct CommandEntry {
    #[serde(default)]
    opcode: Option<i64>,
    #[serde(default)]
    group: Option<i64>,
    #[serde(default)]
    subcommand: Option<i64>,
    name: String,
    class: String,
    evidence: String,
    note: String,
}

/// How a family addresses one command. Mirrors `crate::key::CommandKey`, which
/// this module cannot name: it is included by a build script that has no access
/// to the crate being built.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum Key {
    Opcode(u8),
    GroupSubcommand { group: u8, subcommand: u8 },
}

impl Key {
    fn rust_expr(self) -> String {
        match self {
            Key::Opcode(opcode) => format!("CommandKey::Opcode({opcode:#04x})"),
            Key::GroupSubcommand { group, subcommand } => format!(
                "CommandKey::GroupSubcommand {{ group: {group:#04x}, subcommand: {subcommand:#04x} }}"
            ),
        }
    }

    fn describe(self) -> String {
        match self {
            Key::Opcode(opcode) => format!("{opcode:#04x}"),
            Key::GroupSubcommand { group, subcommand } => format!("{group:#04x}:{subcommand:#04x}"),
        }
    }

    /// The group byte a key occupies, for the overlap check.
    fn group_byte(self) -> u8 {
        match self {
            Key::Opcode(opcode) => opcode,
            Key::GroupSubcommand { group, .. } => group,
        }
    }

    fn is_bare_opcode(self) -> bool {
        matches!(self, Key::Opcode(_))
    }
}

/// A class that may become an executable id.
///
/// Parsed once, at validation time, so that everything downstream of validation
/// works with a value that cannot be a misspelling. Code generation then has no
/// "this should not happen" branch to write, because the type no longer permits
/// the case.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum ExecutableClass {
    SafeRead,
    ProbeOk,
    BootstrapProbe,
    SafeWrite,
    SlowFlash,
}

impl ExecutableClass {
    fn parse(class: &str) -> Option<Self> {
        match class {
            "safe_read" => Some(Self::SafeRead),
            "probe_ok" => Some(Self::ProbeOk),
            "bootstrap_probe" => Some(Self::BootstrapProbe),
            "safe_write" => Some(Self::SafeWrite),
            "slow_flash" => Some(Self::SlowFlash),
            _ => None,
        }
    }

    fn source_name(self) -> &'static str {
        match self {
            Self::SafeRead => "safe_read",
            Self::ProbeOk => "probe_ok",
            Self::BootstrapProbe => "bootstrap_probe",
            Self::SafeWrite => "safe_write",
            Self::SlowFlash => "slow_flash",
        }
    }

    fn rust_variant(self) -> &'static str {
        match self {
            Self::SafeRead => "SafeRead",
            Self::ProbeOk => "ProbeOk",
            Self::BootstrapProbe => "BootstrapProbe",
            Self::SafeWrite => "SafeWrite",
            Self::SlowFlash => "SlowFlash",
        }
    }

    fn writes(self) -> bool {
        matches!(self, Self::SafeWrite | Self::SlowFlash)
    }

    /// Whether this class is dispatched through the probe path rather than the
    /// production one.
    fn probes(self) -> bool {
        matches!(self, Self::BootstrapProbe)
    }
}

const REFUSED_CLASSES: [&str; 2] = ["destructive", "unknown"];
/// Writing needs evidence from this project's own hardware. Prior art and
/// vendor software describe what a byte is *said* to do.
const WRITE_EVIDENCE: [&str; 1] = ["hardware"];
/// Reading tolerates a third party's hardware or a firmware dump, but not a
/// vendor artifact and not an assumption. Deliberately unchanged by TICKET-12:
/// the bootstrap case got its own class rather than a hole in this one.
const READ_EVIDENCE: [&str; 3] = ["hardware", "hardware_third_party", "firmware"];
/// A probe is the one place a vendor artifact is admissible, because a probe is
/// how a vendor artifact is turned into hardware evidence. `assumed` is not
/// here and must never be: a probe is a real command reaching real firmware,
/// and "we think this is a read" is not a reason to send one.
const PROBE_EVIDENCE: [&str; 4] = [
    "hardware",
    "hardware_third_party",
    "firmware",
    "vendor_artifact",
];
const ALL_EVIDENCE: [&str; 5] = [
    "hardware",
    "hardware_third_party",
    "firmware",
    "vendor_artifact",
    "assumed",
];
const SCHEMA: &str = "peripheral.opcode-acl/2";

/// One command that survived validation and will exist in a generated enum.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Command {
    pub variant: String,
    pub family: String,
    pub name: String,
    pub key: Key,
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
    /// Commands reaching the production gate: `safe_read`, `safe_write`,
    /// `slow_flash`.
    pub commands: Vec<Command>,
    /// Commands reaching the probe gate: `probe_ok`, and nothing else.
    pub probes: Vec<Command>,
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
        // A probe performs exactly one operation per authorisation and the gate
        // that runs it is consumed doing so. There is no second operation for a
        // cadence to sit between, so a measured interval here would describe
        // something that cannot happen.
        if parsed_class.probes() {
            return Err(format!(
                "family {}: timing declared for {class}; a bootstrap probe is one operation per authorisation with no second operation to pace against, so there is no cadence to measure",
                file.family
            ));
        }
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
    let mut seen_keys: BTreeSet<Key> = BTreeSet::new();
    let mut seen_names = BTreeSet::new();

    for entry in &file.command {
        let key = parse_key(&file.family, entry)?;
        check_command_name(&file.family, &entry.name)?;
        if entry.note.trim().is_empty() {
            return Err(format!(
                "family {}: command {} has no note; an ACL entry without a reason cannot be reviewed",
                file.family,
                key.describe()
            ));
        }
        if !ALL_EVIDENCE.contains(&entry.evidence.as_str()) {
            return Err(format!(
                "family {}: command {} cites unknown evidence {:?}",
                file.family,
                key.describe(),
                entry.evidence
            ));
        }
        if !seen_keys.insert(key) {
            return Err(format!(
                "family {}: command {} is listed twice; which classification wins is not something to leave to file order",
                file.family,
                key.describe()
            ));
        }
        // The hole TICKET-12 found: authorising a bare `0x82` in a family that
        // also addresses `0x82:0x01` would grant every subcommand of that group,
        // including the fifteen nobody has classified.
        if let Some(other) = seen_keys
            .iter()
            .find(|o| **o != key && o.group_byte() == key.group_byte() && o.is_bare_opcode() != key.is_bare_opcode())
        {
            return Err(format!(
                "family {}: command {} overlaps {}; a bare opcode and a group/subcommand pair cannot share a leading byte, because authorising the bare one would authorise every subcommand of that group",
                file.family,
                key.describe(),
                other.describe()
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
                "family {}: command {} has class {spelling:?}, which is not a class; there is no default and no nearest match",
                file.family,
                key.describe()
            ));
        };

        let admissible: &[&str] = if class.writes() {
            &WRITE_EVIDENCE
        } else if class.probes() {
            &PROBE_EVIDENCE
        } else {
            &READ_EVIDENCE
        };
        if !admissible.contains(&entry.evidence.as_str()) {
            return Err(match class {
                ExecutableClass::SafeWrite | ExecutableClass::SlowFlash => format!(
                    "family {}: command {} is class {spelling} on {} evidence; a write is earned by verifying it on hardware here, so record it as unknown until then",
                    file.family,
                    key.describe(),
                    entry.evidence
                ),
                ExecutableClass::BootstrapProbe => format!(
                    "family {}: command {} is class {spelling} on {} evidence; a bootstrap probe needs a vendor artifact at minimum, and an assumption is not one",
                    file.family,
                    key.describe(),
                    entry.evidence
                ),
                ExecutableClass::SafeRead | ExecutableClass::ProbeOk => format!(
                    "family {}: command {} is class {spelling} on {} evidence, which is weaker than a firmware dump; if this is how the command is about to be sent for the first time, it is a bootstrap_probe, not a {spelling}",
                    file.family,
                    key.describe(),
                    entry.evidence
                ),
            });
        }
        // Probes are exempt: see the refusal of `[timing.class.probe_ok]` above.
        if !class.probes() && !timed_classes.contains(&class) {
            return Err(format!(
                "family {}: command {} is class {spelling}, but the family declares no measured timing for that class; there is no global default to fall back on",
                file.family,
                key.describe()
            ));
        }

        commands.push(Command {
            variant: variant_name(&file.family, &entry.name),
            family: file.family.clone(),
            name: entry.name.clone(),
            key,
            class,
            evidence: entry.evidence.clone(),
        });
    }

    Ok((commands, timings, file.family.clone()))
}

/// Exactly one of the two shapes, never both and never neither.
fn parse_key(family: &str, entry: &CommandEntry) -> Result<Key, String> {
    let byte = |label: &str, value: i64| -> Result<u8, String> {
        u8::try_from(value).map_err(|_| {
            format!("family {family}: {label} {value:#x} does not fit in a byte")
        })
    };
    match (entry.opcode, entry.group, entry.subcommand) {
        (Some(opcode), None, None) => Ok(Key::Opcode(byte("opcode", opcode)?)),
        (None, Some(group), Some(subcommand)) => Ok(Key::GroupSubcommand {
            group: byte("group", group)?,
            subcommand: byte("subcommand", subcommand)?,
        }),
        (None, Some(_), None) | (None, None, Some(_)) => Err(format!(
            "family {family}: command {:?} declares half a group/subcommand pair; both bytes together are the command's identity",
            entry.name
        )),
        (Some(_), _, _) => Err(format!(
            "family {family}: command {:?} declares both an opcode and a group/subcommand pair; a command has one identity",
            entry.name
        )),
        (None, None, None) => Err(format!(
            "family {family}: command {:?} declares no opcode and no group/subcommand pair",
            entry.name
        )),
    }
}

/// Merges the family files into one registry, rejecting collisions between them.
pub fn build_registry(sources: &[(String, String)]) -> Result<Registry, String> {
    let mut registry = Registry::default();
    let mut all = Vec::new();
    for (origin, source) in sources {
        let (commands, timings, family) =
            parse_family(source).map_err(|e| format!("{origin}: {e}"))?;
        if registry.families.contains(&family) {
            return Err(format!("{origin}: family {family} is declared twice"));
        }
        registry.families.push(family);
        all.extend(commands);
        registry.timings.extend(timings);
    }

    for command in all {
        if command.class.probes() {
            registry.probes.push(command);
        } else {
            registry.commands.push(command);
        }
    }

    // Deterministic output: the generated file must not depend on directory
    // iteration order, or CI's regeneration check turns into noise.
    registry.families.sort();
    let by_family_and_name = |a: &Command, b: &Command| {
        (&a.family, &a.name)
            .cmp(&(&b.family, &b.name))
            .then(a.key.cmp(&b.key))
    };
    registry.commands.sort_by(by_family_and_name);
    registry.probes.sort_by(by_family_and_name);
    registry
        .timings
        .sort_by(|a, b| (&a.family, &a.class).cmp(&(&b.family, &b.class)));

    let mut seen = BTreeSet::new();
    for command in registry.commands.iter().chain(registry.probes.iter()) {
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
         // Every variant below is a command a human classified as executable and\n\
         // backed with evidence. Commands classified `destructive` or `unknown`, and\n\
         // commands nobody classified at all, have no variant here and therefore no\n\
         // representation anywhere in the program. `probe_ok` commands are not here\n\
         // either: they generate `ProbeCommandId` instead, in probe_command_id.rs.\n\n",
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
            "    CommandRecord {{ id: SafeCommandId::{}, family: {:?}, name: {:?}, key: {}, class: OpcodeClass::{} }},",
            command.variant,
            command.family,
            command.name,
            command.key.rust_expr(),
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

/// Emits the Rust source included by `probe.rs`.
///
/// A separate enum rather than a flag on the first one. A flag can be ignored
/// by a caller that has the value in hand; a type cannot, and the probe path
/// takes a `ProbeCommandId` and the production path takes a `SafeCommandId`, so
/// neither can be handed to the other by mistake.
pub fn emit_probe(registry: &Registry) -> String {
    let mut out = String::new();
    out.push_str(
        "// @generated by psafety/build.rs from data/protocols/*.toml. Do not edit.\n\
         //\n\
         // Every variant below is a `probe_ok` command: read-only, backed by a vendor\n\
         // artifact or better, and not yet verified against hardware here. These are\n\
         // the only commands the probe gate will run, and each run needs its own\n\
         // confirmation. No other class generates a variant in this enum.\n\n",
    );

    out.push_str("#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Debug)]\n");
    out.push_str("#[non_exhaustive]\n");
    out.push_str("pub enum ProbeCommandId {\n");
    for (index, command) in registry.probes.iter().enumerate() {
        let _ = writeln!(
            out,
            "    /// `{}` on family `{}` (probe_ok, evidence: {}).",
            command.name, command.family, command.evidence
        );
        let _ = writeln!(out, "    {} = {index},", command.variant);
    }
    out.push_str("}\n\n");

    out.push_str("pub(crate) const PROBES: &[ProbeRecord] = &[\n");
    for command in &registry.probes {
        let _ = writeln!(
            out,
            "    ProbeRecord {{ id: ProbeCommandId::{}, family: {:?}, name: {:?}, key: {} }},",
            command.variant,
            command.family,
            command.name,
            command.key.rust_expr(),
        );
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
schema       = "peripheral.opcode-acl/2"
family       = "test-family"
display_name = "Test family"
note         = "fixture"

[timing.class.safe_read]
min_gap_before_ms = 12
settle_after_ms   = 0
evidence          = "hardware"
note              = "measured"

[[command]]
opcode   = 0x80
name     = "revision"
class    = "safe_read"
evidence = "hardware"
note     = "reads the firmware version"
"#
        .to_owned()
    }

    /// The `aula-bytech` shape: a group/subcommand pair, probe class, vendor
    /// artifact evidence, and no timing section at all.
    fn good_probe_family() -> String {
        r#"
schema       = "peripheral.opcode-acl/2"
family       = "test-family"
display_name = "Test family"
note         = "fixture"

[[command]]
group      = 0x82
subcommand = 0x01
name       = "read_model_id"
class      = "bootstrap_probe"
evidence   = "vendor_artifact"
note       = "first read, not yet verified on hardware here"
"#
        .to_owned()
    }

    fn parse(source: &str) -> Result<Vec<Command>, String> {
        parse_family(source).map(|(commands, _, _)| commands)
    }

    // --- the refusals that matter most ----------------------------------

    #[test]
    fn a_destructive_command_produces_nothing() {
        let source =
            good_family().replace(r#"class    = "safe_read""#, r#"class    = "destructive""#);
        let commands = parse(&source).expect("a destructive entry is recorded, not an error");
        assert!(
            commands.is_empty(),
            "a destructive command must not be generated: {commands:?}"
        );
    }

    #[test]
    fn an_unknown_command_produces_nothing() {
        let source = good_family().replace(r#"class    = "safe_read""#, r#"class    = "unknown""#);
        let commands = parse(&source).expect("an unknown entry is recorded, not an error");
        assert!(commands.is_empty(), "unknown must stay unexecutable");
    }

    #[test]
    fn a_destructive_command_does_not_appear_in_either_generated_source() {
        // Not the same assertion as the first test: this one is about the text
        // that actually reaches the compiler. A value that survives into an
        // emitted file as a constant is one `unsafe` accessor away from being
        // sendable, even with no variant of its own.
        let source =
            good_family().replace(r#"class    = "safe_read""#, r#"class    = "destructive""#);
        let registry = build_registry(&[("fixture".to_owned(), source)]).expect("valid file");
        for emitted in [emit(&registry), emit_probe(&registry)] {
            assert!(
                !emitted.contains("0x80"),
                "the refused value leaked into generated code:\n{emitted}"
            );
            assert!(!emitted.contains("revision"), "and neither should its name");
        }
    }

    #[test]
    fn a_command_with_no_class_is_refused() {
        let source = good_family().replace("class    = \"safe_read\"\n", "");
        let error = parse(&source).expect_err("absence of a class is not permission");
        assert!(error.contains("class"), "unhelpful message: {error}");
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

    // --- command identity is a key, never a loose byte -------------------

    #[test]
    fn a_group_subcommand_pair_is_one_command() {
        let commands = parse(&good_probe_family()).expect("valid probe family");
        assert_eq!(commands.len(), 1);
        assert_eq!(
            commands[0].key,
            Key::GroupSubcommand {
                group: 0x82,
                subcommand: 0x01
            }
        );
    }

    #[test]
    fn half_a_group_subcommand_pair_is_refused() {
        let source = good_probe_family().replace("subcommand = 0x01\n", "");
        let error = parse(&source).expect_err("a group alone is not a command");
        assert!(error.contains("half"), "unhelpful message: {error}");
    }

    #[test]
    fn declaring_both_shapes_is_refused() {
        let source = good_probe_family().replace("group      = 0x82", "opcode     = 0x82\ngroup      = 0x82");
        let error = parse(&source).expect_err("a command has one identity");
        assert!(error.contains("one identity"), "unhelpful message: {error}");
    }

    #[test]
    fn a_bare_opcode_cannot_share_a_leading_byte_with_a_pair() {
        // The hole: `0x82` alone would authorise every subcommand of the group.
        let source = good_probe_family()
            + r#"
[[command]]
opcode   = 0x82
name     = "whole_group"
class    = "probe_ok"
evidence = "vendor_artifact"
note     = "this must not be expressible alongside 0x82:0x01"
"#;
        let error = parse(&source).expect_err("a group and its subcommand cannot coexist");
        assert!(error.contains("overlaps"), "unhelpful message: {error}");
    }

    #[test]
    fn a_duplicate_key_within_one_family_is_refused() {
        let source = good_family()
            + r#"
[[command]]
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
    fn two_subcommands_of_one_group_are_two_commands() {
        let source = good_probe_family()
            + r#"
[[command]]
group      = 0x82
subcommand = 0x02
name       = "read_firmware_version"
class      = "bootstrap_probe"
evidence   = "vendor_artifact"
note       = "a different command in the same group"
"#;
        let commands = parse(&source).expect("distinct subcommands are distinct commands");
        assert_eq!(commands.len(), 2);
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
    fn a_write_on_a_vendor_artifact_is_refused() {
        let source = good_family()
            .replace(r#"class    = "safe_read""#, r#"class    = "safe_write""#)
            .replace(
                "evidence = \"hardware\"\nnote     = \"reads",
                "evidence = \"vendor_artifact\"\nnote     = \"reads",
            );
        assert!(
            parse(&source).is_err(),
            "a vendor artifact never earns a write"
        );
    }

    #[test]
    fn a_safe_read_on_a_vendor_artifact_is_still_refused() {
        // The rule TICKET-12 was told not to weaken. The bootstrap case has its
        // own class; this one keeps its price.
        let source = good_family().replace(
            "evidence = \"hardware\"\nnote     = \"reads",
            "evidence = \"vendor_artifact\"\nnote     = \"reads",
        );
        let error = parse(&source).expect_err("a read still needs real evidence");
        assert!(
            error.contains("bootstrap_probe"),
            "the message should point at the class that does allow this: {error}"
        );
    }

    #[test]
    fn a_bootstrap_probe_on_an_assumption_is_refused() {
        let source = good_probe_family().replace(r#"evidence   = "vendor_artifact""#, r#"evidence   = "assumed""#);
        assert!(
            parse(&source).is_err(),
            "a probe reaches real firmware; an assumption is not a reason to send one"
        );
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

    // --- the two enums stay separate -------------------------------------

    #[test]
    fn a_probe_never_becomes_a_safe_command() {
        let registry = build_registry(&[("fixture".to_owned(), good_probe_family())])
            .expect("valid probe family");
        assert!(
            registry.commands.is_empty(),
            "a bootstrap_probe entry produced a production command: {:?}",
            registry.commands
        );
        assert_eq!(registry.probes.len(), 1);
        assert!(
            !emit(&registry).contains("0x82"),
            "a probe's bytes leaked into the SafeCommandId table"
        );
        assert!(emit_probe(&registry).contains("0x82"));
    }

    #[test]
    fn a_safe_read_never_becomes_a_probe() {
        let registry =
            build_registry(&[("fixture".to_owned(), good_family())]).expect("valid family");
        assert!(registry.probes.is_empty());
        assert_eq!(registry.commands.len(), 1);
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
    fn a_bootstrap_probe_needs_no_timing_and_may_not_declare_any() {
        parse(&good_probe_family()).expect("a probe family needs no timing section");
        let source = good_probe_family()
            + r#"
[timing.class.bootstrap_probe]
min_gap_before_ms = 10
settle_after_ms   = 0
evidence          = "hardware"
note              = "invented"
"#;
        let error = parse(&source).expect_err("there is no cadence between one operation");
        assert!(error.contains("cadence"), "unhelpful message: {error}");
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
    fn the_same_key_in_two_families_is_two_distinct_commands() {
        let gen2 = good_family()
            .replace("test-family", "royuan-gen2")
            .replace(r#"name     = "revision""#, r#"name     = "debounce""#);
        let yc500 = good_family()
            .replace("test-family", "royuan-yc500")
            .replace(r#"name     = "revision""#, r#"name     = "options""#);
        let registry = build_registry(&[("gen2".to_owned(), gen2), ("yc500".to_owned(), yc500)])
            .expect("two families");

        assert_eq!(registry.commands.len(), 2);
        assert_eq!(registry.commands[0].key, registry.commands[1].key);
        assert_ne!(registry.commands[0].family, registry.commands[1].family);
        assert_ne!(registry.commands[0].variant, registry.commands[1].variant);
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
        let source = good_family().replace("peripheral.opcode-acl/2", "peripheral.opcode-acl/3");
        assert!(parse(&source).is_err(), "a future schema is not this one");
    }

    #[test]
    fn the_previous_schema_is_refused_rather_than_half_understood() {
        // v1 spelled the entries `[[opcode]]` and had no group/subcommand shape.
        // Accepting it here would mean two meanings for one file format.
        let source = good_family()
            .replace("peripheral.opcode-acl/2", "peripheral.opcode-acl/1")
            .replace("[[command]]", "[[opcode]]");
        assert!(parse(&source).is_err(), "v1 is not v2");
    }

    #[test]
    fn a_family_with_no_commands_is_valid_and_yields_nothing() {
        let source = r#"
schema       = "peripheral.opcode-acl/2"
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
