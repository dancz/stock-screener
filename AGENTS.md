# Daily US Stock Screener & Swing Trading Platform — Agent Guide

This repository contains a local-first, extensible web application for **daily US swing-trading stock screening**, engineered for **Windows 11 development** and **Raspberry Pi (ARM64 Linux) single-process production**.

---

## Architecture Overview

- **Backend (`backend/app/`)**: Python 3.12 + FastAPI + SQLAlchemy 2.0 (SQLite WAL mode) + APScheduler.
- **Frontend (`frontend/src/`)**: React + TypeScript + Vite + Tailwind CSS + TradingView `lightweight-charts` (v4.2.3) + Zustand.
- **Single-Process Production**: On Raspberry Pi (or prod mode on Windows), `frontend/dist` is served directly by FastAPI on port `8000` alongside `/api/*` (~150–300 MB RAM, no Docker or Node runtime required in prod).
- **Two-Stage Scan Pipeline (`backend/app/core/pipeline.py`)**:
  - **Stage 1**: Bulk-download 1Y daily OHLCV (`yfinance.download` in chunks of 60) + bulk movers/insiders (Finviz, Yahoo Movers, OpenInsider) across ~600 tickers (S&P 500 + Nasdaq 100 + dynamic movers + watchlists), and run all fast local quantitative/technical analyzers.
  - **Stage 2**: Deeply enrich the Top 90 Stage-1 candidates + **all user watchlist tickers** with 1H candles, fundamentals, earnings/events, and option chains (IBKR Gateway if connected, else Yahoo + analytical Black-Scholes). Any lower-ranked ticker is enriched **on-demand** when clicked in the UI.

---

## Hard Constraints & Policies

1. **Raspberry Pi ARM64 Pure Wheels Only**:
   - Never add C-extension dependencies that fail to compile on `aarch64` (strictly NO `TA-Lib`). Use `numpy`, `pandas`, and `scipy`.
2. **Graceful External Feed Degradation**:
   - The app MUST work out-of-the-box with zero API keys (`yfinance`, Finviz, OpenInsider, SEC EDGAR).
   - Every external scraper/feed (Finviz, Yahoo, OpenInsider, SEC EDGAR, Finnhub) must catch network/parsing errors gracefully and degrade without crashing a scan.
3. **IBKR Gateway Policy (Read-Only Data + Strict Paper-Only Lock)**:
   - `market_data_ibkr.py` and `IbkrSessionWorker` (`request_queue.py`) remain strictly **read-only** (`readonly=True`, market snapshots, 1H/1D bars, option chain Greeks/IV).
   - Isolated order placement, modification, and cancellation are permitted **exclusively** in `backend/app/core/paper_ibkr.py` (`IbkrPaperExecutionWorker`, `clientId + 10`) and **must** pass `assert_paper_account()` before every connection and order operation:
     - Port MUST be `4002` (IB Gateway Paper) or `7497` (TWS Paper). Any other port (`4001`, `7496`, etc.) raises `LiveAccountBlockedError`.
     - `ib.managedAccounts()` MUST be non-empty and **every** account ID MUST start with `"DU"` or `"DF"`. Any live account (`U*`, `F*`, etc.) raises `LiveAccountBlockedError` and immediately disconnects.
     - Every `Order` MUST explicitly set `order.account = verified_paper_account`.
   - IBKR Gateway (`host:port:clientId`) must be optional with a short connect timeout (`<= 3s`) and transparent fallback to Yahoo Finance + `black_scholes.py`.
4. **100% English UI**:
   - All UI labels, tooltips, signal badges, option strategy names, and error messages in `frontend/` and API-returned display strings must be in English.

---

## Plugin System (`backend/app/plugins/`)

`PluginRegistry` (`backend/app/core/registry.py`) automatically discovers subclasses via `pkgutil.iter_modules`:

- `backend/app/plugins/sources/` → `SourcePlugin`
- `backend/app/plugins/analyzers/` → `AnalyzerPlugin`
- `backend/app/plugins/options/` → `OptionStrategyPlugin`
- `backend/app/plugins/notifiers/` → `NotifierPlugin`

### Adding a New Analyzer Plugin

1. Create a new `.py` file in `backend/app/plugins/analyzers/` subclassing `AnalyzerPlugin`.
2. Define a unique `plugin_id`, `display_name`, `category`, `default_weight`, and implement `analyze(ctx: AnalysisContext) -> AnalyzerResult`.
3. Emit:
   - `score` (`0..100`), `bias` (`"bullish" | "bearish" | "neutral"`), `confidence` (`0..1`), `summary`
   - `signals` (`Signal` objects explaining why the stock scored)
   - `risk_flags` (`RiskFlag` warnings)
   - `metrics` (`dict[str, MetricItem]` rendered automatically in UI)
   - `overlays` (`ChartOverlay` objects using layers `channels`, `sr_zones`, `price_action`, `trade_plan`, `emas`, `volume_profile`, `volume` and kinds `horizontal_zone`, `channel`, `trend_line`, `marker`, `line_series`, `price_box`) — **the frontend renders all overlays generically with no React changes needed**.
   - Optional `trade_hints` used by `TradePlanner`.

---

## Commands

### Development (Windows)
```powershell
.\scripts\dev.ps1
```
Or manually:
```powershell
# Backend
backend\.venv\Scripts\uvicorn.exe app.main:app --app-dir backend --reload --port 8000
# Frontend
npm run dev --prefix frontend
```

### Tests & Build Verification
```powershell
backend\.venv\Scripts\pytest.exe -v
npm run build --prefix frontend
```

### Production Build & Raspberry Pi Deployment
```powershell
.\scripts\build-prod.ps1
```
On Raspberry Pi (ARM64 Linux):
```bash
bash scripts/setup-rpi.sh
```
Service unit is in `deploy/stock-screener.service`.

---

## Workspace MCP Plugin

A read-only workspace MCP plugin is configured at `.agents/plugins/stock-screener-dev/` backed by `backend/app/mcp_server.py`. It provides tools to inspect the SQLite screener DB (`query_screener_db`), list auto-discovered plugins (`list_screener_plugins`), inspect top ranked stocks (`get_latest_screener_top`), and inspect ticker snapshots (`get_ticker_snapshot`).
