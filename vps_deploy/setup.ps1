<#
.SYNOPSIS
    One-time VPS setup: install dependencies, verify MT5, initialize database.

.DESCRIPTION
    Run this once on a fresh VPS after cloning the repo.
    Safe to re-run — skips steps that are already done.

.EXAMPLE
    .\vps_deploy\setup.ps1
    .\vps_deploy\setup.ps1 -RepoRoot "C:\Trading\trading_forward_test"
#>
param(
    [string]$RepoRoot = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = "Continue"
$envFile = Join-Path $RepoRoot ".env.live"

Write-Host ""
Write-Host "=== VPS One-Time Setup ===" -ForegroundColor Cyan
Write-Host "Repo: $RepoRoot"
Write-Host ""

# ─── 1. Python dependencies ──────────────────────────────────────────
Write-Host "1. Installing Python dependencies..." -ForegroundColor White
$deps = @("flask", "python-dotenv", "MetaTrader5", "pandas", "requests")
& python -m pip install $deps --quiet 2>&1 | Out-Null
if ($LASTEXITCODE -eq 0) {
    Write-Host "  [OK] Dependencies installed" -ForegroundColor Green
} else {
    Write-Host "  [WARN] pip install had issues. Running verbose:" -ForegroundColor Yellow
    & python -m pip install $deps
}

# ─── 2. Verify .env.live ─────────────────────────────────────────────
Write-Host ""
Write-Host "2. Checking .env.live..." -ForegroundColor White
if (Test-Path $envFile) {
    Write-Host "  [OK] .env.live exists" -ForegroundColor Green

    $content = Get-Content $envFile -Raw
    $criticalKeys = @("WEBHOOK_SHARED_TOKEN", "MT5_LOGIN", "MT5_PASSWORD", "MT5_SERVER")
    $missing = @()
    foreach ($key in $criticalKeys) {
        $match = [regex]::Match($content, "(?m)^$key=(.+)$")
        if (-not $match.Success -or $match.Groups[1].Value.Trim().Length -eq 0) {
            $missing += $key
        }
    }
    if ($missing.Count -gt 0) {
        Write-Host "  [WARN] Missing values in .env.live: $($missing -join ', ')" -ForegroundColor Yellow
        Write-Host "  Edit .env.live and fill in the missing values before starting the server." -ForegroundColor Yellow
    } else {
        Write-Host "  [OK] All critical keys are set" -ForegroundColor Green
    }
} else {
    Write-Host "  [FAIL] .env.live not found at $envFile" -ForegroundColor Red
    Write-Host "  Copy .env.example to .env.live and fill in your live trading credentials." -ForegroundColor Yellow
}

# ─── 3. Create directories ───────────────────────────────────────────
Write-Host ""
Write-Host "3. Creating directories..." -ForegroundColor White
$dirs = @(
    (Join-Path $RepoRoot "logs"),
    (Join-Path $RepoRoot "logs" "rejected")
)
foreach ($d in $dirs) {
    if (-not (Test-Path $d)) {
        New-Item -ItemType Directory -Path $d -Force | Out-Null
        Write-Host "  Created: $d" -ForegroundColor Green
    } else {
        Write-Host "  Exists:  $d" -ForegroundColor Gray
    }
}

# ─── 4. Initialize database ──────────────────────────────────────────
Write-Host ""
Write-Host "4. Initializing database..." -ForegroundColor White

$dbScript = @"
import os, sys
os.chdir(r'$RepoRoot')
sys.path.insert(0, r'$RepoRoot')
from dotenv import load_dotenv
load_dotenv(r'$envFile', override=True)
from config import AppConfig
from database.db import init_db
cfg = AppConfig.from_env()
init_db(cfg)
print(f'DB_OK|{cfg.db_path}')
"@

$dbResult = & python -X utf8 -c $dbScript 2>&1
if ($dbResult -match "DB_OK\|(.+)") {
    Write-Host "  [OK] Database initialized: $($Matches[1])" -ForegroundColor Green
} else {
    Write-Host "  [WARN] Database init output: $dbResult" -ForegroundColor Yellow
}

# ─── 5. Verify MT5 terminal path ─────────────────────────────────────
Write-Host ""
Write-Host "5. Checking MT5 terminal..." -ForegroundColor White

if (Test-Path $envFile) {
    $content = Get-Content $envFile -Raw
    $pathMatch = [regex]::Match($content, "(?m)^MT5_TERMINAL_PATH=(.+)$")
    if ($pathMatch.Success) {
        $mt5Path = $pathMatch.Groups[1].Value.Trim()
        if (Test-Path $mt5Path) {
            Write-Host "  [OK] MT5 terminal found: $mt5Path" -ForegroundColor Green
        } else {
            Write-Host "  [FAIL] MT5 terminal NOT found: $mt5Path" -ForegroundColor Red
            Write-Host "  Update MT5_TERMINAL_PATH in .env.live to the correct terminal64.exe path." -ForegroundColor Yellow
        }
    } else {
        Write-Host "  [WARN] MT5_TERMINAL_PATH not set in .env.live (will use default)" -ForegroundColor Yellow
    }
}

# ─── 6. Verify MT5 connection ────────────────────────────────────────
Write-Host ""
Write-Host "6. Testing MT5 connection..." -ForegroundColor White

$mt5Script = @"
import os, sys
os.chdir(r'$RepoRoot')
from dotenv import load_dotenv
load_dotenv(r'$envFile', override=True)
import MetaTrader5 as mt5
path = os.getenv('MT5_TERMINAL_PATH', '')
ok = mt5.initialize(path) if path else mt5.initialize()
if not ok:
    print(f'FAIL|{mt5.last_error()}')
else:
    info = mt5.account_info()
    if info:
        print(f'OK|{info.login}|{info.server}|{info.balance}')
    else:
        print('FAIL|Could not get account info')
    mt5.shutdown()
"@

$mt5Result = & python -X utf8 -c $mt5Script 2>&1
$mt5Line = ($mt5Result -split "`n" | Where-Object { $_.Trim() } | Select-Object -Last 1).Trim()

if ($mt5Line -match "^OK\|(.+)\|(.+)\|(.+)$") {
    Write-Host "  [OK] Connected: account=$($Matches[1]), server=$($Matches[2]), balance=$($Matches[3])" -ForegroundColor Green
} else {
    Write-Host "  [WARN] MT5 connection issue: $mt5Line" -ForegroundColor Yellow
    Write-Host "  Make sure MetaTrader 5 is running and logged into the correct account." -ForegroundColor Yellow
}

# ─── 7. Check ngrok ──────────────────────────────────────────────────
Write-Host ""
Write-Host "7. Checking ngrok..." -ForegroundColor White
$ngrokPath = Get-Command ngrok -ErrorAction SilentlyContinue
if ($ngrokPath) {
    Write-Host "  [OK] ngrok found: $($ngrokPath.Source)" -ForegroundColor Green
    $ngrokVer = & ngrok version 2>&1
    Write-Host "  Version: $ngrokVer" -ForegroundColor Gray
} else {
    Write-Host "  [WARN] ngrok not found in PATH" -ForegroundColor Yellow
    Write-Host "  Install ngrok: https://ngrok.com/download" -ForegroundColor Yellow
    Write-Host "  Then run: ngrok config add-authtoken YOUR_TOKEN" -ForegroundColor Yellow
}

# ─── Summary ──────────────────────────────────────────────────────────
Write-Host ""
Write-Host "=== Setup Complete ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "Next steps:" -ForegroundColor White
Write-Host "  1. Ensure MT5 is running with AutoTrading enabled (Ctrl+E)" -ForegroundColor Gray
Write-Host "  2. Ensure all 9 symbols are in Market Watch with bid > 0" -ForegroundColor Gray
Write-Host "  3. Run: .\vps_deploy\start_all.ps1" -ForegroundColor Gray
Write-Host "  4. Copy the ngrok URL to TradingView alert webhooks" -ForegroundColor Gray
Write-Host "  5. Run: .\vps_deploy\diagnose.ps1  (to verify everything)" -ForegroundColor Gray
Write-Host ""
