@echo off
title Position Monitor - LIVE
cd /d "%~dp0"
echo ============================================================
echo   Starting Position Monitor...
echo   Press Ctrl+C to stop
echo ============================================================
python -X utf8 executor/position_monitor.py
pause
