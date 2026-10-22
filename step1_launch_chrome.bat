@echo off
title Winway - Launch Chrome
echo.
echo ============================================================
echo   STEP 1 of 2: Launching Chrome with remote debugging...
echo ============================================================
echo.

REM Try standard Chrome path
set CHROME="C:\Program Files\Google\Chrome\Application\chrome.exe"
if not exist %CHROME% set CHROME="C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"

REM Launch Chrome with debugging enabled and a separate profile
start "" %CHROME% ^
  --remote-debugging-port=9222 ^
  --user-data-dir="C:\ChromeWinway" ^
  --no-first-run ^
  --no-default-browser-check ^
  https://winway.lk

echo Chrome launched! 
echo.
echo ============================================================
echo   IMPORTANT: 
echo   1. Log in to your winway.lk account in the Chrome window
echo   2. Once logged in, run  step2_run_agent.bat
echo ============================================================
echo.
pause
