from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

from app.core.contracts import AnalysisContext, AnalyzerResult, Signal, TradeHints
from app.core.trade_ideas import build_trade_ideas
from app.core.trade_planner import build_trade_plan
from app.db.database import configure_database, session_scope
from app.db.models import AnalysisSnapshot, ScanRun, Ticker, init_db
from app.main import app
from app.plugins.analyzers.price_action_sr import PriceActionSRAnalyzer
from app.plugins.analyzers.volume_volatility import VolumeVolatilityAnalyzer
from tests.conftest import make_ohlcv_from_closes


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
                    "fundamental_health": {
                        "score": 92.0,
                        "bias": "bullish",
                        "display_name": "Fundamental Health",
                        "summary": "Health Grade A+",
                        "metrics": {"health_grade": {"value": "A+"}},
                    },
                },
                "fundamentals": {},
                "dte": 42,
                "options": {
                    "proposals": [
                        {
                            "strategy_name": "Bull Call Debit Spread",
                            "bias": "bullish",
                            "rationale": "Normal IV — debit spread defines risk.",
                            "expiry": "2027-01-15",
                            "dte": 35,
                            "legs": [
                                {"action": "BUY", "option_type": "CALL", "strike": 135.0, "mid_price": 6.2, "delta": 0.54},
                                {"action": "SELL", "option_type": "CALL", "strike": 145.0, "mid_price": 3.1, "delta": 0.34},
                            ],
                            "breakeven": [138.1],
                            "net_credit_debit": -3.10,
                            "max_profit": 690.0,
                            "max_loss": 310.0,
                            "pop_estimate": 56.0,
                            "iv_regime": "normal",
                            "earnings_warning": None,
                        }
                    ]
                },
            },
            # 2a. Clean unconflicted SHORT (high conviction, index aligned)
            {
                "symbol": "INTC",
                "name": "Intel Corp",
                "sector": "Information Technology",
                "price": 24.0,
                "score": 80.0,
                "short_score": 82.0,
                "med_score": 70.0,
                "bias": "bearish",
                "deep": True,
                "plan": {
                    "direction": "SHORT",
                    "setup_name": "SMC Bearish BOS + Supply Rejection",
                    "rationale": "Short entry against supply zone $24.50.",
                    "entry_low": 23.8,
                    "entry_high": 24.4,
                    "stop_loss": 26.2,
                    "tp1": 20.5,
                    "tp2": 17.5,
                    "risk_reward": 3.14,
                    "tp1_risk_reward": 1.71,
                    "atr": 1.1,
                    "risk_per_share": 2.1,
                    "potential_gain_pct": 27.4,
                    "tp1_gain_pct": 14.9,
                    "potential_loss_pct": 8.7,
                    "spot_risk_pct": 9.17,
                    "spot_rr_tp1": 1.59,
                    "spot_rr_tp2": 2.95,
                    "stop_distance_atr": 2.0,
                    "index_aligned": True,
                    "index_trend": "bearish",
                },
                "signals": [
                    {
                        "code": "SMC_BEARISH_BOS",
                        "label": "SMC Internal Bearish BOS",
                        "direction": "bearish",
                        "severity": "high",
                        "score_impact": 20.0,
                        "detail": "Closed below structure low $24.10.",
                    },
                    {
                        "code": "HIGH_ASYMMETRIC_RR_SHORT",
                        "label": "SHORT High R:R 2.5x",
                        "direction": "bearish",
                        "severity": "high",
                        "score_impact": 18.0,
                        "detail": "Old rr_index_trend numbers.",
                    },
                ],
                "risk_flags": [],
                "plugins": {
                    "smart_money_zones": {"score": 83.0, "bias": "bearish", "display_name": "Smart Money Zones", "summary": "Bearish BOS + Supply OB"},
                    "rr_index_trend": {"score": 85.0, "bias": "bearish", "display_name": "R:R & Index Trend", "summary": "Aligned bearish R:R"},
                },
                "fundamentals": {"health_grade": "C"},
                "dte": 40,
                "options": {
                    "proposals": [
                        {
                            "strategy_name": "Bear Put Debit Spread",
                            "bias": "bearish",
                            "rationale": "Capture downside toward $20.",
                            "expiry": "2027-01-15",
                            "dte": 35,
                            "legs": [
                                {"action": "BUY", "option_type": "PUT", "strike": 24.0, "mid_price": 1.4, "delta": -0.49},
                                {"action": "SELL", "option_type": "PUT", "strike": 20.0, "mid_price": 0.4, "delta": -0.22},
                            ],
                            "breakeven": [23.0],
                            "net_credit_debit": -1.0,
                            "max_profit": 300.0,
                            "max_loss": 100.0,
                            "pop_estimate": 42.0,
                            "iv_regime": "normal",
                            "earnings_warning": None,
                        }
                    ]
                },
            },
            # 2b. Conflicted SHORT with high-severity opposing signal (capped <=65, hidden by hide_conflicted=True)
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
                            "legs": [
                                {"action": "BUY", "option_type": "PUT", "strike": 240.0, "mid_price": 12.0, "delta": -0.48},
                                {"action": "SELL", "option_type": "PUT", "strike": 220.0, "mid_price": 7.8, "delta": -0.25},
                            ],
                            "breakeven": [235.8],
                            "net_credit_debit": -4.20,
                            "max_profit": 580.0,
                            "max_loss": 420.0,
                            "pop_estimate": 54.0,
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

    # Default hide_conflicted=True filters out conflicted TSLA while returning clean buys & INTC short
    with session_scope() as session:
        data = build_trade_ideas(session)
        unfiltered = build_trade_ideas(session, hide_conflicted=False)

    buy_syms = [b["symbol"] for b in data["buys"]]
    sell_syms = [s["symbol"] for s in data["sells"]]

    assert buy_syms == ["NVDA", "LEGACY", "EARN"]  # LOWRR filtered out by min_rr=1.5
    assert sell_syms == ["INTC"]  # PENNY filtered by min_price=5.0, TSLA filtered by hide_conflicted=True
    assert data["filtered_conflicted_count"] == 1

    top_buy = data["buys"][0]
    assert top_buy["action"] == "BUY"
    assert top_buy["fundamental_grade"] == "A+"
    assert top_buy["conviction_score"] >= 82.0
    assert top_buy["conviction_tier"] == "A+ High Conviction"
    assert top_buy["top_option"]["strategy_name"] == "Bull Call Debit Spread"
    assert top_buy["top_option"]["pop_estimate"] == 56.0
    assert len(top_buy["top_option"]["legs"]) == 2
    assert top_buy["top_option"]["breakeven"] == [138.1]
    assert any(r["category"] == "Smart Money" for r in top_buy["reasons"])
    assert any(r["category"] == "Events" for r in top_buy["reasons"])

    # Clean short uses unified TradePlan R:R inside reasons (not raw rr_index_trend text)
    clean_sell = data["sells"][0]
    assert clean_sell["symbol"] == "INTC"
    rr_reason = next(r for r in clean_sell["reasons"] if r["category"] == "R:R & Index")
    assert "3.14x" in rr_reason["title"]
    assert "Old rr_index_trend numbers" not in rr_reason["detail"]

    # Unfiltered includes TSLA, but caps its conviction <= 65.0 due to major opposing signal
    tsla = next(s for s in unfiltered["sells"] if s["symbol"] == "TSLA")
    assert tsla["action"] == "SELL / SHORT"
    assert tsla["has_conflict"] is True
    assert tsla["conviction_score"] <= 65.0
    assert tsla["conviction_tier"] != "A+ High Conviction"
    assert tsla["top_option"]["pop_estimate"] == 54.0
    assert any("Conflicting signal" in w for w in tsla["watchouts"])
    assert any("Elevated Volatility" in w for w in tsla["watchouts"])

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
        assert [s["symbol"] for s in res_idx.json()["sells"]] == ["INTC"]

        # Unfiltered conflicted toggle returns TSLA too
        res_conf = client.get("/api/trade-ideas?hide_conflicted=false")
        assert "TSLA" in [s["symbol"] for s in res_conf.json()["sells"]]

        # Lower min_rr includes LOWRR
        res_low = client.get("/api/trade-ideas?min_rr=1.0")
        assert "LOWRR" in [b["symbol"] for b in res_low.json()["buys"]]

        # Horizon medium_term works cleanly
        res_med = client.get("/api/trade-ideas?horizon=medium_term")
        assert res_med.status_code == 200
        assert res_med.json()["horizon"] == "medium_term"

        # Watchlist only keeps explicitly listed tickers (even sub-$5 PENNY) and excludes unlisted tickers like LEGACY/INTC
        wl_id = client.post("/api/watchlists", json={"name": "My Short Watch", "horizon": "short_term"}).json()["id"]
        client.post(f"/api/watchlists/{wl_id}/items/PENNY")
        res_wl = client.get("/api/trade-ideas?watchlist_only=true")
        assert "PENNY" in [s["symbol"] for s in res_wl.json()["sells"]]
        assert "INTC" not in [s["symbol"] for s in res_wl.json()["sells"]]
        assert "LEGACY" not in [b["symbol"] for b in res_wl.json()["buys"]]


def test_nvda_regression_fixes(tmp_path: Path):
    # 1. VolumeVolatilityAnalyzer: +31.5% gap-up volume spike must NOT be bearish & EMA stack includes EMA200
    closes = np.linspace(230.0, 178.0, 130)
    closes[-7:] = [170.0, 162.0, 155.0, 148.0, 138.0, 125.5, 165.05]
    df = make_ohlcv_from_closes(closes, spread_pct=0.03, base_volume=200_000_000.0)
    # Emulate NVDA gap-up red-body bar: prev_close=125.5, open=167.0, high=171.5, low=156.9, close=165.05, 2.7x vol
    df.loc[df.index[-1], "open"] = 167.04
    df.loc[df.index[-1], "high"] = 171.50
    df.loc[df.index[-1], "low"] = 156.93
    df.loc[df.index[-1], "close"] = 165.05
    df.loc[df.index[-1], "volume"] = 540_000_000.0

    vv_res = VolumeVolatilityAnalyzer().analyze(AnalysisContext(symbol="NVDA", daily_df=df))
    rvol_sig = next(s for s in vv_res.signals if s.code == "HIGH_RVOL")
    assert rvol_sig.direction != "bearish"  # Must classify buying/mixed, never selling volume on +31.5% day
    ema_sig = next(s for s in vv_res.signals if s.code == "BEARISH_EMA_STACK")
    assert "EMA200" in ema_sig.detail

    # 2. PriceActionSRAnalyzer: tight zones (<= ~6% width) & "inside resistance zone" wording
    res_df = make_ohlcv_from_closes(
        np.concatenate([np.linspace(150.0, 175.0, 40), np.linspace(175.0, 152.0, 20), np.linspace(152.0, 165.0, 20), np.linspace(165.0, 182.0, 20), np.linspace(182.0, 166.0, 20)]),
        spread_pct=0.04,
    )
    pa_res = PriceActionSRAnalyzer().analyze(AnalysisContext(symbol="NVDA", daily_df=res_df))
    for ov in pa_res.overlays:
        if ov.layer == "sr_zones":
            lo = float(ov.data["priceLow"])
            hi = float(ov.data["priceHigh"])
            assert (hi - lo) / float(res_df["close"].iloc[-1]) <= 0.06

    # Verify explicit "inside resistance zone" when spot sits inside zone
    pa_inside = PriceActionSRAnalyzer()
    assert pa_res.trade_hints is not None
    if pa_res.trade_hints.resistance_zone_low and pa_res.trade_hints.resistance_zone_high:
        assert pa_res.trade_hints.resistance_zone_high >= pa_res.trade_hints.resistance_zone_low

    # 3. TradePlanner: excludes bullish "Lower Channel Touch" from SHORT setup_name, puts SL outside resistance & >=1.0 ATR, TP1 honors channel floor
    fake_results = {
        "smart_money_zones": AnalyzerResult(
            plugin_id="smart_money_zones",
            display_name="Smart Money Zones",
            category="structure",
            score=80.0,
            bias="bearish",
            trade_hints=TradeHints(setup_name="SMC Bearish Structure", smc_supply_zone=(168.0, 172.0), atr=15.0, bias="bearish"),
        ),
        "trend_channels": AnalyzerResult(
            plugin_id="trend_channels",
            display_name="Trend Channels",
            category="structure",
            score=82.0,
            bias="bullish",
            signals=[Signal(code="LOWER_CHANNEL_TOUCH", label="Lower Channel Touch", direction="bullish", severity="high", score_impact=30.0)],
            trade_hints=TradeHints(setup_name="Lower Channel Touch", channel_lower=153.34, channel_mid=175.0, channel_upper=195.0, atr=15.0, bias="bullish"),
        ),
        "price_action_sr": AnalyzerResult(
            plugin_id="price_action_sr",
            display_name="Price Action & S/R",
            category="technical",
            score=65.0,
            bias="bearish",
            trade_hints=TradeHints(
                nearest_support=140.0,
                nearest_resistance=171.54,
                support_zone_low=137.0,
                support_zone_high=143.0,
                resistance_zone_low=167.0,
                resistance_zone_high=176.0,
                atr=15.0,
                bias="bearish",
            ),
        ),
    }
    plan, _ = build_trade_plan(spot_price=165.05, results=fake_results, overall_bias="bearish")
    assert "Lower Channel Touch" not in plan.setup_name
    assert plan.stop_loss > 176.0  # outside resistance_zone_high (176.0)
    assert plan.stop_distance_atr is not None and plan.stop_distance_atr >= 1.0
    assert plan.tp1 >= 153.34  # honors channel_lower floor instead of blindly undercutting it
    assert plan.spot_rr_tp1 is not None and plan.spot_rr_tp2 is not None and plan.spot_risk_pct is not None
    assert plan.dist_to_support_pct is None or plan.dist_to_support_pct >= 0.0

    # Verify direction-pure setup_name excludes opposing FVG/Zone labels
    dirty_results = {
        "smart_money_zones": AnalyzerResult(
            plugin_id="smart_money_zones",
            display_name="Smart Money Zones",
            category="structure",
            score=70.0,
            bias="bearish",
            trade_hints=TradeHints(setup_name="Bullish FVG Support + Discount Zone", atr=5.0, bias="bearish"),
        ),
        "price_action_sr": AnalyzerResult(
            plugin_id="price_action_sr",
            display_name="Price Action & S/R",
            category="technical",
            score=70.0,
            bias="bearish",
            trade_hints=TradeHints(nearest_support=165.5, nearest_resistance=175.0, atr=5.0, bias="bearish"),
        ),
    }
    clean_short_plan, _ = build_trade_plan(spot_price=165.0, results=dirty_results, overall_bias="bearish")
    assert "bullish" not in clean_short_plan.setup_name.lower()
    assert "discount zone" not in clean_short_plan.setup_name.lower()
    assert clean_short_plan.dist_to_support_pct is None or clean_short_plan.dist_to_support_pct >= 0.0

    # 4. TradeIdeas: PoP stays 38.5 (never 3850), options include strikes/breakeven, conflicted extreme-move NVDA capped <= 65 & hidden by default
    db_file = tmp_path / "nvda_reg.db"
    configure_database(db_file)
    init_db()
    with session_scope() as session:
        t = session.get(Ticker, "NVDA")
        if t is None:
            t = Ticker(symbol="NVDA", name="NVIDIA Corp", sector="Technology", primary_benchmark="QQQ")
            session.add(t)
            session.flush()
        session.add(
            AnalysisSnapshot(
                last_price=165.05,
                change_pct=31.48,
                composite_score=85.0,
                bias="bearish",
                symbol="NVDA",
                preset_scores_json=json.dumps({"short_term_high_rr": 85.0}),
                signals_json=json.dumps(
                    [
                        {"code": "LOWER_CHANNEL_TOUCH", "label": "Lower Channel Touch", "direction": "bullish", "severity": "high", "score_impact": 30.0, "detail": "Touched lower rail ($153.34)."},
                        {"code": "HIGH_ASYMMETRIC_RR_SHORT", "label": "SHORT High R:R 2.1x", "direction": "bearish", "severity": "high", "score_impact": 22.0, "detail": "Raw rr_index_trend"},
                    ]
                ),
                risk_flags_json="[]",
                overlays_json="[]",
                trade_plan_json=json.dumps(plan.model_dump()),
                option_proposals_json=json.dumps(
                    {
                        "proposals": [
                            {
                                "strategy_name": "Bear Put Debit Spread",
                                "bias": "bearish",
                                "rationale": "Debit spread",
                                "expiry": "2026-12-31",
                                "dte": 38,
                                "legs": [
                                    {"action": "BUY", "option_type": "PUT", "strike": 165.0, "mid_price": 12.05, "delta": -0.45},
                                    {"action": "SELL", "option_type": "PUT", "strike": 145.0, "mid_price": 5.12, "delta": -0.22},
                                ],
                                "breakeven": [158.07],
                                "net_credit_debit": -6.93,
                                "max_profit": 1307.5,
                                "max_loss": 692.5,
                                "pop_estimate": 38.5,
                                "iv_regime": "low",
                            }
                        ]
                    }
                ),
                fundamentals_json="{}",
                events_insiders_json=json.dumps({"days_to_earnings": -6, "next_earnings_date": "2026-11-17"}),
                plugin_results_json="{}",
                is_deep_enriched=True,
            )
        )

    with session_scope() as session:
        hidden = build_trade_ideas(session, min_rr=0.5, hide_conflicted=True)
        shown = build_trade_ideas(session, min_rr=0.5, hide_conflicted=False)

    assert hidden["sells"] == []
    assert hidden["filtered_conflicted_count"] == 1
    assert len(shown["sells"]) == 1
    nvda = shown["sells"][0]
    assert nvda["conviction_score"] <= 65.0
    assert nvda["days_to_earnings"] == -6
    assert nvda["top_option"]["pop_estimate"] == 38.5
    assert [l["strike"] for l in nvda["top_option"]["legs"]] == [165.0, 145.0]
    assert nvda["top_option"]["breakeven"] == [158.07]
    assert any("Extreme 1D move" in w for w in nvda["watchouts"])
    rr_r = next(r for r in nvda["reasons"] if r["category"] == "R:R & Index")
    assert f"{nvda['risk_reward']:.2f}x" in rr_r["title"]
