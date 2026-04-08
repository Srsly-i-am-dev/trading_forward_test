<#
.SYNOPSIS
    Diagnose the live trading webhook pipeline end-to-end.
    Checks: Python -> .env.live -> MT5 -> Flask server -> Firewall -> External access.

.DESCRIPTION
    Run this on the VPS to pinpoint why TradingView webhooks are failing (404, timeout, etc.).
    Each check prints PASS/FAIL with actionable guidance.

.EXAMPLE
    .\vps_deploy\diagnose.ps1
    .\vps_deploy\diagnose.ps1 -RepoRoot "C:\Trading\trading_forward_test"
#>
param(
    [string]$RepoRoot = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = "Continue"
$envFile = Join-Path $RepoRoot ".env.live"
$passed = 0
$failed = 0

function Write-Check {
    param([bool]$Ok, [string]$Label, [string]$Detail = "")
    if ($Ok) {
        Write-Host "  [PASS] " -ForegroundColor Green -NoNewline
        Write-Host $Label
        $script:passed++
    } else {
        Write-Host "  [FAIL] " -ForegroundColor Red -NoNewline
        Write-Host $Label
        if ($Detail) { Write-Host "         -> $Detail" -ForegroundColor Yellow }
        $script:failed++
    }
}

Write-Host ""
Write-Host "=== Live Trading Webhook Diagnostics ===" -ForegroundColor Cyan
Write-Host "Repo root: $RepoRoot"
Write-Host ""

# ─── 1. Python version ───────────────────────────────────────────────
Write-Host "1. Python Environment" -ForegroundColor White
try {
    $pyVer = & python --version 2>&1
    $verMatch = [regex]::Match($pyVer, "(\d+)\.(\d+)")
    $major = [int]$verMatch.Groups[1].Value
    $minor = [int]$verMatch.Groups[2].Value
    Write-Check ($major -ge 3 -and $minor -ge 11) "Python version: $pyVer" "Python 3.11+ required"
} catch {
    Write-Check $false "Python not found in PATH" "Install Python 3.11+ and add to PATH"
}

# ─── 2. Required packages ────────────────────────────────────────────
Write-Host ""
Write-Host "2. Python Packages" -ForegroundColor White
$packages = @("flask", "dotenv", "MetaTrader5", "pandas", "requests")
foreach ($pkg in $packages) {
    $importName = if ($pkg -eq "dotenv") { "dotenv" } else { $pkg }
    $result = & python -c "import $importName" 2>&1
    $ok = $LASTEXITCODE -eq 0
    Write-Check $ok "import $importName" "Run: python -m pip install $(if ($pkg -eq 'dotenv') {'python-dotenv'} else {$pkg})"
}

# ─── 3. .env.live configuration ──────────────────────────────────────
Write-Host ""
Write-Host "3. Configuration (.env.live)" -ForegroundColor White
$envExists = Test-Path $envFile
Write-Check $envExists ".env.live exists at $envFile" "Create .env.live from .env.example"

$token = ""
if ($envExists) {
    $envContent = Get-Content $envFile -Raw
    $criticalKeys = @("WEBHOOK_SHARED_TOKEN", "MT5_LOGIN", "MT5_PASSWORD", "MT5_SERVER", "SERVER_PORT")
    foreach ($key in $criticalKeys) {
        $match = [regex]::Match($envContent, "(?m)^$key=(.+)$")
        $hasValue = $match.Success -and $match.Groups[1].Value.Trim().Length -gt 0
        Write-Check $hasValue "$key is set" "$key is missing or empty in .env.live"
    }

    # Check SERVER_PORT is 5001
    $portMatch = [regex]::Match($envContent, "(?m)^SERVER_PORT=(\d+)")
    if ($portMatch.Success) {
        $port = $portMatch.Groups[1].Value
        Write-Check ($port -eq "5001") "SERVER_PORT = $port" "Expected 5001 for live server, got $port"
    }

    # Extract token for later use
    $tokenMatch = [regex]::Match($envContent, "(?m)^WEBHOOK_SHARED_TOKEN=(.+)$")
    if ($tokenMatch.Success) { $token = $tokenMatch.Groups[1].Value.Trim() }
}

# ─── 4. MT5 connectivity ─────────────────────────────────────────────
Write-Host ""
Write-Host "4. MetaTrader 5" -ForegroundColor White

$mt5ScriptFile = Join-Path $RepoRoot "vps_deploy\_mt5_check.py"
@"
import sys, os
os.chdir(r'$RepoRoot')
from dotenv import load_dotenv
load_dotenv(r'$envFile', override=True)
import MetaTrader5 as mt5
path = os.getenv('MT5_TERMINAL_PATH', '')
if path:
    ok = mt5.initialize(path)
else:
    ok = mt5.initialize()
if not ok:
    print(f'INIT_FAIL|{mt5.last_error()}')
    sys.exit(1)
info = mt5.account_info()
if not info:
    print('ACCOUNT_FAIL|Could not get account info')
    mt5.shutdown()
    sys.exit(1)
print(f'ACCOUNT_OK|{info.login}|{info.server}|{info.balance}')
symbols = ['EURUSD.sc','EURJPY.sc','GBPAUD.sc','USDCHF.sc','GBPUSD.sc','AUDJPY.sc','EURAUD.sc','NZDUSD.sc','USDJPY.sc']
bad = []
for s in symbols:
    mt5.symbol_select(s, True)
import time
time.sleep(0.5)
for s in symbols:
    si = mt5.symbol_info(s)
    tick = mt5.symbol_info_tick(s)
    bid = tick.bid if tick else 0
    vis = si.visible if si else False
    if not vis or bid <= 0:
        bad.append(s)
if bad:
    sep = ','
    print(f'SYMBOLS_BAD|{sep.join(bad)}')
else:
    print(f'SYMBOLS_OK|{len(symbols)}')
mt5.shutdown()
"@ | Set-Content -Path $mt5ScriptFile -Encoding UTF8

$mt5Result = & python -X utf8 $mt5ScriptFile 2>&1
Remove-Item $mt5ScriptFile -Force -ErrorAction SilentlyContinue
$mt5Lines = ($mt5Result -split "`n") | Where-Object { $_.Trim() }

$mt5InitOk = $false
$mt5AccountOk = $false
$mt5SymbolsOk = $false

foreach ($line in $mt5Lines) {
    $parts = $line.Trim() -split '\|'
    switch ($parts[0]) {
        "INIT_FAIL" {
            Write-Check $false "MT5 initialize" "Error: $($parts[1]). Is MetaTrader 5 running?"
        }
        "ACCOUNT_FAIL" {
            $mt5InitOk = $true
            Write-Check $true "MT5 initialize"
            Write-Check $false "MT5 account info" $parts[1]
        }
        "ACCOUNT_OK" {
            $mt5InitOk = $true
            $mt5AccountOk = $true
            Write-Check $true "MT5 initialize"
            Write-Check $true "MT5 account: $($parts[1]) @ $($parts[2]), balance=$($parts[3])"
        }
        "SYMBOLS_BAD" {
            Write-Check $false "Symbol visibility" "Missing/no bid: $($parts[1]). Add to Market Watch in MT5."
        }
        "SYMBOLS_OK" {
            $mt5SymbolsOk = $true
            Write-Check $true "All $($parts[1]) symbols visible with bid > 0"
        }
    }
}

if (-not $mt5InitOk -and -not ($mt5Lines -match "INIT_FAIL")) {
    Write-Check $false "MT5 check" "Unexpected output: $mt5Result"
}

# ─── 5. Flask server status ──────────────────────────────────────────
Write-Host ""
Write-Host "5. Webhook Server (Flask)" -ForegroundColor White

$portInUse = $false
try {
    $tcp = New-Object System.Net.Sockets.TcpClient
    $tcp.Connect("127.0.0.1", 5001)
    $portInUse = $true
    $tcp.Close()
} catch {
    $portInUse = $false
}

Write-Check $portInUse "Port 5001 is listening" "Webhook server is NOT running. Start with: python -X utf8 -m server.webhook_server_live"

if ($portInUse) {
    try {
        $health = Invoke-RestMethod -Uri "http://localhost:5001/health" -Method Get -TimeoutSec 5
        $isLive = $health.mode -eq "LIVE" -and $health.status -eq "ok"
        Write-Check $isLive "Health endpoint: mode=$($health.mode), status=$($health.status), account=$($health.account)" "Server responded but mode is not LIVE"
    } catch {
        Write-Check $false "Health endpoint unreachable" "Server is on port 5001 but /health failed: $_"
    }

    # Test the actual webhook route with a GET (should return 405 Method Not Allowed, NOT 404)
    try {
        $webhookTest = Invoke-WebRequest -Uri "http://localhost:5001/webhook" -Method Get -TimeoutSec 5 -ErrorAction SilentlyContinue
    } catch {
        $statusCode = $_.Exception.Response.StatusCode.value__
        if ($statusCode -eq 405) {
            Write-Check $true "Webhook route /webhook exists (405 on GET = correct, expects POST)"
        } elseif ($statusCode -eq 404) {
            Write-Check $false "Webhook route /webhook returns 404!" "The /webhook route is not registered. Server may have crashed during startup."
        } else {
            Write-Check $true "Webhook route responded with HTTP $statusCode"
        }
    }
}

# ─── 6. Position monitor ─────────────────────────────────────────────
Write-Host ""
Write-Host "6. Position Monitor" -ForegroundColor White

$monPid = $null
$monPidFile = Join-Path $RepoRoot ".position-monitor.pid"
if (Test-Path $monPidFile) {
    $monPid = (Get-Content $monPidFile -ErrorAction SilentlyContinue | Select-Object -First 1).Trim()
}

$monRunning = $false
if ($monPid) {
    $monRunning = [bool](Get-Process -Id $monPid -ErrorAction SilentlyContinue)
}
if (-not $monRunning) {
    # Fallback: check for any python process with position_monitor in command line
    $monProcs = Get-WmiObject Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match "position_monitor" }
    $monRunning = $null -ne $monProcs -and @($monProcs).Count -gt 0
}

Write-Check $monRunning "Position monitor is running" "Start with: python -X utf8 executor/position_monitor.py"

# ─── 7. Firewall & external access ───────────────────────────────────
Write-Host ""
Write-Host "7. Firewall & External Access" -ForegroundColor White

# Check Windows Firewall rule for port 5001
$fwRule = Get-NetFirewallRule -ErrorAction SilentlyContinue |
    Where-Object { $_.Enabled -eq 'True' -and $_.Direction -eq 'Inbound' -and $_.Action -eq 'Allow' } |
    ForEach-Object {
        $portFilter = $_ | Get-NetFirewallPortFilter -ErrorAction SilentlyContinue
        if ($portFilter.LocalPort -match '5001' -or $portFilter.LocalPort -eq 'Any') { $_ }
    } | Select-Object -First 1

if ($fwRule) {
    Write-Check $true "Firewall allows inbound on port 5001 (rule: $($fwRule.DisplayName))"
} else {
    Write-Check $false "No firewall rule allowing inbound port 5001" "Run (admin): New-NetFirewallRule -DisplayName 'Webhook Server' -Direction Inbound -Port 5001 -Protocol TCP -Action Allow"
}

# Get public IP
try {
    $publicIp = (Invoke-RestMethod -Uri "https://api.ipify.org" -TimeoutSec 5).Trim()
    Write-Host "  Public IP: $publicIp" -ForegroundColor Cyan

    Write-Host ""
    Write-Host "  TradingView Webhook URL:" -ForegroundColor Cyan
    Write-Host "  http://$publicIp`:5001/webhook?token=$token" -ForegroundColor Green
    Write-Host ""

    # Test external reachability
    if ($portInUse) {
        try {
            $extHealth = Invoke-RestMethod -Uri "http://$publicIp`:5001/health" -Method Get -TimeoutSec 10
            Write-Check ($extHealth.status -eq "ok") "External access: http://$publicIp`:5001/health returns OK"
        } catch {
            Write-Check $false "External access to http://$publicIp`:5001/health" "Port 5001 not reachable from outside. Check: firewall, VPS provider security group, or ISP blocking."
        }
    }
} catch {
    Write-Host "  [WARN] Could not determine public IP (no internet?)" -ForegroundColor Yellow
}

# ─── Summary ──────────────────────────────────────────────────────────
Write-Host ""
Write-Host "=== Summary ===" -ForegroundColor Cyan
Write-Host "  Passed: $passed" -ForegroundColor Green
Write-Host "  Failed: $failed" -ForegroundColor $(if ($failed -gt 0) { "Red" } else { "Green" })

if ($failed -gt 0) {
    Write-Host ""
    Write-Host "Fix the FAIL items above, then re-run this diagnostic." -ForegroundColor Yellow
} else {
    Write-Host ""
    Write-Host "All checks passed! Webhook pipeline should be operational." -ForegroundColor Green
}
Write-Host ""
