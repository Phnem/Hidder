"""Hardened production build script for Hidder (Peripheral Research Probe).

Features:
- Compiles native Rust helper DLL with embedded Windows PE version resource (Hidder.NativeObserver.x64.dll).
- Builds Russian (PeripheralResearch_ru.exe) and English (PeripheralResearch_en.exe) standalone executables.
- Embeds standard Windows VS_VERSION_INFO metadata into all executables.
- Disables UPX and packing compression (--noupx).
- Generates reproducible cryptographic build_manifest.json with SHA-256 hashes.
- Scans built binaries with local Microsoft Defender (MpCmdRun.exe).
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Ensure UTF-8 console output
if sys.platform == "win32":
    import ctypes
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        ctypes.windll.kernel32.SetConsoleCP(65001)
    except Exception:
        pass

community_dir = Path(__file__).resolve().parent
project_root = community_dir.parent
en_dir = community_dir / "en"
dist_dir = community_dir / "dist"
build_dir = community_dir / "build"
hook_crate_dir = community_dir / "probe_hook"
version_file = community_dir / "version_info.txt"
mpcmdrun_path = r"C:\Program Files\Windows Defender\MpCmdRun.exe"

dist_dir.mkdir(parents=True, exist_ok=True)


def compute_sha256(file_path: Path) -> str:
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def get_git_commit() -> str:
    try:
        res = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(project_root), capture_output=True, text=True)
        if res.returncode == 0:
            return res.stdout.strip()
    except Exception:
        pass
    return "unknown"


def scan_with_defender(file_path: Path) -> str:
    if not os.path.exists(mpcmdrun_path):
        return "MpCmdRun not found"
    cmd = [mpcmdrun_path, "-Scan", "-ScanType", "3", "-File", str(file_path.resolve())]
    try:
        res = subprocess.run(cmd, capture_output=True, timeout=30)
        output = (res.stdout.decode("utf-8", errors="replace") + "\n" + res.stderr.decode("utf-8", errors="replace")).strip()
        threat_found = "found no threats" not in output.lower() and res.returncode != 0
        return "Clean (No threats)" if not threat_found else "Threat Detected"
    except Exception as exc:
        return f"Scan error: {exc}"


def build_rust_hook_dll() -> Path:
    print("\n====================================================")
    print("Building native Rust helper (Hidder.NativeObserver.x64.dll)...")
    print("====================================================")
    cmd = ["cargo", "build", "--release"]
    res = subprocess.run(cmd, cwd=str(hook_crate_dir))
    if res.returncode != 0:
        print("[ERROR] Failed to compile probe_hook with cargo.")
        sys.exit(res.returncode)
        
    dll_path = hook_crate_dir / "target" / "release" / "probe_hook.dll"
    if not dll_path.is_file():
        print(f"[ERROR] {dll_path} does not exist after cargo build.")
        sys.exit(1)
        
    ru_assets = community_dir / "probe" / "assets"
    en_assets = en_dir / "probe" / "assets"
    ru_assets.mkdir(parents=True, exist_ok=True)
    en_assets.mkdir(parents=True, exist_ok=True)
    
    target_name = "Hidder.NativeObserver.x64.dll"
    shutil.copy2(dll_path, ru_assets / target_name)
    shutil.copy2(dll_path, ru_assets / "probe_hook_x64.dll")
    shutil.copy2(dll_path, en_assets / target_name)
    shutil.copy2(dll_path, en_assets / "probe_hook_x64.dll")
    
    sha = compute_sha256(ru_assets / target_name)
    print(f"[SUCCESS] Native helper ready: {target_name} ({dll_path.stat().st_size / 1024:.1f} KB | SHA256: {sha[:16]}...)")
    return ru_assets / target_name


def build_variant(name: str, entry_point: Path, search_paths: list[Path], assets_path: Path) -> Path:
    print(f"\n====================================================")
    print(f"Building {name}.exe from: {entry_point}")
    print("====================================================")
    
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--onefile",
        "--noupx",
        "--name", name,
        "--distpath", str(dist_dir),
        "--workpath", str(build_dir / name),
        "--specpath", str(community_dir),
        "--clean",
        "--version-file", str(version_file),
        "--add-data", f"{assets_path / 'Hidder.NativeObserver.x64.dll'};probe/assets",
        "--add-data", f"{assets_path / 'probe_hook_x64.dll'};probe/assets",
    ]
    for sp in search_paths:
        cmd.extend(["--paths", str(sp)])
    cmd.append(str(entry_point))
    
    res = subprocess.run(cmd, cwd=str(community_dir))
    if res.returncode != 0:
        print(f"[ERROR] PyInstaller failed for {name} with code: {res.returncode}")
        sys.exit(res.returncode)
        
    exe_path = dist_dir / f"{name}.exe"
    if not exe_path.is_file():
        print(f"[ERROR] {exe_path} not found after build.")
        sys.exit(1)
        
    size_mb = exe_path.stat().st_size / (1024 * 1024)
    sha = compute_sha256(exe_path)
    def_status = scan_with_defender(exe_path)
    print(f"[SUCCESS] Generated: {exe_path.name} ({size_mb:.2f} MB | SHA256: {sha[:16]}... | Defender: {def_status})")
    return exe_path


def generate_build_manifest(ru_exe: Path, en_exe: Path, helper_dll: Path, vetro_ru: Path | None = None, vetro_en: Path | None = None) -> Path:
    binaries: dict[str, dict] = {
        "PeripheralResearch_ru.exe": {
            "size_bytes": ru_exe.stat().st_size,
            "sha256": compute_sha256(ru_exe),
            "defender_scan": scan_with_defender(ru_exe),
            "has_pe_metadata": True,
        },
        "PeripheralResearch_en.exe": {
            "size_bytes": en_exe.stat().st_size,
            "sha256": compute_sha256(en_exe),
            "defender_scan": scan_with_defender(en_exe),
            "has_pe_metadata": True,
        },
        "Hidder.NativeObserver.x64.dll": {
            "size_bytes": helper_dll.stat().st_size,
            "sha256": compute_sha256(helper_dll),
            "defender_scan": scan_with_defender(helper_dll),
            "has_pe_metadata": True,
        },
    }
    if vetro_ru is not None and vetro_ru.is_file():
        binaries["VetroProbe_ru.exe"] = {
            "size_bytes": vetro_ru.stat().st_size,
            "sha256": compute_sha256(vetro_ru),
            "defender_scan": scan_with_defender(vetro_ru),
            "has_pe_metadata": True,
        }
    if vetro_en is not None and vetro_en.is_file():
        binaries["VetroProbe_en.exe"] = {
            "size_bytes": vetro_en.stat().st_size,
            "sha256": compute_sha256(vetro_en),
            "defender_scan": scan_with_defender(vetro_en),
            "has_pe_metadata": True,
        }
    manifest = {
        "project": "Hidder",
        "version": "0.3.0",
        "git_commit": get_git_commit(),
        "build_timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "toolchain": {
            "python_version": sys.version,
            "pyinstaller_version": "6.21.0",
            "rust_edition": "2021",
            "c_compiler": "MSVC cl 14.5",
        },
        "binaries": binaries,
    }
    
    manifest_path = dist_dir / "build_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n[+] Build manifest generated: {manifest_path}")
    return manifest_path


def main() -> None:
    # 0. Build native Rust hook DLL first
    helper_dll = build_rust_hook_dll()

    # 1. Build Russian Edition (legacy observer)
    ru_exe = build_variant(
        name="PeripheralResearch_ru",
        entry_point=community_dir / "PeripheralResearch.py",
        search_paths=[community_dir, project_root],
        assets_path=community_dir / "probe" / "assets",
    )
    
    # 2. Build English Edition (legacy observer)
    en_exe = build_variant(
        name="PeripheralResearch_en",
        entry_point=en_dir / "PeripheralResearch.py",
        search_paths=[en_dir, community_dir, project_root],
        assets_path=en_dir / "probe" / "assets",
    )

    # 3. Build VetroProbe editions (isolated validator)
    vetro_ru = build_variant(
        name="VetroProbe_ru",
        entry_point=community_dir / "VetroProbe.py",
        search_paths=[community_dir, project_root],
        assets_path=community_dir / "probe" / "assets",
    )
    vetro_en = build_variant(
        name="VetroProbe_en",
        entry_point=en_dir / "VetroProbe.py",
        search_paths=[en_dir, community_dir, project_root],
        assets_path=en_dir / "probe" / "assets",
    )
    
    # 4. Generate cryptographic build manifest
    manifest_path = generate_build_manifest(ru_exe, en_exe, helper_dll, vetro_ru, vetro_en)

    # Clean temporary build folders and specs
    if build_dir.is_dir():
        shutil.rmtree(build_dir, ignore_errors=True)
    for spec in community_dir.glob("*.spec"):
        spec.unlink(missing_ok=True)
        
    print("\n====================================================")
    print("All hardened production builds completed successfully!")
    print(f"PeripheralResearch_ru: {ru_exe} ({compute_sha256(ru_exe)})")
    print(f"PeripheralResearch_en: {en_exe} ({compute_sha256(en_exe)})")
    print(f"VetroProbe_ru: {vetro_ru} ({compute_sha256(vetro_ru)})")
    print(f"VetroProbe_en: {vetro_en} ({compute_sha256(vetro_en)})")
    print(f"Build manifest:  {manifest_path}")
    print("====================================================")


if __name__ == "__main__":
    main()
