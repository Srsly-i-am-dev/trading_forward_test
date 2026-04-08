@echo off
echo Stopping all trading processes...
taskkill /F /FI "WINDOWTITLE eq Webhook Server*" 2>nul
taskkill /F /FI "WINDOWTITLE eq Position Monitor*" 2>nul
echo.
echo Done. Both servers stopped.
pause
