//! Generates the built-in registry from `data/devices/*.toml`.
//!
//! Same arrangement as `psafety`: the generator lives in `src/codegen.rs` and is
//! included rather than duplicated, so the code the tests exercise is the code
//! that runs here. The same cost applies too, and is recorded rather than
//! discovered later -- this puts `data/devices` on the build graph. If that ever
//! becomes awkward, the answer is an xtask writing a checked-in file with CI
//! verifying it matches, not a hand-maintained table.
#![allow(clippy::expect_used, clippy::panic, clippy::unwrap_used)]

include!("src/codegen.rs");

fn main() {
    let manifest = std::path::PathBuf::from(
        std::env::var("CARGO_MANIFEST_DIR").expect("cargo sets CARGO_MANIFEST_DIR"),
    );
    let devices = manifest
        .parent()
        .and_then(|p| p.parent())
        .expect("crates/pregistry sits two levels below the repository root")
        .join("data/devices");

    println!("cargo:rerun-if-changed=src/codegen.rs");
    println!("cargo:rerun-if-changed={}", devices.display());

    let mut sources = Vec::new();
    let entries = std::fs::read_dir(&devices)
        .unwrap_or_else(|e| panic!("cannot read {}: {e}", devices.display()));
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
    sources.sort();

    let registry = match build_registry(&sources) {
        Ok(registry) => registry,
        Err(error) => panic!("device registry is not usable: {error}"),
    };

    let out = std::path::PathBuf::from(std::env::var("OUT_DIR").expect("cargo sets OUT_DIR"))
        .join("registry.rs");
    std::fs::write(&out, emit(&registry))
        .unwrap_or_else(|e| panic!("cannot write {}: {e}", out.display()));
}
