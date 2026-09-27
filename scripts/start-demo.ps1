# Start the demo on this laptop (laptop 1): the Core on 8000 and the web app on 5173, each in its own
# window, then print (and copy) the address laptop 2 opens.
#
#   powershell -ExecutionPolicy Bypass -File scripts\start-demo.ps1     (or double-click start-demo.bat)
#
# It stops whatever is already listening on 8000 / 5173 first. If Windows asks "Allow Node.js to
# communicate", tick BOTH Private and Public, then click Allow.

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot

# 4. Stop any old servers.
$old = Get-NetTCPConnection -LocalPort 8000, 5173 -State Listen -ErrorAction SilentlyContinue
foreach ($c in $old) {
    try {
        Stop-Process -Id $c.OwningProcess -Force -ErrorAction Stop
        Write-Host "[ok] Stopped the old server on port $($c.LocalPort) (process $($c.OwningProcess))."
    } catch {}
}

# 5. Window 1: the Core.
Start-Process powershell -WorkingDirectory $repo -ArgumentList '-NoExit', '-Command',
    "`$Host.UI.RawUI.WindowTitle = 'Clench Core :8000'; uv run uvicorn core.main:app --port 8000"
Write-Host '[..] Starting the Core on 8000 (window "Clench Core :8000")...'

# 6. Window 2: the web app, reachable from other machines (--host).
Start-Process powershell -WorkingDirectory $repo -ArgumentList '-NoExit', '-Command',
    "`$Host.UI.RawUI.WindowTitle = 'Clench Web :5173'; npm --prefix web run dev -- --host"
Write-Host '[..] Starting the web app on 5173 (window "Clench Web :5173")...'

function Wait-Url($url, $name) {
    for ($i = 0; $i -lt 90; $i++) {
        try {
            Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 2 | Out-Null
            Write-Host "[ok] $name is up."
            return $true
        } catch { Start-Sleep -Seconds 1 }
    }
    Write-Host "[x] $name did not come up in 90 s. Check its window." -ForegroundColor Red
    return $false
}
$coreUp = Wait-Url 'http://127.0.0.1:8000/health' 'The Core'
$webUp = Wait-Url 'http://127.0.0.1:5173/' 'The web app'

# 7. This laptop's address on the Wi-Fi (an iPhone hotspot gives 172.20.10.x).
$ips = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' -and $_.PrefixOrigin -ne 'WellKnown' }
$wifi = $ips | Where-Object { $_.InterfaceAlias -like 'Wi-Fi*' -or $_.InterfaceAlias -like 'Wireless*' } | Select-Object -First 1
$ip = if ($wifi) { $wifi.IPAddress } else { ($ips | Select-Object -First 1).IPAddress }

Write-Host ''
if ($ip) {
    $url = "http://${ip}:5173/"
    Write-Host "Send this address to laptop 2:  $url" -ForegroundColor Green
    Write-Host "  (console: ${url}console)"
    try { Set-Clipboard -Value $url; Write-Host '  Copied to the clipboard.' } catch {}
    if (-not $wifi) { Write-Host '  (No Wi-Fi adapter found; this is the first other network address. Check with ipconfig.)' -ForegroundColor Yellow }
} else {
    Write-Host '[x] No network address found. Are you on the Wi-Fi / hotspot? Run ipconfig.' -ForegroundColor Red
}
Write-Host 'This laptop: http://localhost:5173/   Leave both server windows open; close them to stop.'
if (-not ($coreUp -and $webUp)) { exit 1 }
