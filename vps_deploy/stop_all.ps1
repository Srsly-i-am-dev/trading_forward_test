<#
.SYNOPSIS
    Stop all live trading components: webhook server and position monitor.

.EXAMPLE
    .\vps_deploy\stop_all.ps1
    .\vps_deploy\stop_all.ps1 -RepoRoot "C:\Trading\trading_forward_test"
#>
param(
    [string]$RepoRoot = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = "Continue"

Write-Host ""
Write-Host "=== Stopping Live Trading Stack ===" -ForegroundColor Cyan

$components = @(
    @{ Name = "Position Monitor"; PidFile = Join-Path $RepoRoot ".position-monitor.pid" },
    @{ Name = "Webhook Server";   PidFile = Join-Path $RepoRoot ".webhook-server.pid" }
)

foreach ($c in $components) {
    Write-Host ""
    Write-Host "  $($c.Name):" -NoNewline

    if (-not (Test-Path $c.PidFile)) {
        Write-Host " no PID file (not managed by start_all.ps1)" -ForegroundColor Yellow
        continue
    }

    $pid = (Get-Content $c.PidFile -ErrorAction SilentlyContinue | Select-Object -First 1).Trim()

    if (-not $pid) {
        Write-Host " empty PID file" -ForegroundColor Yellow
        Remove-Item $c.PidFile -Force -ErrorAction SilentlyContinue
        continue
    }

    $proc = Get-Process -Id $pid -ErrorAction SilentlyContinue
    if ($proc) {
        try {
            Stop-Process -Id $pid -Force
            Write-Host " stopped (PID=$pid)" -ForegroundColor Green
        } catch {
            Write-Host " failed to stop (PID=$pid): $_" -ForegroundColor Red
        }
    } else {
        Write-Host " already stopped (stale PID=$pid)" -ForegroundColor Gray
    }

    Remove-Item $c.PidFile -Force -ErrorAction SilentlyContinue
}

# Also kill any orphaned python processes running our scripts
$orphans = Get-WmiObject Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match "webhook_server_live|position_monitor" }

if ($orphans -and @($orphans).Count -gt 0) {
    Write-Host ""
    Write-Host "  Cleaning up orphaned Python processes:" -ForegroundColor Yellow
    foreach ($o in $orphans) {
        try {
            Stop-Process -Id $o.ProcessId -Force
            Write-Host "    Killed PID=$($o.ProcessId) ($($o.CommandLine.Substring(0, [Math]::Min(80, $o.CommandLine.Length)))...)" -ForegroundColor Gray
        } catch { }
    }
}

Write-Host ""
Write-Host "All components stopped." -ForegroundColor Green
Write-Host ""
