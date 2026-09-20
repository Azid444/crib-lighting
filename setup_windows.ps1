# One-time Windows setup. Right-click -> "Run with PowerShell", or:
#   powershell -ExecutionPolicy Bypass -File setup_windows.ps1
#
# Run as Administrator if you want the firewall rule added automatically;
# without admin everything else still works and the rule is skipped.

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "`n=== crib-lighting setup ===`n" -ForegroundColor Cyan

# --- python ---------------------------------------------------------------
try { $ver = (python --version) 2>&1 } catch {
    Write-Host "Python not found. Install it from https://python.org/downloads" -ForegroundColor Red
    Write-Host 'Tick "Add python.exe to PATH" during install.' -ForegroundColor Red
    exit 1
}
Write-Host "Found $ver"

# --- venv + deps ----------------------------------------------------------
if (-not (Test-Path ".venv")) {
    Write-Host "Creating virtual environment..."
    python -m venv .venv
}
Write-Host "Installing dependencies (this takes a minute)..."
& .\.venv\Scripts\python.exe -m pip install --quiet --upgrade pip
& .\.venv\Scripts\python.exe -m pip install --quiet -r requirements.txt

# --- config ---------------------------------------------------------------
if (-not (Test-Path "config.yaml")) {
    Copy-Item "config.example.yaml" "config.yaml"
    Write-Host "Created config.yaml - you still need to fill in your devices." -ForegroundColor Yellow
}

# --- firewall -------------------------------------------------------------
# Without this your iPhone cannot reach the server; this is the single most
# common reason the page will not load.
$admin = ([Security.Principal.WindowsPrincipal] `
    [Security.Principal.WindowsIdentity]::GetCurrent()
    ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if ($admin) {
    if (-not (Get-NetFirewallRule -DisplayName "crib-lighting" -ErrorAction SilentlyContinue)) {
        New-NetFirewallRule -DisplayName "crib-lighting" -Direction Inbound `
            -LocalPort 8080 -Protocol TCP -Action Allow -Profile Private | Out-Null
        Write-Host "Firewall opened on port 8080 (private networks)." -ForegroundColor Green
    } else {
        Write-Host "Firewall rule already present."
    }
} else {
    Write-Host "Not running as Administrator - skipped the firewall rule." -ForegroundColor Yellow
    Write-Host "If your phone cannot connect, re-run this as Admin." -ForegroundColor Yellow
}

# --- audio devices --------------------------------------------------------
Write-Host "`nAudio inputs available:" -ForegroundColor Cyan
& .\.venv\Scripts\python.exe -m crib.audio

# --- address --------------------------------------------------------------
$ip = (Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" } |
    Select-Object -First 1).IPAddress

Write-Host "`n=== Done ===" -ForegroundColor Green
Write-Host "1. Edit config.yaml with your device details"
Write-Host "2. Double-click start.bat"
Write-Host "3. On your iPhone open:  http://${ip}:8080"
Write-Host "   then Share -> Add to Home Screen`n"
