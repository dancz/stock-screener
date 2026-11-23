from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.core.trade_ideas import build_trade_ideas
from app.db.database import configure_database, session_scope
from app.db.models import AnalysisSnapshot, ScanRun, Ticker, init_db
from app.main import app


def _seed_trade_ideas_db(db_file: Path) -> None:
    configure_database(db_file)
    init_db()

    with session_scope() as session:
        run = ScanRun(
            mode="quick",
            status="completed",
            stage="completed",
            total_tickers=6,
            processed_tickers=6,
            market_regime_json=json.dumps({"regime": "NEUTRAL", "summary": "Mixed tape"}),
        )
        session.add(run)
        session.flush()

        specs = [
            # 1. Strong LONG with confirming signals, index alignment, and option idea
            {
                "symbol": "NVDA",
                "name": "NVIDIA Corp",
                "sector": "Information Technology",
                "price": 135.0,
                "score": 84.0,
                "short_score": 85.0,
                "med_score": 80.0,
                "bias": "bullish",
                "deep": True,
                "plan": {
                    "direction": "LONG",
                    "setup_name": "SMC Demand OB + Lower Channel Bounce",
                    "rationale": "Long entry at Demand OB.",
                    "entry_low": 133.5,
                    "entry_high": 135.5,
                    "stop_loss": 128.0,
                    "tp1": 145.0,
                    "tp2": 154.0,
                    "risk_reward": 3.0,
                    "atr": 3.5,
                    "risk_per_share": 6.5,
                    "potential_gain_pct": 14.5,
                    "tp1_gain_pct": 7.8,
                    "potential_loss_pct": 4.8,
                    "index_aligned": True,
                    "index_trend": "bullish",
                },
                "signals": [
                    {
                        "code": "SMC_BULLISH_CHOCH",
                        "label": "SMC Internal Bullish CHoCH",
                        "direction": "bullish",
                        "severity": "high",
                        "score_impact": 20.0,
                        "detail": "Closed above structure level $134.10.",
                    },
                    {
                        "code": "LOWER_CHANNEL_TOUCH",
                        "label": "Lower Channel Touch",
                        "direction": "bullish",
                        "severity": "high",
                        "score_impact": 18.0,
                        "detail": "Bounced off 120d ascending channel lower rail.",
                    },
                ],
                "risk_flags": [],
                "plugins": {
                    "smart_money_zones": {"score": 88.0, "bias": "bullish", "display_name": "Smart Money Zones", "summary": "Demand OB $131-$134"},
                    "trend_channels": {"score": 82.0, "bias": "bullish", "display_name": "Trend Channels", "summary": "Ascending lower rail bounce"},
                    "fundamental_health": {"score": 92.0, "bias": "bullish", "display_name": "Fundamental Health", "summary": "Health Grade A+"},
                },
                "fundamentals": {"health_grade": "A+"},
                "dte": 42,
                "options": {
                    "proposals": [
                        {
                            "strategy_name": "Bull Call Debit Spread",
                            "bias": "bullish",
                            "rationale": "Normal IV — debit spread defines risk.",
                            "expiry": "2027-01-15",
                            "dte": 35,
                            "net_credit_debit": -3.10,
                            "max_profit": 690.0,
                            "max_loss": 310.0,
                            "pop_estimate": 0.56,
                            "iv_regime": "normal",
                            "earnings_warning": None,
                        }
                    ]
                },
            },
            # 2. Strong SHORT with opposing signal surfaced in watchouts + bearish option idea
            {
                "symbol": "TSLA",
                "name": "Tesla Inc",
                "sector": "Consumer Discretionary",
                "price": 240.0,
                "score": 81.0,
                "short_score": 83.0,
                "med_score": 68.0,
                "bias": "bearish",
                "deep": True,
                "plan": {
                    "direction": "SHORT",
                    "setup_name": "Supply Rejection + Descending Channel",
                    "rationale": "Short entry below Supply Zone.",
                    "entry_low": 238.0,
                    "entry_high": 241.5,
                    "stop_loss": 252.0,
                    "tp1": 220.0,
                    "tp2": 205.0,
                    "risk_reward": 2.83,
                    "atr": 7.0,
                    "risk_per_share": 12.25,
                    "potential_gain_pct": 14.5,
                    "tp1_gain_pct": 8.2,
                    "potential_loss_pct": 5.1,
                    "index_aligned": True,
                    "index_trend": "bearish",
                },
                "signals": [
                    {
                        "code": "SMC_BEARISH_BOS",
                        "label": "SMC Internal Bearish BOS",
                        "direction": "bearish",
                        "severity": "high",
                        "score_impact": 22.0,
                        "detail": "Closed below structure low $242.00.",
                    },
                    {
                        "code": "HIGH_ASYMMETRIC_RR_SHORT",
                        "label": "SHORT High R:R 2.8x",
                        "direction": "bearish",
                        "severity": "high",
                        "score_impact": 19.0,
                        "detail": "Target distance +14.5% vs S/R invalidation -5.1%.",
                    },
                    {
                        "code": "AT_SUPPORT_ZONE",
                        "label": "Minor Support Near $236",
                        "direction": "bullish",
                        "severity": "high",
                        "score_impact": 10.0,
                        "detail": "Watch reaction around minor support $236.",
                    },
                ],
                "risk_flags": [
                    {"code": "HIGH_VOL", "label": "Elevated Volatility", "severity": "warning", "detail": "ATR is 4.8% of price."}
                ],
                "plugins": {
                    "smart_money_zones": {"score": 84.0, "bias": "bearish", "display_name": "Smart Money Zones", "summary": "Bearish BOS + Supply OB"},
                    "rr_index_trend": {"score": 86.0, "bias": "bearish", "display_name": "R:R & Index Trend", "summary": "SHORT 2.8x aligned with QQQ"},
                    "fundamental_health": {"score": 45.0, "bias": "neutral", "display_name": "Fundamental Health", "summary": "Health Grade C"},
                },
                "fundamentals": {"health_grade": "C"},
                "dte": 30,
                "options": {
                    "proposals": [
                        {
                            "strategy_name": "Bear Put Debit Spread",
                            "bias": "bearish",
                            "rationale": "Capture downside toward $220.",
                            "expiry": "2027-01-15",
                            "dte": 35,
                            "net_credit_debit": -4.20,
                            "max_profit": 580.0,
                            "max_loss": 420.0,
                            "pop_estimate": 0.54,
                            "iv_regime": "normal",
                            "earnings_warning": None,
                        }
                    ]
                },
            },
            # 3. Legacy snapshot (no potential_gain_pct, no index_aligned, Stage-1 only)
            {
                "symbol": "LEGACY",
                "name": "Legacy Corp",
                "sector": "Industrials",
                "price": 50.0,
                "score": 73.0,
                "short_score": 73.0,
                "med_score": 73.0,
                "bias": "bullish",
                "deep": False,
                "plan": {
                    "direction": "LONG",
                    "setup_name": "Support Bounce",
                    "rationale": "Testing major daily support at $49.",
                    "entry_low": 49.5,
                    "entry_high": 50.5,
                    "stop_loss": 47.0,
                    "tp1": 54.0,
                    "tp2": 57.5,
                    "risk_reward": 2.5,
                },
                "signals": [],
                "risk_flags": [],
                "plugins": {},
                "fundamentals": {},
                "dte": None,
                "options": {},
            },
            # 4. Imminent earnings LONG (excluded when exclude_earnings_days=7)
            {
                "symbol": "EARN",
                "name": "Earnings Soon Inc",
                "sector": "Financials",
                "price": 80.0,
                "score": 76.0,
                "short_score": 76.0,
                "med_score": 76.0,
                "bias": "bullish",
                "deep": True,
                "plan": {
                    "direction": "LONG",
                    "setup_name": "Breakout",
                    "rationale": "Testing breakout level.",
                    "entry_low": 79.0,
                    "entry_high": 80.5,
                    "stop_loss": 75.0,
                    "tp1": 86.0,
                    "tp2": 91.0,
                    "risk_reward": 2.3,
                    "potential_loss_pct": 5.9,
                    "potential_gain_pct": 14.1,
                    "index_aligned": False,
                    "index_trend": "bearish",
                },
                "signals": [],
                "risk_flags": [{"code": "EARNINGS_SOON", "label": "Earnings in 3d", "severity": "high", "detail": "Binary gap risk."}],
                "plugins": {},
                "fundamentals": {"health_grade": "B"},
                "dte": 3,
                "options": {},
            },
            # 5. Low R:R snapshot (filtered out when min_rr=1.5)
            {
                "symbol": "LOWRR",
                "name": "Low RR Co",
                "sector": "Utilities",
                "price": 60.0,
                "score": 79.0,
                "short_score": 79.0,
                "med_score": 79.0,
                "bias": "bullish",
                "deep": False,
                "plan": {
                    "direction": "LONG",
                    "setup_name": "Range",
                    "rationale": "Low RR",
                    "entry_low": 59.5,
                    "entry_high": 60.5,
                    "stop_loss": 56.0,
                    "tp1": 62.5,
                    "tp2": 64.4,
                    "risk_reward": 1.1,
                },
                "signals": [],
                "risk_flags": [],
                "plugins": {},
                "fundamentals": {},
                "dte": None,
                "options": {},
            },
            # 6. Sub-$5 stock (excluded by min_price=5.0 unless watchlist_only)
            {
                "symbol": "PENNY",
                "name": "Penny Stock",
                "sector": "Materials",
                "price": 2.5,
                "score": 88.0,
                "short_score": 88.0,
                "med_score": 70.0,
                "bias": "bearish",
                "deep": False,
                "plan": {
                    "direction": "SHORT",
                    "setup_name": "Breakdown",
                    "rationale": "Short penny",
                    "entry_low": 2.45,
                    "entry_high": 2.55,
                    "stop_loss": 2.80,
                    "tp1": 2.00,
                    "tp2": 1.60,
                    "risk_reward": 3.0,
                },
                "signals": [],
                "risk_flags": [],
                "plugins": {},
                "fundamentals": {},
                "dte": None,
                "options": {},
            },
        ]

        for s in specs:
            existing_ticker = session.get(Ticker, s["symbol"])
            if existing_ticker is None:
                session.add(
                    Ticker(
                        symbol=s["symbol"],
                        name=s["name"],
                        sector=s["sector"],
                        primary_benchmark="QQQ",
                    )
                )
            else:
                existing_ticker.name = s["name"]
                existing_ticker.sector = s["sector"]
                existing_ticker.primary_benchmark = "QQQ"
            session.flush()
            session.add(
                AnalysisSnapshot(
                    scan_run_id=run.id,
                    symbol=s["symbol"],
                    last_price=s["price"],
                    change_pct=1.2 if s["bias"] == "bullish" else -1.4,
                    composite_score=s["score"],
                    bias=s["bias"],
                    preset_scores_json=json.dumps(
                        {
                            "all_around_swing": s["score"],
                            "short_term_high_rr": s["short_score"],
                            "medium_term_invest": s["med_score"],
                        }
                    ),
                    signals_json=json.dumps(s["signals"]),
                    risk_flags_json=json.dumps(s["risk_flags"]),
                    overlays_json="[]",
                    trade_plan_json=json.dumps(s["plan"]),
                    option_proposals_json=json.dumps(s["options"]),
                    fundamentals_json=json.dumps(s["fundamentals"]),
                    events_insiders_json=json.dumps({"days_to_earnings": s["dte"]} if s["dte"] is not None else {}),
                    plugin_results_json=json.dumps(s["plugins"]),
                    is_deep_enriched=s["deep"],
                )
            )


def test_build_trade_ideas_ranking_reasons_and_legacy_normalization(tmp_path: Path):
    _seed_trade_ideas_db(tmp_path / "ideas.db")

    with session_scope() as session:
        data = build_trade_ideas(session)

    buy_syms = [b["symbol"] for b in data["buys"]]
    sell_syms = [s["symbol"] for s in data["sells"]]

    assert buy_syms == ["NVDA", "LEGACY", "EARN"]  # LOWRR filtered out by min_rr=1.5
    assert sell_syms == ["TSLA"]  # PENNY filtered out by min_price=5.0

    top_buy = data["buys"][0]
    assert top_buy["action"] == "BUY"
    assert top_buy["conviction_score"] >= 82.0
    assert top_buy["conviction_tier"] == "A+ High Conviction"
    assert top_buy["top_option"]["strategy_name"] == "Bull Call Debit Spread"
    assert any(r["category"] == "Smart Money" for r in top_buy["reasons"])
    assert any(r["category"] == "Events" for r in top_buy["reasons"])

    top_sell = data["sells"][0]
    assert top_sell["action"] == "SELL / SHORT"
    assert top_sell["top_option"]["strategy_name"] == "Bear Put Debit Spread"
    assert any(r["category"] == "Smart Money" for r in top_sell["reasons"])
    assert any("Conflicting signal" in w for w in top_sell["watchouts"])
    assert any("Elevated Volatility" in w for w in top_sell["watchouts"])

    legacy = next(b for b in data["buys"] if b["symbol"] == "LEGACY")
    assert legacy["potential_gain_pct"] > 0
    assert legacy["tp1_gain_pct"] > 0
    assert legacy["potential_loss_pct"] > 0
    assert any("Stage-1 technical snapshot" in w for w in legacy["watchouts"])


def test_api_trade_ideas_filters_and_watchlist(tmp_path: Path):
    _seed_trade_ideas_db(tmp_path / "ideas_api.db")

    with TestClient(app) as client:
        # Exclude earnings < 7d removes EARN
        res = client.get("/api/trade-ideas?exclude_earnings_days=7")
        assert res.status_code == 200
        payload = res.json()
        assert [b["symbol"] for b in payload["buys"]] == ["NVDA", "LEGACY"]
        assert payload["last_scan"]["status"] == "completed"

        # Index-aligned only removes counter-trend LEGACY and EARN
        res_idx = client.get("/api/trade-ideas?index_aligned_only=true")
        assert [b["symbol"] for b in res_idx.json()["buys"]] == ["NVDA"]
        assert [s["symbol"] for s in res_idx.json()["sells"]] == ["TSLA"]

        # Lower min_rr includes LOWRR
        res_low = client.get("/api/trade-ideas?min_rr=1.0")
        assert "LOWRR" in [b["symbol"] for b in res_low.json()["buys"]]

        # Horizon medium_term works cleanly
        res_med = client.get("/api/trade-ideas?horizon=medium_term")
        assert res_med.status_code == 200
        assert res_med.json()["horizon"] == "medium_term"

        # Watchlist only keeps explicitly listed tickers (even sub-$5 PENNY) and excludes unlisted tickers like LEGACY/TSLA
        wl_id = client.post("/api/watchlists", json={"name": "My Short Watch", "horizon": "short_term"}).json()["id"]
        client.post(f"/api/watchlists/{wl_id}/items/PENNY")
        res_wl = client.get("/api/trade-ideas?watchlist_only=true")
        assert "PENNY" in [s["symbol"] for s in res_wl.json()["sells"]]
        assert "TSLA" not in [s["symbol"] for s in res_wl.json()["sells"]]
        assert "LEGACY" not in [b["symbol"] for b in res_wl.json()["buys"]]
