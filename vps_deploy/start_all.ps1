<#
.SYNOPSIS
    Start the full live trading stack: webhook server + position monitor.

.DESCRIPTION
    Launches both components as background processes with PID tracking.
    Prints the TradingView webhook URL at the end.

.EXAMPLE
    .\vps_deploy\start_all.ps1
    .\vps_deploy\start_all.ps1 -RepoRoot "C:\Trading\trading_forward_test"
#>
param(
    [string]$RepoRoot = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = "Stop"

$pidWebhook  = Join-Path $RepoRoot ".webhook-server.pid"
$pidMonitor  = Join-Path $RepoRoot ".position-monitor.pid"
$logsDir     = Join-Path $RepoRoot "logs"

# Ensure logs directory exists
if (-not (Test-Path $logsDir)) { New-Item -ItemType Directory -Path $logsDir -Force | Out-Null }

function Test-AlreadyRunning {
    param([string]$PidFile, [string]$Name)
    if (Test-Path $PidFile) {
        $existingPid = (Get-Content $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1).Trim()
        if ($existingPid -and (Get-Process -Id $existingPid -ErrorAction SilentlyContinue)) {
            Write-Host "  $Name already running (PID=$existingPid). Stop first with stop_all.ps1" -ForegroundColor Yellow
            return $true
        }
        # Stale PID file — remove it
        Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    }
    return $false
}

function Start-BackgroundPython {
    param([string]$Name, [string]$PidFile, [string]$LogFile, [string[]]$PythonArgs)

    if (Test-AlreadyRunning $PidFile $Name) { return $null }

    $allArgs = @("-X", "utf8") + $PythonArgs
    $proc = Start-Process -FilePath "python" -ArgumentList $allArgs `
        -WorkingDirectory $RepoRoot -PassThru -WindowStyle Hidden `
        -RedirectStandardOutput $LogFile `
        -RedirectStandardError "$LogFile.err"

    Start-Sleep -Milliseconds 800

    if (Get-Process -Id $proc.Id -ErrorAction SilentlyContinue) {
        Set-Content -Path $PidFile -Value $proc.Id
        Write-Host "  $Name started (PID=$($proc.Id))" -ForegroundColor Green
        return $proc
    } else {
        Write-Host "  $Name FAILED to start. Check $LogFile.err" -ForegroundColor Red
        return $null
    }
}

Write-Host ""
Write-Host "=== Starting Live Trading Stack ===" -ForegroundColor Cyan
Write-Host "Repo: $RepoRoot"
Write-Host ""

# ─── 1. Webhook Server ───────────────────────────────────────────────
Write-Host "1. Webhook Server (port 5001)" -ForegroundColor White
$webhookLog = Join-Path $logsDir "webhook_server.log"
$webhookProc = Start-BackgroundPython "Webhook Server" $pidWebhook $webhookLog @("-m", "server.webhook_server_live")

if ($webhookProc) {
    Write-Host "  Waiting for server to bind port 5001..." -ForegroundColor Gray
    $ready = $false
    for ($i = 0; $i -lt 10; $i++) {
        Start-Sleep -Seconds 1
        try {
            $tcp = New-Object System.Net.Sockets.TcpClient
            $tcp.Connect("127.0.0.1", 5001)
            $tcp.Close()
            $ready = $true
            break
        } catch { }
    }
    if ($ready) {
        Write-Host "  Port 5001 is listening" -ForegroundColor Green
        try {
            $health = Invoke-RestMethod -Uri "http://localhost:5001/health" -Method Get -TimeoutSec 5
            Write-Host "  Health: mode=$($health.mode), account=$($health.account)" -ForegroundColor Green
        } catch {
            Write-Host "  [WARN] Port is open but /health didn't respond yet" -ForegroundColor Yellow
        }
    } else {
        Write-Host "  [WARN] Server started but port 5001 not yet listening after 10s" -ForegroundColor Yellow
        Write-Host "  Check: $webhookLog.err" -ForegroundColor Yellow
    }
}

# ─── 2. Position Monitor ─────────────────────────────────────────────
Write-Host ""
Write-Host "2. Position Monitor" -ForegroundColor White
$monitorLog = Join-Path $logsDir "position_monitor.log"
Start-BackgroundPython "Position Monitor" $pidMonitor $monitorLog @("executor/position_monitor.py") | Out-Null

# ─── Print Webhook URL ───────────────────────────────────────────────
Write-Host ""
$envFile = Join-Path $RepoRoot ".env.live"
$token = ""
if (Test-Path $envFile) {
    $envContent = Get-Content $envFile -Raw
    $tokenMatch = [regex]::Match($envContent, "(?m)^WEBHOOK_SHARED_TOKEN=(.+)$")
    if ($tokenMatch.Success) { $token = $tokenMatch.Groups[1].Value.Trim() }
}

try {
    $publicIp = (Invoke-RestMethod -Uri "https://api.ipify.org" -TimeoutSec 5).Trim()
    Write-Host "  ============================================" -ForegroundColor Cyan
    Write-Host "  TradingView Webhook URL:" -ForegroundColor Cyan
    Write-Host "  http://$publicIp`:5001/webhook?token=$token" -ForegroundColor Green
    Write-Host "  ============================================" -ForegroundColor Cyan
} catch {
    Write-Host "  Could not determine public IP. Webhook URL:" -ForegroundColor Yellow
    Write-Host "  http://<VPS_IP>:5001/webhook?token=$token" -ForegroundColor Yellow
}

# ─── Summary ──────────────────────────────────────────────────────────
Write-Host ""
Write-Host "=== Status ===" -ForegroundColor Cyan

$components = @(
    @{ Name = "Webhook Server"; PidFile = $pidWebhook },
    @{ Name = "Position Monitor"; PidFile = $pidMonitor }
)

foreach ($c in $components) {
    if (Test-Path $c.PidFile) {
        $pid = (Get-Content $c.PidFile | Select-Object -First 1).Trim()
        $running = [bool](Get-Process -Id $pid -ErrorAction SilentlyContinue)
        $color = if ($running) { "Green" } else { "Red" }
        $status = if ($running) { "RUNNING (PID=$pid)" } else { "STOPPED" }
        Write-Host "  $($c.Name): $status" -ForegroundColor $color
    } else {
        Write-Host "  $($c.Name): NOT STARTED" -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "To stop all: .\vps_deploy\stop_all.ps1" -ForegroundColor Gray
Write-Host "To diagnose: .\vps_deploy\diagnose.ps1" -ForegroundColor Gray
Write-Host ""
