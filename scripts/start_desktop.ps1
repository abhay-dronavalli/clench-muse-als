# Start the Clench desktop agent: eyes point and the jaw clicks in all of Windows (docs/desktop-control.md).
#
#   .\scripts\start_desktop.ps1                          # Eyedid gaze, the Core on 8000, person "default"
#   .\scripts\start_desktop.ps1 -Person taher            # load and save taher's eye calibration
#   .\scripts\start_desktop.ps1 -Mouse                   # the mouse stands in for the eyes
#   .\scripts\start_desktop.ps1 -Url ws://127.0.0.1:8001/ws/desktop -- --sg-window-ms 600
#
# It checks the setup first and says what is missing; it never edits .env. Anything after `--` goes
# to the agent unchanged (see `uv run --extra desktop python -m desktop.agent --help`). Ctrl+C quits.

param(
    [string]$Url = 'ws://127.0.0.1:8000/ws/desktop',
    [string]$Person = 'default',
    [switch]$Mouse,
    [Parameter(ValueFromRemainingArguments = $true)] [string[]]$Rest
)

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$ok = $true

if (-not $Mouse) {
    $dll = Join-Path $root 'desktop\eyedid\third_party\eyedid\bin\eyedid\eyedid_core.dll'
    if (-not (Test-Path $dll)) {
        Write-Host "Eyedid SDK missing: unzip the Windows SDK so $dll exists (or use -Mouse)." -ForegroundColor Red
        $ok = $false
    }
    $envFile = Join-Path $root '.env'
    $hasKey = (Test-Path $envFile) -and (Select-String -Path $envFile -Pattern '^\s*EYEDID_DESKTOP_KEY\s*=\s*\S' -Quiet)
    if (-not $hasKey -and -not $env:EYEDID_DESKTOP_KEY) {
        Write-Host 'EYEDID_DESKTOP_KEY is not set in .env (or use -Mouse).' -ForegroundColor Red
        $ok = $false
    }
}
if (-not $ok) { exit 1 }

# The Core: the agent waits and reconnects on its own, so a missing Core is only a warning.
$health = ($Url -replace '^ws', 'http') -replace '/ws/desktop$', '/health'
try {
    $h = Invoke-RestMethod -Uri $health -TimeoutSec 3
    Write-Host ("Core: {0}, language {1}, input on the {2}" -f $health, $h.lang, $h.input_target)
    if ($h.dry_run -eq $false) {
        Write-Host 'Careful: ACTIONS_DRY_RUN is off on this Core. A help alert really calls and texts.' -ForegroundColor Yellow
    }
} catch {
    Write-Host "No Core at $health yet: start it (uv run uvicorn core.main:app --port 8000); the agent connects when it is up." -ForegroundColor Yellow
}

$agentArgs = @('run', '--extra', 'desktop', 'python', '-m', 'desktop.agent', '--url', $Url, '--person', $Person)
if ($Mouse) { $agentArgs += @('--gaze', 'mouse') }
if ($Rest) { $agentArgs += ($Rest | Where-Object { $_ -ne '--' }) }
Write-Host 'Keys: F8 clench (hold: help), F9 double blink, F7 calibrate, F10 pause, Ctrl+F8 board/desktop. Ctrl+C quits.'
& uv @agentArgs
