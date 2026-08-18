//! Generates `SafeCommandId` from `data/protocols/*.toml` at build time.
//!
//! The generator itself lives in `src/codegen.rs` and is included rather than
//! duplicated, so the code the tests exercise is the code that runs here.
//!
//! Why a build script rather than a checked-in generated file: the closed enum
//! has to be a consequence of the reviewed data, with no step in between where a
//! human could add a variant the data does not justify. A checked-in file can be
//! edited; this one cannot exist without the data saying so.
//!
//! Known cost, recorded rather than discovered later (TICKET-11 risk): this puts
//! `data/protocols` on the build graph, so a workspace build now depends on the
//! repository layout above the crate. If that becomes awkward -- vendored
//! builds, publishing, or a second consumer of the same data -- the answer is an
//! xtask that regenerates a checked-in file with CI verifying it matches, not a
//! hand-maintained enum.
//!
//! The lints the workspace turns on for product code are turned off here on
//! purpose. In a build script a panic is the delivery mechanism: refusing to
//! produce a binary is the strongest thing this file can do, and it is exactly
//! what an unusable ACL should cause.
#![allow(clippy::expect_used, clippy::panic, clippy::unwrap_used)]

include!("src/codegen.rs");

fn main() {
    let manifest = std::path::PathBuf::from(
        std::env::var("CARGO_MANIFEST_DIR").expect("cargo sets CARGO_MANIFEST_DIR"),
    );
    let protocols = manifest
        .parent()
        .and_then(|p| p.parent())
        .expect("crates/psafety sits two levels below the repository root")
        .join("data/protocols");

    println!("cargo:rerun-if-changed=src/codegen.rs");
    println!("cargo:rerun-if-changed={}", protocols.display());

    let mut sources = Vec::new();
    let entries = std::fs::read_dir(&protocols)
        .unwrap_or_else(|e| panic!("cannot read {}: {e}", protocols.display()));
    for entry in entries {
        let path = entry.expect("directory entry").path();
        if path.extension().and_then(|e| e.to_str()) != Some("toml") {
            continue;
        }
        println!("cargo:rerun-if-changed={}", path.display());
        let text = std::fs::read_to_string(&path)
            .unwrap_or_else(|e| panic!("cannot read {}: {e}", path.display()));
        sources.push((path.display().to_string(), text));
    }

    // Order the inputs so the build script's output does not depend on the
    // filesystem's iteration order.
    sources.sort();

    // A refusal here fails the build on purpose. Every message names the file,
    // the opcode and what to do instead, because the person reading it is
    // holding a keyboard they would like not to brick.
    let registry = match build_registry(&sources) {
        Ok(registry) => registry,
        Err(error) => panic!("opcode ACL is not usable: {error}"),
    };

    let out_dir = std::path::PathBuf::from(std::env::var("OUT_DIR").expect("cargo sets OUT_DIR"));
    for (name, text) in [
        ("safe_command_id.rs", emit(&registry)),
        ("probe_command_id.rs", emit_probe(&registry)),
    ] {
        let out = out_dir.join(name);
        std::fs::write(&out, text)
            .unwrap_or_else(|e| panic!("cannot write {}: {e}", out.display()));
    }
}
