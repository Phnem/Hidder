<#
.SYNOPSIS
    Signs Hidder release binaries using signtool.exe if a certificate is available.

.DESCRIPTION
    Accepts executable/DLL paths, looks up Windows SDK signtool.exe,
    and applies Authenticode signature with timestamping.
    If no certificate or signtool is provided, gracefully reports unsigned status.

.PARAMETER Path
    Path to file(s) to sign (e.g. .\dist\PeripheralResearch_ru.exe).

.PARAMETER CertThumbprint
    Optional SHA1 thumbprint from Cert:\CurrentUser\My or Cert:\LocalMachine\My.

.PARAMETER PfxPath
    Optional path to .pfx certificate file.

.PARAMETER PfxPassword
    Optional password for .pfx certificate.

.PARAMETER TimestampServer
    Timestamp server URL (default: http://timestamp.digicert.com).
#>

param(
    [Parameter(Mandatory = $false, Position = 0)]
    [string[]]$Path = @("dist\PeripheralResearch_ru.exe", "dist\PeripheralResearch_en.exe"),

    [Parameter(Mandatory = $false)]
    [string]$CertThumbprint,

    [Parameter(Mandatory = $false)]
    [string]$PfxPath,

    [Parameter(Mandatory = $false)]
    [string]$PfxPassword,

    [Parameter(Mandatory = $false)]
    [string]$TimestampServer = "http://timestamp.digicert.com"
)

Write-Host "====================================================" -ForegroundColor Cyan
Write-Host "         Hidder Authenticode Signing Pipeline        " -ForegroundColor Cyan
Write-Host "====================================================" -ForegroundColor Cyan

# 1. Locate signtool.exe
$signtoolPaths = @(
    "C:\Program Files (x86)\Windows Kits\10\bin\*\x64\signtool.exe",
    "C:\Program Files\Microsoft Visual Studio\*\*\SDK\ScopeCppSDK\vc15\SDK\bin\signtool.exe"
)
$signtool = Resolve-Path $signtoolPaths -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Path

if (-not $signtool) {
    $signtoolCmd = Get-Command "signtool.exe" -ErrorAction SilentlyContinue
    if ($signtoolCmd) {
        $signtool = $signtoolCmd.Source
    }
}

if (-not $signtool) {
    Write-Host "[i] signtool.exe not found on system." -ForegroundColor Yellow
    Write-Host "    Binaries will remain unsigned." -ForegroundColor Yellow
    exit 0
}

Write-Host "[+] Found signtool: $signtool" -ForegroundColor Green

# 2. Check Certificate configuration
if (-not $CertThumbprint -and -not $PfxPath) {
    Write-Host "[i] No certificate thumbprint or PFX path provided." -ForegroundColor Yellow
    Write-Host "    To sign releases in CI/CD, specify -CertThumbprint <SHA1> or -PfxPath <path.pfx>" -ForegroundColor Yellow
    Write-Host "    Binaries remain unsigned (Open Source Development Mode)." -ForegroundColor Yellow
    exit 0
}

# 3. Perform signing
foreach ($target in $Path) {
    if (-not (Test-Path $target)) {
        Write-Host "[-] Skipping missing target: $target" -ForegroundColor Yellow
        continue
    }

    Write-Host "[*] Signing: $target" -ForegroundColor Cyan
    $signArgs = @("sign", "/fd", "sha256", "/tr", $TimestampServer, "/td", "sha256")

    if ($CertThumbprint) {
        $signArgs += @("/sha1", $CertThumbprint)
    } elseif ($PfxPath) {
        $signArgs += @("/f", $PfxPath)
        if ($PfxPassword) {
            $signArgs += @("/p", $PfxPassword)
        }
    }
    $signArgs += (Resolve-Path $target).Path

    $proc = Start-Process -FilePath $signtool -ArgumentList $signArgs -NoNewWindow -PassThru -Wait
    if ($proc.ExitCode -eq 0) {
        Write-Host "[✓] Successfully signed: $target" -ForegroundColor Green
    } else {
        Write-Host "[!] Failed to sign $target (ExitCode: $($proc.ExitCode))" -ForegroundColor Red
    }
}
