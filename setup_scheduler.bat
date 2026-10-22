@echo off
title NLB - Setup Daily Task Scheduler

echo.
echo =====================================================
echo   Setting up Windows Task Scheduler
echo   Task: NLB_Lottery_DailyUpdate
echo   Schedule: Every day at 22:30
echo =====================================================
echo.

schtasks /delete /tn "NLB_Lottery_DailyUpdate" /f 2>nul

schtasks /create /tn "NLB_Lottery_DailyUpdate" /tr "cmd /c cd /d C:\Lottery ^&^& python lottery_updater.py >> C:\Lottery\update_log.txt 2>&1" /sc daily /st 22:30 /f

if %ERRORLEVEL% EQU 0 (
    echo.
    echo  SUCCESS! Task scheduled.
    echo  Every day at 22:30 the lottery results will auto-update.
    echo  Log file: C:\Lottery\update_log.txt
    echo.
    echo  To view:    schtasks /query /tn "NLB_Lottery_DailyUpdate"
    echo  To run now: schtasks /run /tn "NLB_Lottery_DailyUpdate"
    echo  To remove:  schtasks /delete /tn "NLB_Lottery_DailyUpdate" /f
) else (
    echo.
    echo  [!] Task creation failed. Try running as Administrator.
    echo  Right-click setup_scheduler.bat and choose Run as administrator.
)
echo.
pause
