# 🎰 Lottery Prediction System

An automated lottery data scraping, analysis, and prediction system for Sri Lanka's National Lottery Board (NLB) and other lottery providers.

## ✨ Features

- 🤖 **Automated Agent** — `lottery_agent_v2.py` intelligently scrapes live lottery data
- 📊 **Analytics Dashboard** — `analytics.py` for deep statistical analysis
- 🔮 **Predictor** — `predictor.py` uses historical data patterns for number prediction
- 📅 **Daily Results** — Automated daily result fetching via `daily_results.py`
- 🗄️ **Database Management** — Full SQLite DB management with `lottery_db_manager.py`
- 📈 **Performance Tracker** — Track prediction accuracy over time
- 🌐 **Multi-Provider** — Supports NLB scraper + WinWay lottery sources
- 🖥️ **Browser Automation** — Playwright-powered scraping for dynamic sites

## 🚀 Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
playwright install chromium
```

### 2. Run the Agent

```bash
# Step 1: Launch Chrome (remote debugging)
step1_launch_chrome.bat

# Step 2: Run the agent
step2_run_agent.bat

# Step 3: Fetch daily results
step3_daily_results.bat

# Step 4: View analytics / performance
step4_analytics.bat
step4_performance.bat
```

### 3. One-Click Daily Run

```bash
run_daily.bat
```

## 📁 Project Structure

```
lottery-prediction-system/
├── lottery_agent_v2.py       # Main agent (v2) - core automation
├── lottery_agent.py          # Agent v1 (legacy)
├── winway_agent.py           # WinWay lottery agent
├── analytics.py              # Statistical analytics
├── predictor.py              # Number prediction engine
├── daily_results.py          # Daily result scraper
├── lottery_db_manager.py     # Database manager
├── lottery_updater.py        # Database updater
├── nlb_database.py           # NLB-specific DB operations
├── nlb_scraper_pw.py         # NLB Playwright scraper
├── performance_tracker.py    # Prediction performance tracker
├── run_agent.py              # Agent runner
├── winway_run.py             # WinWay runner
├── requirements.txt          # Python dependencies
├── setup_first_time.bat      # First-time setup script
├── run_daily.bat             # Daily automation script
└── step*.bat                 # Step-by-step run scripts
```

## 🛠️ Requirements

- Python 3.9+
- Playwright (Chromium)
- SQLite (built-in)

See [`requirements.txt`](requirements.txt) for full dependency list.

## ⚙️ Configuration

The system uses a local SQLite database (`nlb_results.db`, excluded from repo) to store:
- Historical lottery results
- Prediction records
- Performance metrics

## 📌 Version

**v2.0.0** — Major refactor with `lottery_agent_v2.py`, improved scraping reliability, and added WinWay support.

## 📄 License

MIT License — feel free to fork and improve!
