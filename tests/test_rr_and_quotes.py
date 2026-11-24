from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

from app.core.contracts import AnalysisContext
from app.core.trade_planner import build_trade_plan
from app.db.database import configure_database
from app.db.models import init_db
from app.main import app
from app.plugins.analyzers.price_action_sr import PriceActionSRAnalyzer
from app.plugins.analyzers.rr_index_trend import RRIndexTrendAnalyzer
from app.plugins.analyzers.smart_money_zones import SmartMoneyZonesAnalyzer
from tests.conftest import make_ohlcv_from_closes


def test_rr_index_trend_favors_short_near_resistance_in_bear_market():
    # Oscillate between 90 and 120, then downtrend & pull back up near 118 resistance while a correlated index trends down
    x = np.linspace(0, 6 * np.pi, 100)
    osc = 105.0 + 14.0 * np.sin(x)
    stock_tail = np.array([112.0, 104.0, 96.0, 92.0, 98.0, 106.0, 113.0, 116.5, 117.8, 117.2, 116.8, 116.4, 116.0, 115.6, 115.2, 114.9, 114.6, 114.3, 114.0, 113.8])
    stock_closes = np.concatenate([osc, stock_tail])
    # Strongly downtrending SPY that also genuinely co-moves with stock swings (corr_60 >= 0.50)
    bench_closes = np.linspace(580.0, 430.0, 120) + 1.8 * (stock_closes - 105.0)

    stock_df = make_ohlcv_from_closes(stock_closes, spread_pct=0.01)
    bench_df = make_ohlcv_from_closes(bench_closes, spread_pct=0.008)

    ctx = AnalysisContext(symbol="SHRT", daily_df=stock_df, benchmark_symbol="SPY", benchmark_df=bench_df)
    rr_res = RRIndexTrendAnalyzer().analyze(ctx)
    pa_res = PriceActionSRAnalyzer().analyze(ctx)
    smc_res = SmartMoneyZonesAnalyzer().analyze(ctx)

    assert rr_res.metrics["best_direction"].value == "SHORT"
    assert rr_res.bias == "bearish"
    assert float(rr_res.metrics["corr_60d"].value) >= 0.50
    assert float(rr_res.metrics["asymmetric_rr"].value) >= 1.5
    assert rr_res.metrics["index_trend_match"].value is True

    spot = float(stock_df["close"].iloc[-1])
    plan, _ = build_trade_plan(
        spot,
        {"rr_index_trend": rr_res, "price_action_sr": pa_res, "smart_money_zones": smc_res},
        "bearish",
        benchmark_df=bench_df,
    )
    assert plan.direction == "SHORT"
    assert plan.potential_gain_pct > plan.potential_loss_pct
    assert plan.index_aligned is True


def test_rr_index_trend_decoupled_stock_not_index_aligned():
    # Inverse/uncorrelated bear benchmark (corr_60 < 0.35, like Energy/Defense vs SPY) must NOT credit alignment
    x = np.linspace(0, 6 * np.pi, 100)
    osc = 105.0 + 14.0 * np.sin(x)
    stock_tail = np.array([112.0, 104.0, 96.0, 92.0, 98.0, 106.0, 113.0, 116.5, 117.8, 117.2, 116.8, 116.4, 116.0, 115.6, 115.2, 114.9, 114.6, 114.3, 114.0, 113.8])
    stock_closes = np.concatenate([osc, stock_tail])
    bench_closes = np.linspace(580.0, 430.0, 120) - 2.0 * (stock_closes - 105.0)

    stock_df = make_ohlcv_from_closes(stock_closes, spread_pct=0.01)
    bench_df = make_ohlcv_from_closes(bench_closes, spread_pct=0.008)

    ctx = AnalysisContext(symbol="COP", daily_df=stock_df, benchmark_symbol="SPY", benchmark_df=bench_df)
    rr_res = RRIndexTrendAnalyzer().analyze(ctx)
    pa_res = PriceActionSRAnalyzer().analyze(ctx)
    smc_res = SmartMoneyZonesAnalyzer().analyze(ctx)

    assert float(rr_res.metrics["corr_60d"].value) < 0.35
    assert rr_res.metrics["index_trend_match"].value is False
    assert all(s.code != "INDEX_TREND_ALIGNED" for s in rr_res.signals)

    spot = float(stock_df["close"].iloc[-1])
    plan, _ = build_trade_plan(
        spot,
        {"rr_index_trend": rr_res, "price_action_sr": pa_res, "smart_money_zones": smc_res},
        "bearish",
        benchmark_df=bench_df,
    )
    assert plan.direction == "SHORT"
    assert plan.index_aligned is False


def test_sqlite_migration_adds_horizon_and_watchlist_api(tmp_path: Path):
    # Simulate an older screener.db created before watchlists.horizon & tickers.history_backfilled_at existed
    db_file = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_file)
    conn.executescript(
        """
        CREATE TABLE tickers (
            symbol VARCHAR(16) PRIMARY KEY,
            name VARCHAR(128) DEFAULT '',
            sector VARCHAR(64) DEFAULT '',
            industry VARCHAR(128) DEFAULT '',
            primary_benchmark VARCHAR(16) DEFAULT 'SPY',
            sector_etf VARCHAR(16) DEFAULT 'SPY',
            discovery_sources_json TEXT DEFAULT '[]',
            is_custom BOOLEAN DEFAULT 0,
            last_enriched_at DATETIME
        );
        CREATE TABLE watchlists (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name VARCHAR(64) UNIQUE,
            color VARCHAR(24) DEFAULT '#10b981',
            sort_order INTEGER DEFAULT 0,
            created_at DATETIME
        );
        """
    )
    conn.commit()
    conn.close()

    configure_database(db_file)
    init_db()

    with TestClient(app) as client:
        wl_resp = client.post(
            "/api/watchlists",
            json={"name": "Short Candidates", "color": "#f59e0b", "horizon": "short_term"},
        )
        assert wl_resp.status_code == 201
        assert wl_resp.json()["horizon"] == "short_term"

        scr_resp = client.get("/api/screener?preset=short_term_high_rr&sort_by=potential_gain")
        assert scr_resp.status_code == 200
        assert "short_term_high_rr" in scr_resp.json()["presets"]
        assert "medium_term_invest" in scr_resp.json()["presets"]
        assert "weights" in scr_resp.json()["presets"]["short_term_high_rr"]
