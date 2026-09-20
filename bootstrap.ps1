<#
  One-command install. Paste into PowerShell:

    irm https://raw.githubusercontent.com/Azid444/crib-lighting/main/bootstrap.ps1 | iex

  Downloads the project to your Documents folder and runs setup.
#>
$ErrorActionPreference = "Stop"

$repo   = "https://github.com/Azid444/crib-lighting"
$branch = "claude/room-lighting-unification-a1i9fy"
$target = Join-Path ([Environment]::GetFolderPath("MyDocuments")) "crib-lighting"

Write-Host "`nInstalling crib-lighting to $target`n" -ForegroundColor Cyan

if (Test-Path (Join-Path $target ".git")) {
    Write-Host ">> Updating existing copy" -ForegroundColor Cyan
    git -C $target pull --ff-only
} elseif (Get-Command git -ErrorAction SilentlyContinue) {
    git clone --branch $branch --depth 1 $repo $target
} else {
    # No git? Fall back to the zip so nothing else needs installing.
    Write-Host ">> git not found, downloading zip instead" -ForegroundColor Yellow
    $zip = Join-Path $env:TEMP "crib-lighting.zip"
    Invoke-WebRequest "$repo/archive/refs/heads/$branch.zip" -OutFile $zip
    $tmp = Join-Path $env:TEMP "crib-extract"
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
    Expand-Archive $zip -DestinationPath $tmp -Force
    $inner = Get-ChildItem $tmp | Select-Object -First 1
    New-Item -ItemType Directory -Force -Path $target | Out-Null
    Copy-Item (Join-Path $inner.FullName "*") $target -Recurse -Force
    Remove-Item $zip, $tmp -Recurse -Force -ErrorAction SilentlyContinue
}

& powershell -ExecutionPolicy Bypass -File (Join-Path $target "setup.ps1")
