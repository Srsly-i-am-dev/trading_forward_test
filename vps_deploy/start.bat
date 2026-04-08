@echo off
title Webhook Server - LIVE
cd /d "%~dp0"
echo ============================================================
echo   Starting Live Webhook Server...
echo   Press Ctrl+C to stop
echo ============================================================
python -X utf8 app.py
pause
