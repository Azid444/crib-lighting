# Starts crib-lighting minimised whenever you log in.
# Undo with:  Unregister-ScheduledTask -TaskName "crib-lighting"
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$action = New-ScheduledTaskAction -Execute "$PSScriptRoot\.venv\Scripts\pythonw.exe" `
    -Argument "-m crib.app" -WorkingDirectory $PSScriptRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn
# Without this the task is throttled or killed on battery.
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -ExecutionTimeLimit 0

Register-ScheduledTask -TaskName "crib-lighting" -Action $action `
    -Trigger $trigger -Settings $settings -Force | Out-Null

Write-Host "crib-lighting will now start automatically at login." -ForegroundColor Green
Write-Host 'Remove with: Unregister-ScheduledTask -TaskName "crib-lighting"'
