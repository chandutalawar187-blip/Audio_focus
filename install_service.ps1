#requires -RunAsAdministrator
$ErrorActionPreference = "Stop"

$python = (Get-Command python -ErrorAction Stop).Source
$serviceScript = Join-Path $PSScriptRoot "audio_focus_service.py"
$requirements = Join-Path $PSScriptRoot "requirements.txt"

& $python -m pip install -r $requirements
if ($LASTEXITCODE -ne 0) {
    throw "Could not install Audio Focus dependencies."
}

$existingService = Get-Service -Name "AudioFocusService" -ErrorAction SilentlyContinue
if ($existingService) {
    if ($existingService.Status -ne "Stopped") {
        Stop-Service -Name "AudioFocusService"
        $existingService.WaitForStatus("Stopped", [TimeSpan]::FromSeconds(30))
    }
    & $python $serviceScript --startup auto --username LocalSystem update
} else {
    & $python $serviceScript --startup auto --username LocalSystem install
}
if ($LASTEXITCODE -ne 0) {
    throw "Could not install the Audio Focus Windows service."
}

Start-Service -Name "AudioFocusService"
Write-Host "Audio Focus service installed and started."
