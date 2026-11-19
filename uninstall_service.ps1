#requires -RunAsAdministrator
$ErrorActionPreference = "Stop"

$python = (Get-Command python -ErrorAction Stop).Source
$serviceScript = Join-Path $PSScriptRoot "audio_focus_service.py"

Stop-Service -Name "AudioFocusService" -ErrorAction SilentlyContinue
& $python $serviceScript remove
if ($LASTEXITCODE -ne 0) {
    throw "Could not remove the Audio Focus Windows service."
}

Write-Host "Audio Focus service removed."
