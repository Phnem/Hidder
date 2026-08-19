@echo off
setlocal enabledelayedexpansion

:: 1. Navigate to script directory
cd /d "%~dp0"
title Peripheral Registry Ingest - 87 Brands Universe

echo ============================================================
echo         PERIPHERAL REGISTRY INGESTION PIPELINE
echo ============================================================

:: 2. Check Python installation
set "PYTHON_EXE="
where python >nul 2>nul
if not errorlevel 1 (
    set "PYTHON_EXE=python"
) else (
    where py >nul 2>nul
    if not errorlevel 1 (
        set "PYTHON_EXE=py -3"
    )
)

if "%PYTHON_EXE%"=="" (
    echo [ERROR] Python is not found in PATH!
    echo Please install Python 3.12+ from https://www.python.org/
    echo and make sure to check "Add Python to PATH" during installation.
    echo.
    pause
    exit /b 1
)

:: 3. Setup Virtual Environment
if not exist ".venv\Scripts\activate.bat" (
    echo [*] Creating virtual environment .venv...
    %PYTHON_EXE% -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment!
        pause
        exit /b 1
    )
)

:: 4. Activate Virtual Environment
call .venv\Scripts\activate.bat

:: 5. Install / Verify Requirements
if not exist ".venv\.requirements_installed" (
    echo [*] Verifying dependencies from requirements.txt...
    python -m pip install --upgrade pip
    pip install -r requirements.txt
    if errorlevel 1 (
        echo [WARNING] Some dependencies failed to install cleanly. Continuing...
    )
    echo [*] Verifying Playwright Chromium browser...
    playwright install chromium 2>nul
    echo installed > .venv\.requirements_installed
)

:: 6. Ensure runtime directories exist
if not exist "data\reports" mkdir "data\reports"
if not exist "artifacts" mkdir "artifacts"
if not exist "extracted" mkdir "extracted"
if not exist "logs" mkdir "logs"

:MENU
echo.
echo ============================================================
echo         PERIPHERAL REGISTRY INGESTION MENU (100 Brands)
echo ============================================================
echo [1] Full crawl - all 100 brands
echo [2] Current pilot brands (AULA, ATK, VXE, EPOMAKER, Keychron)
echo [3] Major brands (Batch A: Logitech, Razer, SteelSeries, Corsair, etc.)
echo [4] Chinese / enthusiast brands (Batch B: Ajazz, Attack Shark, etc.)
echo [5] Regional / custom brands (Batch C: A4Tech, Bloody, Ducky, etc.)
echo [6] One brand by name
echo [7] Metadata only (crawl catalogs without file downloads)
echo [8] Software / artifacts only
echo [9] Status and Brand list
echo [10] Last report
echo [0] Exit
echo ============================================================
set "choice="
set /p choice="Select option [0-10]: "

if "%choice%"=="1" (
    echo.
    echo [*] Running Full Ingest Crawl across all 100 canonical brands...
    python -m ingest.main run --batch all --verbose
    goto AFTER_RUN
)
if "%choice%"=="2" (
    echo.
    echo [*] Running Pilot Brands (AULA, ATK, VXE, EPOMAKER, Keychron)...
    python -m ingest.main run --batch pilot --verbose
    goto AFTER_RUN
)
if "%choice%"=="3" (
    echo.
    echo [*] Running Batch A (Major global / enthusiast brands)...
    python -m ingest.main run --batch A --verbose
    goto AFTER_RUN
)
if "%choice%"=="4" (
    echo.
    echo [*] Running Batch B (Chinese / enthusiast performance brands)...
    python -m ingest.main run --batch B --verbose
    goto AFTER_RUN
)
if "%choice%"=="5" (
    echo.
    echo [*] Running Batch C (Regional / custom ecosystem brands)...
    python -m ingest.main run --batch C --verbose
    goto AFTER_RUN
)
if "%choice%"=="6" (
    echo.
    set "target_brand="
    set /p target_brand="Enter brand name or slug (e.g. razer, wooting, bloody, steelseries): "
    if not "!target_brand!"=="" (
        echo [*] Running collector for '!target_brand!'...
        python -m ingest.main run --brand "!target_brand!" --verbose
    )
    goto AFTER_RUN
)
if "%choice%"=="7" (
    echo.
    echo [*] Running Metadata Only Crawl across all brands...
    python -m ingest.main run --batch all --metadata-only --verbose
    goto AFTER_RUN
)
if "%choice%"=="8" (
    echo.
    echo [*] Running Software / Artifacts Ingestion...
    python -m ingest.main run --batch all --verbose
    goto AFTER_RUN
)
if "%choice%"=="9" (
    echo.
    python -m ingest.main status
    python -m ingest.main list-brands
    echo.
    pause
    goto MENU
)
if "%choice%"=="10" (
    echo.
    echo [*] Listing latest report from data\reports...
    dir /b /o:-d data\reports\*.md 2>nul | (set /p latest_rep= & if defined latest_rep type "data\reports\!latest_rep!")
    echo.
    pause
    goto MENU
)
if "%choice%"=="0" (
    echo [*] Exiting.
    exit /b 0
)

echo [ERROR] Invalid option selected!
goto MENU

:AFTER_RUN
echo.
echo ============================================================
echo [*] Run finished.
echo ============================================================
pause
goto MENU
