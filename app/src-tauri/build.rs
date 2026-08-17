// Embeds a build identifier so a bug report or an exported device profile can
// be tied to an exact build. The version alone cannot do that: a report can
// arrive from any commit between two releases.
//
// Absent when this is not a git checkout (a release tarball), and marked when
// the working tree is dirty, so a local build is never mistaken for the released
// commit. Only this repository's own `.git` is consulted: a tarball unpacked
// inside an unrelated checkout would otherwise report that repository's commit,
// which is worse in a bug report than reporting none.

fn main() {
    if let Some(commit) = git_commit() {
        println!("cargo:rustc-env=PERIPHERAL_COMMIT={commit}");
    }
    // Without these the recorded commit is whatever HEAD was the last time this
    // script happened to run.
    for path in ["../../.git/HEAD", "../../.git/index"] {
        if std::path::Path::new(path).exists() {
            println!("cargo:rerun-if-changed={path}");
        }
    }
    tauri_build::build()
}

fn git_commit() -> Option<String> {
    if !std::path::Path::new("../../.git").exists() {
        return None;
    }
    let run = |args: &[&str]| {
        std::process::Command::new("git")
            .args(args)
            .output()
            .ok()
            .filter(|out| out.status.success())
            .map(|out| String::from_utf8_lossy(&out.stdout).trim().to_string())
    };
    let short = run(&["rev-parse", "--short", "HEAD"]).filter(|s| !s.is_empty())?;
    let dirty = run(&["status", "--porcelain"]).is_some_and(|s| !s.is_empty());
    Some(if dirty {
        format!("{short}-dirty")
    } else {
        short
    })
}
