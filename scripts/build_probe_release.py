"""Master release build script for Standalone Vetro Probe.

Produces self-contained, distributable Windows artifacts for testers:
1. Builds the PyInstaller Python Probe sidecar (zero-Python dependency).
2. Builds the Vite React frontend.
3. Builds the Tauri desktop executable (Vetro Probe.exe) and installers (NSIS/MSI).
4. Packages a portable ZIP in `dist/release/`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
PROBE_APP_DIR = ROOT_DIR / "probe-app"
DIST_RELEASE_DIR = ROOT_DIR / "dist" / "release"


def run_step(description: str, cmd: list[str], cwd: Path) -> None:
    print(f"\n=======================================================")
    print(f"[STEP] {description}")
    print(f"Command: {' '.join(cmd)}")
    print(f"CWD: {cwd}")
    print(f"=======================================================")
    res = subprocess.run(cmd, cwd=str(cwd), shell=(sys.platform == "win32"))
    if res.returncode != 0:
        print(f"[ERROR] Step failed with returncode {res.returncode}")
        sys.exit(res.returncode)


def check_worktree_cleanliness(allow_dirty: bool = False) -> tuple[str, bool]:
    commit = "unknown"
    dirty = False
    try:
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(ROOT_DIR), capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            commit = r.stdout.strip()[:12]
        s = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=str(ROOT_DIR), capture_output=True, text=True)
        if s.returncode == 0 and s.stdout.strip():
            dirty = True
            if not allow_dirty:
                print(f"[ERROR] Release build aborted: tracked working tree is dirty.")
                print(f"Modified tracked files:\n{s.stdout.strip()}")
                print(f"Commit or stash changes before building an official release, or pass --allow-dirty.")
                sys.exit(1)
    except Exception as e:
        print(f"[WARNING] Could not query git status: {e}")
    return commit, dirty


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Build Standalone Vetro Probe Release")
    parser.add_argument("--allow-dirty", action="store_true", help="Allow building from a dirty working tree")
    args = parser.parse_args()

    commit, dirty = check_worktree_cleanliness(allow_dirty=args.allow_dirty)
    print(f"[+] Release build starting for HEAD commit: {commit} (dirty={dirty})")

    print("=======================================================")
    print("VETRO PROBE: STANDALONE RELEASE BUILD")
    print("=======================================================")

    DIST_RELEASE_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Build PyInstaller sidecar
    sidecar_script = ROOT_DIR / "scripts" / "build_probe_sidecar.py"
    run_step("Building standalone Python Probe sidecar", [sys.executable, str(sidecar_script)], ROOT_DIR)

    # 2. Build frontend
    run_step("Building probe-app frontend (Vite/React)", ["npm", "run", "build"], PROBE_APP_DIR)

    # 3. Build Tauri binary and bundle
    run_step("Building Tauri standalone executable and bundle", ["npx", "tauri", "build"], PROBE_APP_DIR)

    # 4. Locate artifacts
    target_release = ROOT_DIR / "target" / "release"
    if not target_release.exists():
        target_release = PROBE_APP_DIR / "src-tauri" / "target" / "release"

    exe_candidates = [
        target_release / "Vetro Probe.exe",
        target_release / "vetro-probe-app.exe",
        target_release / "VetroProbe.exe",
    ]
    main_exe = next((p for p in exe_candidates if p.is_file()), None)
    if not main_exe:
        print(f"[ERROR] Could not find compiled main executable in {target_release}")
        sys.exit(1)

    bundle_dir = target_release / "bundle"
    if not bundle_dir.exists():
        bundle_dir = PROBE_APP_DIR / "src-tauri" / "target" / "release" / "bundle"

    # 5. Assemble Portable Package
    version = "0.3.0"
    portable_dir_name = f"VetroProbe-v{version}-win-x64-portable"
    portable_dir = DIST_RELEASE_DIR / portable_dir_name
    if portable_dir.exists():
        try:
            shutil.rmtree(portable_dir)
        except Exception:
            if sys.platform == "win32":
                subprocess.run(
                    ["powershell", "-Command", "Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Name -like '*Vetro*' -or $_.Name -like '*probe*' } | Stop-Process -Force -ErrorAction SilentlyContinue"],
                    shell=True,
                )
                import time
                time.sleep(1)
            shutil.rmtree(portable_dir, ignore_errors=True)
    portable_dir.mkdir(parents=True, exist_ok=True)

    shutil.copy2(main_exe, portable_dir / "Vetro Probe.exe")
    sidecar_bin = PROBE_APP_DIR / "src-tauri" / "binaries" / "vetro-probe-sidecar.exe"
    if sidecar_bin.is_file():
        shutil.copy2(sidecar_bin, portable_dir / "vetro-probe-sidecar.exe")

    readme_content = """Vetro Probe (Standalone)
=======================

Quick Start for Testers:
1. Connect your compatible gaming keyboard (e.g. HERO 84 HE).
2. Double-click "Vetro Probe.exe".
3. Wait for device detection and review the safe checks list.
4. Click "Start research".
5. When complete, click "Open results folder" or view technical details.

No installation, Python, or administrative setup is required.
"""
    (portable_dir / "README.txt").write_text(readme_content, encoding="utf-8")

    # Create ZIP archive
    zip_path = DIST_RELEASE_DIR / f"{portable_dir_name}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        for file in portable_dir.rglob("*"):
            if file.is_file():
                zipf.write(file, arcname=file.relative_to(DIST_RELEASE_DIR))

    # Copy installers to dist/release if present
    installer_files = []
    if bundle_dir.exists():
        for f in bundle_dir.rglob("*"):
            if f.is_file() and f.suffix.lower() in [".exe", ".msi", ".zip"]:
                dest = DIST_RELEASE_DIR / f.name
                shutil.copy2(f, dest)
                installer_files.append(dest)

    print("\n=======================================================")
    print("RELEASE BUILD COMPLETED SUCCESSFULLY")
    print("=======================================================")
    print(f"Portable Directory: {portable_dir}")
    print(f"Portable ZIP:       {zip_path} ({zip_path.stat().st_size / (1024*1024):.2f} MB)")
    print(f"Main Executable:    {main_exe}")
    for inst in installer_files:
        print(f"Installer:          {inst} ({inst.stat().st_size / (1024*1024):.2f} MB)")
    print("=======================================================")


if __name__ == "__main__":
    main()
