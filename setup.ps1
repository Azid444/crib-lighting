<#
  Sets everything up and starts it. Run once:

      powershell -ExecutionPolicy Bypass -File setup.ps1

  Or just right-click this file -> "Run with PowerShell".
  Run as Administrator if you can; it then opens the firewall for you.
#>

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Step($text) { Write-Host "`n>> $text" -ForegroundColor Cyan }
function Good($text) { Write-Host "   $text" -ForegroundColor Green }
function Warn($text) { Write-Host "   $text" -ForegroundColor Yellow }

Write-Host "`n=============================" -ForegroundColor Magenta
Write-Host "  crib-lighting setup" -ForegroundColor Magenta
Write-Host "=============================" -ForegroundColor Magenta

$admin = ([Security.Principal.WindowsPrincipal] `
    [Security.Principal.WindowsIdentity]::GetCurrent()
    ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

# The firewall rule and the login task both need admin. Ask once, here,
# rather than getting halfway and skipping them.
if (-not $admin -and $PSCommandPath) {
    Write-Host "`n   Windows will ask for permission -- say yes." -ForegroundColor Yellow
    Write-Host "   (It is needed to let your phone through the firewall.)" -ForegroundColor Yellow
    try {
        Start-Process powershell -Verb RunAs -Wait -ArgumentList @(
            "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`""
        )
        exit 0
    } catch {
        Write-Host "   Carrying on without it." -ForegroundColor Yellow
    }
}

# --- 1. Python ------------------------------------------------------------
Step "Checking Python"
$python = $null
foreach ($candidate in @("python", "python3", "py")) {
    try {
        $v = & $candidate --version 2>&1
        if ($v -match "Python 3\.(\d+)" -and [int]$Matches[1] -ge 10) {
            $python = $candidate; Good "$v"; break
        }
    } catch { }
}

if (-not $python) {
    Warn "Python 3.10+ not found. Installing it..."
    try {
        winget install --id Python.Python.3.12 --source winget `
            --accept-package-agreements --accept-source-agreements -h
    } catch {
        Write-Host @"

   Automatic install failed. Grab Python from https://python.org/downloads
   and tick "Add python.exe to PATH" during setup, then run this again.
"@ -ForegroundColor Red
        Read-Host "`nPress Enter to close"; exit 1
    }
    # winget does not refresh this shell's PATH.
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [Environment]::GetEnvironmentVariable("Path", "User")
    $python = "python"
    try { Good (& $python --version 2>&1) }
    catch {
        Warn "Python is installed but not on PATH yet."
        Warn "Close this window, open a new one, and run setup.ps1 again."
        Read-Host "`nPress Enter to close"; exit 1
    }
}

# --- 2. Dependencies ------------------------------------------------------
Step "Installing dependencies (a minute or so)"
if (-not (Test-Path ".venv")) { & $python -m venv .venv }
$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
& $py -m pip install --quiet --upgrade pip
& $py -m pip install --quiet -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    Write-Host "   Dependency install failed. See the errors above." -ForegroundColor Red
    Read-Host "`nPress Enter to close"; exit 1
}
Good "Installed."

# --- 3. Firewall ----------------------------------------------------------
Step "Checking the firewall"
if ($admin) {
    if (-not (Get-NetFirewallRule -DisplayName "crib-lighting" -ErrorAction SilentlyContinue)) {
        New-NetFirewallRule -DisplayName "crib-lighting" -Direction Inbound `
            -LocalPort 8080 -Protocol TCP -Action Allow -Profile Private | Out-Null
        Good "Port 8080 opened for private networks."
    } else { Good "Already allowed." }
} else {
    Warn "Not running as Administrator, so the firewall rule was skipped."
    Warn "If your phone cannot connect later, re-run this as Admin."
}

# --- 4. Start at login ----------------------------------------------------
Step "Starting automatically at login"
try {
    $action = New-ScheduledTaskAction -Execute (Join-Path $PSScriptRoot ".venv\Scripts\pythonw.exe") `
        -Argument "-m crib.app" -WorkingDirectory $PSScriptRoot
    $trigger = New-ScheduledTaskTrigger -AtLogOn
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries -ExecutionTimeLimit 0
    Register-ScheduledTask -TaskName "crib-lighting" -Action $action `
        -Trigger $trigger -Settings $settings -Force | Out-Null
    Good "Done. Remove later with: Unregister-ScheduledTask -TaskName crib-lighting"
} catch {
    Warn "Could not register the login task: $_"
    Warn "Not a problem -- use start.bat when you want it."
}

# --- 5. Launch ------------------------------------------------------------
Step "Starting crib-lighting"
$log = Join-Path $PSScriptRoot "crib-lighting.log"
$errlog = Join-Path $PSScriptRoot "crib-lighting.err.log"
# pythonw has no console window, and combining -WindowStyle with output
# redirection is unreliable in Windows PowerShell 5.1.
$pyw = Join-Path $PSScriptRoot ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $pyw)) { $pyw = $py }
Start-Process -FilePath $pyw -ArgumentList "-m crib.app" `
    -WorkingDirectory $PSScriptRoot `
    -RedirectStandardOutput $log -RedirectStandardError $errlog | Out-Null

$ip = (Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" } |
    Select-Object -First 1).IPAddress

# Wait for it to answer before opening a browser at it.
$ready = $false
foreach ($i in 1..30) {
    Start-Sleep -Milliseconds 500
    try {
        Invoke-WebRequest "http://127.0.0.1:8080/api/setup/state" `
            -UseBasicParsing -TimeoutSec 2 | Out-Null
        $ready = $true; break
    } catch { }
}

if (-not $ready) {
    Write-Host "`n   The server did not start. Here is why:`n" -ForegroundColor Red
    foreach ($file in @($errlog, $log)) {
        if ((Test-Path $file) -and (Get-Item $file).Length -gt 0) {
            Get-Content $file -Tail 20 | ForEach-Object {
                Write-Host "   $_" -ForegroundColor Red
            }
        }
    }
    Write-Host "`n   Full log: $log" -ForegroundColor Yellow
    Read-Host "`nPress Enter to close"; exit 1
}
Good "Running."

Start-Process "http://127.0.0.1:8080/setup"

Write-Host @"

=============================
  Ready
=============================

  A browser has opened. Press "Scan for my lights" and it will find
  them for you -- about 15 seconds. Make sure your lights are on.

  Then on your iPhone, open:

      http://${ip}:8080

  and tap Share -> Add to Home Screen.

  It starts by itself from now on, so you never run this again.

"@ -ForegroundColor Green

Read-Host "Press Enter to close this window (the lights keep running)"
