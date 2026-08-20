"""Diagnostic AV-Hardening Build Matrix Generator.

Builds diagnostic variants A, B, C, D, E, N1, analyzes PE imports,
calculates SHA-256 hashes, scans with Windows Defender locally,
and produces a structured diagnostic matrix report.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

# Ensure UTF-8 stdout
if sys.platform == "win32":
    import codecs
    import ctypes
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        ctypes.windll.kernel32.SetConsoleCP(65001)
    except Exception:
        pass

import pefile

community_dir = Path(__file__).resolve().parent
project_root = community_dir.parent
dist_diag_dir = community_dir / "dist_diagnostic"
build_diag_dir = community_dir / "build_diagnostic"
hook_crate_dir = community_dir / "probe_hook"
version_file = community_dir / "version_info.txt"
mpcmdrun_path = r"C:\Program Files\Windows Defender\MpCmdRun.exe"

dist_diag_dir.mkdir(parents=True, exist_ok=True)
build_diag_dir.mkdir(parents=True, exist_ok=True)


def compute_sha256(file_path: Path) -> str:
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def scan_with_defender(file_path: Path) -> dict[str, Any]:
    if not os.path.exists(mpcmdrun_path):
        return {"scanned": False, "status": "MpCmdRun.exe not found", "threat_found": False}
    
    cmd = [mpcmdrun_path, "-Scan", "-ScanType", "3", "-File", str(file_path.resolve())]
    try:
        res = subprocess.run(cmd, capture_output=True, timeout=30)
        output = (res.stdout.decode("utf-8", errors="replace") + "\n" + res.stderr.decode("utf-8", errors="replace")).strip()
        threat_found = "found no threats" not in output.lower() and res.returncode != 0
        return {
            "scanned": True,
            "returncode": res.returncode,
            "threat_found": threat_found,
            "status": "Clean (No threats)" if not threat_found else "Threat Detected",
        }
    except Exception as exc:
        return {"scanned": False, "status": f"Scan failed: {exc}", "threat_found": False}


def analyze_pe_imports(file_path: Path) -> dict[str, Any]:
    if not file_path.is_file():
        return {"error": "File not found"}
    try:
        pe = pefile.PE(str(file_path))
        imported_symbols = set()
        if hasattr(pe, "DIRECTORY_ENTRY_IMPORT"):
            for entry in pe.DIRECTORY_ENTRY_IMPORT:
                for imp in entry.imports:
                    if imp.name:
                        fn_name = imp.name.decode("utf-8", errors="replace").lower()
                        imported_symbols.add(fn_name)
                
        has_crt = "createremotethread" in imported_symbols or "createremotethreadex" in imported_symbols
        has_wpm = "writeprocessmemory" in imported_symbols
        has_vae = "virtualallocex" in imported_symbols
        has_open_proc = "openprocess" in imported_symbols

        pe.close()
        return {
            "has_CreateRemoteThread": has_crt,
            "has_WriteProcessMemory": has_wpm,
            "has_VirtualAllocEx": has_vae,
            "has_OpenProcess": has_open_proc,
            "total_imports_count": len(imported_symbols),
        }
    except Exception as exc:
        return {
            "error": str(exc),
            "has_CreateRemoteThread": False,
            "has_WriteProcessMemory": False,
            "has_VirtualAllocEx": False,
            "has_OpenProcess": False,
            "total_imports_count": 0,
        }


def build_rust_hook_dll() -> Path:
    cmd = ["cargo", "build", "--release"]
    subprocess.run(cmd, cwd=str(hook_crate_dir), check=True)
    dll_path = hook_crate_dir / "target" / "release" / "probe_hook.dll"
    
    assets_dir = community_dir / "probe" / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    target_dll = assets_dir / "Hidder.NativeObserver.x64.dll"
    shutil.copy2(dll_path, target_dll)
    shutil.copy2(dll_path, assets_dir / "probe_hook_x64.dll")
    return target_dll


def build_pyinstaller_variant(
    tag: str,
    name: str,
    entry_point: Path,
    onefile: bool,
    include_hook: bool,
) -> Path:
    if onefile:
        target = dist_diag_dir / f"{name}.exe"
    else:
        target = dist_diag_dir / name / f"{name}.exe"

    if target.is_file():
        print(f"[*] Reusing existing build {tag}: {target.name}")
        return target

    print(f"\n[*] Building PyInstaller {tag}: {name} (onefile={onefile}, include_hook={include_hook})...")
    mode_flag = "--onefile" if onefile else "--onedir"
    
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        mode_flag,
        "--noupx",
        "--name", name,
        "--distpath", str(dist_diag_dir),
        "--workpath", str(build_diag_dir / name),
        "--specpath", str(build_diag_dir),
        "--clean",
        "--version-file", str(version_file),
        "--paths", str(community_dir),
        "--paths", str(project_root),
    ]
    if include_hook:
        hook_dll = community_dir / "probe" / "assets" / "Hidder.NativeObserver.x64.dll"
        cmd.extend(["--add-data", f"{hook_dll};probe/assets"])
        
    cmd.append(str(entry_point))
    
    subprocess.run(cmd, cwd=str(community_dir), check=True)
    
    if not target.is_file():
        raise FileNotFoundError(f"Target {target} was not produced.")
    return target


def test_execution(target: Path) -> dict[str, Any]:
    print(f"[*] Testing execution of {target.name} --demo...")
    try:
        proc = subprocess.run([str(target), "--demo"], capture_output=True, timeout=15)
        stdout_str = proc.stdout.decode("utf-8", errors="replace")
        ok = proc.returncode == 0 and ("Готово" in stdout_str or "Done" in stdout_str)
        return {
            "executable_runs": True,
            "returncode": proc.returncode,
            "demo_successful": ok,
        }
    except Exception as exc:
        return {
            "executable_runs": False,
            "error": str(exc),
        }


def main() -> None:
    print("====================================================")
    print("      Hidder AV-Hardening Diagnostic Matrix         ")
    print("====================================================")
    
    # 0. Build Rust Hook DLL
    hook_dll = build_rust_hook_dll()
    hook_sha = compute_sha256(hook_dll)
    hook_pe = analyze_pe_imports(hook_dll)
    hook_def = scan_with_defender(hook_dll)
    print(f"[+] Native Hook DLL: {hook_dll.name}")
    print(f"    Size: {hook_dll.stat().st_size / 1024:.1f} KB | SHA256: {hook_sha}")
    print(f"    Defender: {hook_def['status']}")
    
    builds_config = [
        {
            "tag": "Build A",
            "desc": "FULL (WebHID + Native Hook), PyInstaller ONEFILE",
            "name": "Hidder_BuildA_Full_Onefile",
            "entry": community_dir / "PeripheralResearch.py",
            "builder": "pyinstaller",
            "onefile": True,
            "hook": True,
            "has_native": True,
            "has_cdp": True,
        },
        {
            "tag": "Build B",
            "desc": "FULL (WebHID + Native Hook), PyInstaller ONEDIR",
            "name": "Hidder_BuildB_Full_Onedir",
            "entry": community_dir / "PeripheralResearch.py",
            "builder": "pyinstaller",
            "onefile": False,
            "hook": True,
            "has_native": True,
            "has_cdp": True,
        },
        {
            "tag": "Build C",
            "desc": "WEB-ONLY (WebHID only, Native Hook excluded), PyInstaller ONEFILE",
            "name": "Hidder_BuildC_WebOnly_Onefile",
            "entry": community_dir / "diagnostic" / "PeripheralResearch_webonly.py",
            "builder": "pyinstaller",
            "onefile": True,
            "hook": False,
            "has_native": False,
            "has_cdp": True,
        },
        {
            "tag": "Build D",
            "desc": "WEB-ONLY (WebHID only, Native Hook excluded), PyInstaller ONEDIR",
            "name": "Hidder_BuildD_WebOnly_Onedir",
            "entry": community_dir / "diagnostic" / "PeripheralResearch_webonly.py",
            "builder": "pyinstaller",
            "onefile": False,
            "hook": False,
            "has_native": False,
            "has_cdp": True,
        },
        {
            "tag": "Build E",
            "desc": "MOCK-NATIVE (WebHID + Mock Native Hook, no CRT/WPM), PyInstaller ONEFILE",
            "name": "Hidder_BuildE_MockNative_Onefile",
            "entry": community_dir / "diagnostic" / "PeripheralResearch_mocknative.py",
            "builder": "pyinstaller",
            "onefile": True,
            "hook": False,
            "has_native": False,
            "has_cdp": True,
        },
    ]

    results = []

    for cfg in builds_config:
        try:
            exe_path = build_pyinstaller_variant(
                tag=cfg["tag"],
                name=cfg["name"],
                entry_point=cfg["entry"],
                onefile=cfg["onefile"],
                include_hook=cfg["hook"],
            )
            sha = compute_sha256(exe_path)
            size_bytes = exe_path.stat().st_size
            size_mb = size_bytes / (1024 * 1024)
            
            pe_info = analyze_pe_imports(exe_path)
            def_scan = scan_with_defender(exe_path)
            exec_test = test_execution(exe_path)
            
            res = {
                "tag": cfg["tag"],
                "description": cfg["desc"],
                "filename": exe_path.name,
                "relative_path": str(exe_path.relative_to(project_root)),
                "size_bytes": size_bytes,
                "size_mb": round(size_mb, 2),
                "sha256": sha,
                "onefile": cfg["onefile"],
                "has_native_observer": cfg["has_native"],
                "has_cdp_webhid": cfg["has_cdp"],
                "pe_imports": pe_info,
                "defender": def_scan,
                "execution_test": exec_test,
            }
            results.append(res)
            print(f"[+] {cfg['tag']} ({exe_path.name}): Size={size_mb:.2f} MB | Defender={def_scan['status']} | SHA256={sha}")
        except Exception as exc:
            print(f"[!] Error processing {cfg['tag']}: {exc}")
            results.append({"tag": cfg["tag"], "error": str(exc)})

    # Check Nuitka Build N1
    n1_path = dist_diag_dir / "Hidder_Nuitka_Full_Onefile.exe"
    if n1_path.is_file():
        sha = compute_sha256(n1_path)
        size_bytes = n1_path.stat().st_size
        size_mb = size_bytes / (1024 * 1024)
        pe_info = analyze_pe_imports(n1_path)
        def_scan = scan_with_defender(n1_path)
        exec_test = test_execution(n1_path)
        res_n1 = {
            "tag": "Build N1",
            "description": "FULL (WebHID + Native Hook), Nuitka C-Compiled ONEFILE",
            "filename": n1_path.name,
            "relative_path": str(n1_path.relative_to(project_root)),
            "size_bytes": size_bytes,
            "size_mb": round(size_mb, 2),
            "sha256": sha,
            "onefile": True,
            "has_native_observer": True,
            "has_cdp_webhid": True,
            "pe_imports": pe_info,
            "defender": def_scan,
            "execution_test": exec_test,
        }
        results.append(res_n1)
        print(f"[+] Build N1 ({n1_path.name}): Size={size_mb:.2f} MB | Defender={def_scan['status']} | SHA256={sha}")

    # Output report
    report_file = dist_diag_dir / "diagnostic_matrix_report.json"
    report_file.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n[+] Diagnostic report saved to: {report_file}")


if __name__ == "__main__":
    main()
