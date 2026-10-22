@echo off
title NLB Lottery - Full Historical Setup

echo.
echo =====================================================
echo   NLB LOTTERY - FULL SYSTEM SETUP
echo   This will:
echo      1. Initialize databases for all active NLB lotteries
echo      2. Migrate any legacy records from nlb_results.db
echo      3. Scrape ALL historical draws
echo      4. Compute initial probability
echo =====================================================
echo.
pause

echo [1/4] Initializing database schemas for all lotteries...
python lottery_db_manager.py init
echo.

echo [2/4] Migrating legacy records...
python lottery_db_manager.py migrate
echo.

echo [3/4] Downloading full historical draws from nlb.lk...
:: Using a high limit to ensure it fetches all historical pages
python lottery_updater.py --limit 10000
echo.

echo [4/4] Training offline agent and generating predictions...
python lottery_agent.py all
echo.

echo Database status report:
python lottery_db_manager.py status
echo.

echo =====================================================
echo   SETUP COMPLETE!
echo   Historical data ingested. Use run_daily.bat for updates.
echo =====================================================
echo.
pause