@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

echo ====================================================
echo        Hidder - Peripheral Research Probe (RU)
echo ====================================================
echo.

set "PY="
python --version >nul 2>nul && set "PY=python"
if not defined PY (
    py -3 --version >nul 2>nul && set "PY=py -3"
)

if not defined PY (
    echo [!] Python 3 not found. Please install Python 3.10+ from https://www.python.org/downloads/
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo [*] Creating virtual environment .venv...
    %PY% -m venv .venv
    if errorlevel 1 (
        echo [!] Failed to create virtual environment.
        pause
        exit /b 1
    )
    echo [OK] Virtual environment created.
)

if not exist ".venv\.installed" (
    echo [*] Installing dependencies from requirements.txt...
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [!] Warning: Dependency installation issue. Continuing...
    ) else (
        type nul > ".venv\.installed"
        echo [OK] Dependencies installed.
    )
    echo.
)

if not exist "community\probe\assets\Hidder.NativeObserver.x64.dll" (
    where cargo >nul 2>nul
    if not errorlevel 1 (
        echo [*] Compiling native helper with cargo...
        cargo build --release --manifest-path "community\probe_hook\Cargo.toml"
        if exist "community\probe_hook\target\release\probe_hook.dll" (
            if not exist "community\probe\assets" mkdir "community\probe\assets"
            copy /y "community\probe_hook\target\release\probe_hook.dll" "community\probe\assets\Hidder.NativeObserver.x64.dll" >nul
            copy /y "community\probe_hook\target\release\probe_hook.dll" "community\probe\assets\probe_hook_x64.dll" >nul
            echo [OK] Native helper compiled.
        )
    )
)

echo [*] Starting Hidder...
echo.
".venv\Scripts\python.exe" "community\PeripheralResearch.py" %*
set "EXIT_CODE=%ERRORLEVEL%"

if %EXIT_CODE% neq 0 (
    echo.
    echo [!] Process finished with code: %EXIT_CODE%
    pause
)
exit /b %EXIT_CODE%
