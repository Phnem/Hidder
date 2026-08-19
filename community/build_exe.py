"""Build script to create both PeripheralResearch_ru.exe and PeripheralResearch_en.exe using PyInstaller."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

community_dir = Path(__file__).resolve().parent
project_root = community_dir.parent
en_dir = community_dir / "en"
dist_dir = community_dir / "dist"
build_dir = community_dir / "build"
hook_crate_dir = community_dir / "probe_hook"

dist_dir.mkdir(parents=True, exist_ok=True)


def build_rust_hook_dll() -> Path:
    print("\n====================================================")
    print("Building native Rust probe_hook.dll (MinHook)...")
    print("====================================================")
    cmd = ["cargo", "build", "--release"]
    res = subprocess.run(cmd, cwd=str(hook_crate_dir))
    if res.returncode != 0:
        print("[ERROR] Failed to compile probe_hook.dll with cargo.")
        sys.exit(res.returncode)
        
    dll_path = hook_crate_dir / "target" / "release" / "probe_hook.dll"
    if not dll_path.is_file():
        print(f"[ERROR] {dll_path} does not exist after cargo build.")
        sys.exit(1)
        
    # Copy to assets directories
    ru_assets = community_dir / "probe" / "assets"
    en_assets = en_dir / "probe" / "assets"
    ru_assets.mkdir(parents=True, exist_ok=True)
    en_assets.mkdir(parents=True, exist_ok=True)
    
    shutil.copy2(dll_path, ru_assets / "probe_hook_x64.dll")
    shutil.copy2(dll_path, en_assets / "probe_hook_x64.dll")
    print(f"[SUCCESS] Native probe_hook_x64.dll ready ({dll_path.stat().st_size / 1024:.1f} KB)")
    return dll_path


def build_variant(name: str, entry_point: Path, search_paths: list[Path], assets_path: Path) -> Path:
    print(f"\n====================================================")
    print(f"Building {name}.exe from: {entry_point}")
    print(f"====================================================")
    
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--onefile",
        "--name", name,
        "--distpath", str(dist_dir),
        "--workpath", str(build_dir / name),
        "--specpath", str(community_dir),
        "--clean",
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
    print(f"[SUCCESS] Generated: {exe_path} ({size_mb:.2f} MB)")
    return exe_path


def main() -> None:
    # 0. Build native Rust hook DLL first
    build_rust_hook_dll()

    # 1. Build Russian Edition
    build_variant(
        name="PeripheralResearch_ru",
        entry_point=community_dir / "PeripheralResearch.py",
        search_paths=[community_dir, project_root],
        assets_path=community_dir / "probe" / "assets",
    )
    
    # 2. Build English Edition
    build_variant(
        name="PeripheralResearch_en",
        entry_point=en_dir / "PeripheralResearch.py",
        search_paths=[en_dir, community_dir, project_root],
        assets_path=en_dir / "probe" / "assets",
    )
    
    # Clean temporary build folders and specs
    if build_dir.is_dir():
        shutil.rmtree(build_dir, ignore_errors=True)
    for spec in community_dir.glob("*.spec"):
        spec.unlink(missing_ok=True)
        
    print("\n====================================================")
    print("All builds completed successfully!")
    print(f"Russian edition: {dist_dir / 'PeripheralResearch_ru.exe'}")
    print(f"English edition: {dist_dir / 'PeripheralResearch_en.exe'}")
    print("====================================================")


if __name__ == "__main__":
    main()
