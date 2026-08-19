"""Build script to create the standalone portable PeripheralResearch_en.exe using PyInstaller."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

en_dir = Path(__file__).resolve().parent
community_dir = en_dir.parent
project_root = community_dir.parent
entry_point = en_dir / "PeripheralResearch.py"
dist_dir = community_dir / "dist"
build_dir = en_dir / "build"

dist_dir.mkdir(parents=True, exist_ok=True)

print(f"Building portable English executable from: {entry_point}")
print(f"Output directory: {dist_dir}")

cmd = [
    sys.executable,
    "-m",
    "PyInstaller",
    "--onefile",
    "--name", "PeripheralResearch_en",
    "--paths", str(en_dir),
    "--paths", str(community_dir),
    "--paths", str(project_root),
    "--distpath", str(dist_dir),
    "--workpath", str(build_dir),
    "--specpath", str(en_dir),
    "--clean",
    str(entry_point)
]

print("Executing PyInstaller...")
res = subprocess.run(cmd, cwd=str(en_dir))

if res.returncode == 0:
    exe_path = dist_dir / "PeripheralResearch_en.exe"
    if exe_path.is_file():
        size_mb = exe_path.stat().st_size / (1024 * 1024)
        print(f"\n[SUCCESS] Portable English executable generated: {exe_path}")
        print(f"[SIZE] {size_mb:.2f} MB")
        
        # Clean build artifacts
        if build_dir.is_dir():
            shutil.rmtree(build_dir, ignore_errors=True)
        spec_file = en_dir / "PeripheralResearch_en.spec"
        if spec_file.is_file():
            spec_file.unlink(missing_ok=True)
    else:
        print("[ERROR] Executable not found after build.")
        sys.exit(1)
else:
    print(f"[ERROR] PyInstaller failed with code: {res.returncode}")
    sys.exit(res.returncode)
