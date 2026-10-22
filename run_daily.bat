@echo off
title NLB Lottery - Daily Update and Advanced Predict

echo.
echo =====================================================
echo   NLB LOTTERY DAILY RUNNER (Advanced v2)
echo   %date%  %time%
echo =====================================================
echo.

echo [1/4] Initializing databases...
python lottery_db_manager.py init
echo.

echo [2/4] Updating lottery results (only missing draws)...
python lottery_updater.py
echo.

echo [3/4] Running ADVANCED AI agent v2 predictions...
python lottery_agent_v2.py all
echo.

echo [4/4] Running analytics dashboard...
python analytics.py
echo.

echo =====================================================
echo   DONE! All lotteries updated and predicted.
echo =====================================================
echo.
pause
