from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from fastapi.testclient import TestClient

from app.core import pipeline
from app.core.paper_analytics import build_paper_overview, build_signal_attribution, export_trades_csv
from app.core.paper_risk import check_kill_switch, ensure_active_paper_run, evaluate_candidate_gate
from app.core.paper_virtual import (
    run_paper_allocation_cycle,
    set_paper_kill_switch,
    step_virtual_lifecycle,
)
from app.db.database import configure_database, session_scope
from app.db.models import (
    AnalysisSnapshot,
    PaperTrade,
    ScanRun,
    Ticker,
    init_db,
    load_runtime_settings,
)
from app.main import app


def _seed_candles_1d(session, symbol: str, closes: list[float], volume: float, end_dt: datetime) -> None:
    pipeline._ensure_ticker_row(session, symbol, name=symbol, sector="Information Technology")
    n = len(closes)
    start_ts = int((end_dt - timedelta(days=n)).timestamp())
    df = pd.DataFrame(
        {
            "ts": [start_ts + i * 86400 for i in range(n)],
            "open": closes,
            "high": [c * 1.01 for c in closes],
            "low": [c * 0.99 for c in closes],
            "close": closes,
            "volume": [volume] * n,
        }
    )
    pipeline.upsert_candles(session, symbol, "1d", df)


def test_paper_risk_sizing_and_gate_rejections(tmp_path: Path):
    configure_database(tmp_path / "paper_risk.db")
    init_db()
    now_dt = datetime(2026, 11, 24, 16, 0, tzinfo=timezone.utc)

    with session_scope() as session:
        run = ensure_active_paper_run(session, now_utc=now_dt)
        paper_cfg = load_runtime_settings(session)["paper_trading"]

        base_idea = {
            "symbol": "TEST1",
            "sector": "Industrials",
            "direction": "LONG",
            "is_latest_scan": True,
            "snapshot_age_hours": 1.0,
            "conviction_score": 78.0,
            "risk_reward": 2.4,
            "has_conflict": False,
            "days_to_earnings": 30,
            "watchouts": [],
            "live_status": "IN_ZONE",
            "entry_low": 98.0,
            "entry_high": 100.0,
            "stop_loss": 99.0,  # Tight $1 stop -> 1% of $25k = 250 shares, capped by 15% notional ($3,750 / $100 = 37)
            "tp1": 104.0,
            "tp2": 106.0,
            "last_price": 100.0,
            "live_price": 100.0,
        }
        g_cap = evaluate_candidate_gate(session, run, base_idea, paper_cfg, now_utc=now_dt)
        assert g_cap["approved"] is True
        assert g_cap["quantity"] == 37

        wide_idea = {**base_idea, "stop_loss": 90.0}  # $10 stop -> $250 / $10 = 25 shares (< 37 notional cap)
        g_risk = evaluate_candidate_gate(session, run, wide_idea, paper_cfg, now_utc=now_dt)
        assert g_risk["approved"] is True
        assert g_risk["quantity"] == 25

        # Imminent earnings rejection
        g_earn = evaluate_candidate_gate(
            session, run, {**base_idea, "days_to_earnings": 5}, paper_cfg, now_utc=now_dt
        )
        assert g_earn["approved"] is False and "Earnings" in g_earn["reason"]

        # Missed entry rejection
        g_missed = evaluate_candidate_gate(
            session, run, {**base_idea, "live_status": "MISSED_ENTRY"}, paper_cfg, now_utc=now_dt
        )
        assert g_missed["approved"] is False and "MISSED_ENTRY" in g_missed["reason"]

        # Wide spread rejection (> 0.4%)
        g_spread = evaluate_candidate_gate(
            session,
            run,
            base_idea,
            paper_cfg,
            quote={"bid": 99.0, "ask": 101.0, "price": 100.0},
            now_utc=now_dt,
        )
        assert g_spread["approved"] is False and "spread" in g_spread["reason"].lower()

        # Low 20D dollar volume rejection ($500k < $20M)
        _seed_candles_1d(session, "ILLIQ", [10.0] * 15, volume=50_000.0, end_dt=now_dt)
        g_liq = evaluate_candidate_gate(
            session, run, {**base_idea, "symbol": "ILLIQ"}, paper_cfg, now_utc=now_dt
        )
        assert g_liq["approved"] is False and "dollar volume" in g_liq["reason"].lower()

        # Sector cap + duplicate + 5-day cooldown
        for sym in ("SEC1", "SEC2"):
            pipeline._ensure_ticker_row(session, sym, name=sym, sector="Industrials")
            session.add(
                PaperTrade(
                    run_id=run.id,
                    symbol=sym,
                    direction="LONG",
                    status="OPEN",
                    planned_limit_price=100.0,
                    planned_stop_loss=95.0,
                    current_stop_loss=95.0,
                    planned_tp1=110.0,
                    planned_tp2=115.0,
                    quantity_planned=10,
                    quantity_filled=10,
                    quantity_remaining=10,
                    entry_fill_price=100.0,
                    sector_at_entry="Industrials",
                )
            )
        pipeline._ensure_ticker_row(session, "LOST1", name="LOST1", sector="Healthcare")
        session.add(
            PaperTrade(
                run_id=run.id,
                symbol="LOST1",
                direction="LONG",
                status="CLOSED",
                planned_limit_price=100.0,
                planned_stop_loss=95.0,
                current_stop_loss=95.0,
                planned_tp1=110.0,
                planned_tp2=115.0,
                quantity_planned=10,
                quantity_filled=10,
                quantity_remaining=0,
                entry_fill_price=100.0,
                exit_fill_price=95.0,
                realized_pnl_usd=-50.0,
                r_multiple=-1.0,
                closed_at=now_dt - timedelta(days=2),
                sector_at_entry="Healthcare",
            )
        )
        session.commit()

        assert evaluate_candidate_gate(session, run, {**base_idea, "symbol": "SEC1"}, paper_cfg, now_utc=now_dt)["approved"] is False
        g_sec = evaluate_candidate_gate(session, run, {**base_idea, "symbol": "SEC3"}, paper_cfg, now_utc=now_dt)
        assert g_sec["approved"] is False and "Sector cap" in g_sec["reason"]
        g_cd = evaluate_candidate_gate(
            session, run, {**base_idea, "symbol": "LOST1", "sector": "Healthcare"}, paper_cfg, now_utc=now_dt
        )
        assert g_cd["approved"] is False and "cooldown" in g_cd["reason"].lower()


def test_virtual_lifecycle_fill_be_conservative_exit_and_cash_conservation(tmp_path: Path):
    configure_database(tmp_path / "paper_lifecycle.db")
    init_db()
    t0 = datetime(2026, 11, 2, 15, 0, tzinfo=timezone.utc)

    with session_scope() as session:
        run = ensure_active_paper_run(session, now_utc=t0)
        for sym in ("AAPL", "MSFT", "NVDA", "META"):
            pipeline._ensure_ticker_row(session, sym, name=sym, sector="Technology")

        # 1) Pending limit order for AAPL -> fills on bar then moves SL to BE, then stops out conservatively
        run.cash_balance -= 1000.0  # Reserve 10 * 100
        tr_pending = PaperTrade(
            run_id=run.id,
            symbol="AAPL",
            direction="LONG",
            status="PENDING_ENTRY",
            created_at=t0,
            submitted_at=t0,
            planned_entry_low=99.0,
            planned_entry_high=100.0,
            planned_limit_price=100.0,
            planned_stop_loss=95.0,
            current_stop_loss=95.0,
            planned_tp1=110.0,
            planned_tp2=115.0,
            atr_at_entry=2.0,
            quantity_planned=10,
        )
        # 2) Pending order that expires EOD -> must refund $2,000
        run.cash_balance -= 2000.0
        tr_expire = PaperTrade(
            run_id=run.id,
            symbol="MSFT",
            direction="LONG",
            status="PENDING_ENTRY",
            created_at=t0,
            submitted_at=t0,
            planned_entry_low=199.0,
            planned_entry_high=200.0,
            planned_limit_price=200.0,
            planned_stop_loss=190.0,
            current_stop_loss=190.0,
            planned_tp1=220.0,
            planned_tp2=230.0,
            quantity_planned=10,
        )
        # 3) Open NVDA trade for Time Stop (held >= 15 business days below +0.5R)
        run.cash_balance -= 1000.0
        tr_time = PaperTrade(
            run_id=run.id,
            symbol="NVDA",
            direction="LONG",
            status="OPEN",
            created_at=t0,
            submitted_at=t0,
            filled_at=t0,
            planned_entry_low=99.0,
            planned_entry_high=100.0,
            planned_limit_price=100.0,
            planned_stop_loss=95.0,
            current_stop_loss=95.0,
            planned_tp1=110.0,
            planned_tp2=115.0,
            quantity_planned=10,
            quantity_filled=10,
            quantity_remaining=10,
            entry_fill_price=100.0,
            last_mark_price=100.0,
            risk_amount_usd=50.0,
        )
        # 4) Open META trade for Earnings Exit Guard (earnings tomorrow)
        run.cash_balance -= 1000.0
        tr_earn = PaperTrade(
            run_id=run.id,
            symbol="META",
            direction="LONG",
            status="OPEN",
            created_at=t0,
            submitted_at=t0,
            filled_at=t0,
            planned_entry_low=99.0,
            planned_entry_high=100.0,
            planned_limit_price=100.0,
            planned_stop_loss=95.0,
            current_stop_loss=95.0,
            planned_tp1=110.0,
            planned_tp2=115.0,
            quantity_planned=10,
            quantity_filled=10,
            quantity_remaining=10,
            entry_fill_price=100.0,
            last_mark_price=100.0,
            risk_amount_usd=50.0,
        )
        session.add_all([tr_pending, tr_expire, tr_time, tr_earn])
        sr = ScanRun(mode="quick", status="completed", stage="completed")
        session.add(sr)
        session.flush()
        earn_date = (t0 + timedelta(days=25)).date().isoformat()
        session.add(
            AnalysisSnapshot(
                scan_run_id=sr.id,
                symbol="META",
                events_insiders_json=json.dumps({"next_earnings_date": earn_date}),
                is_deep_enriched=True,
            )
        )
        session.commit()

        # Step 1: AAPL fills at 100, then moves SL to BE on +1R bar (high=105.5, close=104)
        bar1_ts = int((t0 + timedelta(hours=1)).timestamp())
        bar2_ts = int((t0 + timedelta(hours=2)).timestamp())
        step_virtual_lifecycle(
            session,
            run=run,
            bars_overrides={
                "AAPL": [
                    {"ts": bar1_ts, "open": 101.0, "high": 102.0, "low": 99.5, "close": 101.0},
                    {"ts": bar2_ts, "open": 101.0, "high": 105.5, "low": 101.0, "close": 104.0},
                ]
            },
            price_overrides={"MSFT": 210.0, "NVDA": 100.0, "META": 102.0},
            now_utc=t0 + timedelta(hours=3),
        )
        session.refresh(tr_pending)
        assert tr_pending.status == "OPEN"
        assert tr_pending.entry_fill_price == 100.0
        assert tr_pending.be_moved is True
        assert tr_pending.current_stop_loss > 100.0

        # Step 2: Single bar touches BOTH BE stop AND TP1 -> conservative SL (BREAK_EVEN) must win!
        # Also advance 24 calendar days (>15 business days, 1 day before META earnings, past AAPL expiry for MSFT)
        t_later = t0 + timedelta(days=24)
        bar3_ts = int((t0 + timedelta(days=1)).timestamp())
        step_virtual_lifecycle(
            session,
            run=run,
            bars_overrides={
                "AAPL": [
                    {"ts": bar3_ts, "open": 104.0, "high": 112.0, "low": 98.0, "close": 111.0},
                ]
            },
            price_overrides={"NVDA": 101.0, "META": 104.0},
            now_utc=t_later,
        )
        for tr in (tr_pending, tr_expire, tr_time, tr_earn):
            session.refresh(tr)

        assert tr_pending.status == "CLOSED" and tr_pending.exit_reason == "BREAK_EVEN"
        assert tr_pending.realized_pnl_usd > 0
        assert tr_expire.status == "CANCELLED" and tr_expire.exit_reason == "ENTRY_EXPIRED"
        assert tr_time.status == "CLOSED" and tr_time.exit_reason == "TIME_STOP"
        assert tr_earn.status == "CLOSED" and tr_earn.exit_reason == "EARNINGS_EXIT"

        # Verify exact cash/equity conservation when all positions are closed
        expected_pnl = tr_pending.realized_pnl_usd + tr_time.realized_pnl_usd + tr_earn.realized_pnl_usd
        assert round(run.cash_balance - run.initial_capital, 2) == round(expected_pnl, 2)
        assert run.current_equity == run.cash_balance


def test_paper_kill_switch_on_drawdown(tmp_path: Path):
    configure_database(tmp_path / "paper_kill.db")
    init_db()

    with session_scope() as session:
        run = ensure_active_paper_run(session)
        paper_cfg = load_runtime_settings(session)["paper_trading"]
        run.high_water_mark = 25000.0
        run.current_equity = 23400.0  # -6.4% <= -6.0% threshold
        session.commit()

        halted, reason = check_kill_switch(session, run, paper_cfg)
        assert halted is True
        assert "drawdown" in reason.lower()
        assert run.kill_switch_triggered is True
        session.commit()

        alloc = run_paper_allocation_cycle(force=True, db_session=session)
        assert alloc["executed"] is False
        assert "drawdown" in alloc["reason"].lower()

        set_paper_kill_switch(session, halt=False)
        assert run.kill_switch_triggered is False


def test_paper_analytics_attribution_csv_and_api(tmp_path: Path):
    configure_database(tmp_path / "paper_api.db")
    init_db()

    with session_scope() as session:
        run = ScanRun(
            mode="quick",
            status="completed",
            stage="completed",
            total_tickers=2,
            processed_tickers=2,
            market_regime_json=json.dumps({"regime": "RISK_ON", "benchmarks": []}),
        )
        session.add(run)
        session.flush()

        for sym, sector, price in [("AAPL", "Information Technology", 200.0), ("JPM", "Financials", 180.0)]:
            pipeline._ensure_ticker_row(session, sym, name=sym, sector=sector)
            session.add(
                AnalysisSnapshot(
                    scan_run_id=run.id,
                    symbol=sym,
                    analyzed_at=datetime.now(timezone.utc),
                    last_price=price,
                    change_pct=1.2,
                    composite_score=84.0,
                    bias="bullish",
                    signals_json=json.dumps(
                        [
                            {
                                "code": "LOWER_CHANNEL_TOUCH",
                                "label": "Lower Channel Touch",
                                "direction": "bullish",
                                "severity": "high",
                                "score_impact": 25.0,
                                "detail": "Touch",
                            },
                            {
                                "code": "INDEX_TREND_ALIGNED",
                                "label": "Index Aligned",
                                "direction": "bullish",
                                "severity": "medium",
                                "score_impact": 15.0,
                                "detail": "Aligned",
                            },
                        ]
                    ),
                    risk_flags_json="[]",
                    trade_plan_json=json.dumps(
                        {
                            "direction": "LONG",
                            "setup_name": "Channel Bounce",
                            "rationale": "Strong uptrend support",
                            "entry_low": price - 2.0,
                            "entry_high": price + 0.5,
                            "stop_loss": price - 6.0,
                            "tp1": price + 12.0,
                            "tp2": price + 18.0,
                            "risk_reward": 2.4,
                            "atr": 3.0,
                            "risk_per_share": 6.0,
                            "index_aligned": True,
                            "index_trend": "bullish",
                        }
                    ),
                    plugin_results_json=json.dumps({"trend_channels": {"score": 88.0, "bias": "bullish"}}),
                    events_insiders_json=json.dumps({"days_to_earnings": 30}),
                    fundamentals_json=json.dumps({"health_grade": "A"}),
                    is_deep_enriched=True,
                )
            )
        session.commit()

    with TestClient(app) as client:
        # 1. Overview initial state
        ov = client.get("/api/paper/overview").json()
        assert ov["run"]["mode"] == "virtual"
        assert ov["run"]["initial_capital"] == 25000.0

        # 2. Evaluate now allocates AAPL & JPM in virtual mode
        ev = client.post("/api/paper/evaluate-now").json()
        assert ev["executed"] is True
        assert len(ev["opened"]) == 2

        trades = client.get("/api/paper/trades").json()["trades"]
        assert len(trades) == 2

        # 3. Close both trades manually via API and inspect attribution & CSV export
        for tr in trades:
            cl = client.post(f"/api/paper/trades/{tr['id']}/close")
            assert cl.status_code == 200
            assert cl.json()["trade"]["status"] == "CLOSED"

        attr = client.get("/api/paper/attribution").json()
        assert attr["closed_trades"] == 2
        assert any(row["key"] == "LOWER_CHANNEL_TOUCH" for row in attr["by_signal"])

        csv_resp = client.get("/api/paper/trades/export.csv")
        assert csv_resp.status_code == 200
        assert "AAPL" in csv_resp.text and "JPM" in csv_resp.text

        # 4. Reset experiment
        reset_resp = client.post("/api/paper/runs/reset", json={"name": "Clean Batch #2", "initial_capital": 50000})
        assert reset_resp.status_code == 200
        assert reset_resp.json()["run"]["initial_capital"] == 50000.0
        assert len(client.get("/api/paper/runs").json()["runs"]) == 2
