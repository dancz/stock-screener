from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.core import pipeline, telegram_bot
from app.core.paper_analytics import build_paper_overview, build_signal_attribution, export_trades_csv
from app.core.paper_ibkr import LiveAccountBlockedError, assert_paper_account, reconcile_ibkr_state
from app.core.paper_risk import check_kill_switch, ensure_active_paper_run, evaluate_candidate_gate
from app.core.paper_virtual import (
    run_paper_allocation_cycle,
    set_paper_kill_switch,
    step_virtual_lifecycle,
)
from app.db.database import configure_database, session_scope
from app.db.models import (
    AnalysisSnapshot,
    Candle,
    PaperTrade,
    ScanRun,
    Ticker,
    init_db,
    load_runtime_settings,
    save_runtime_settings,
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


def test_virtual_be_does_not_retroactively_stop_on_earlier_bar_and_trails(tmp_path: Path):
    configure_database(tmp_path / "paper_be_trail.db")
    init_db()
    t0 = datetime(2026, 11, 24, 15, 0, tzinfo=timezone.utc)

    with session_scope() as session:
        run = ensure_active_paper_run(session, now_utc=t0)
        pipeline._ensure_ticker_row(session, "LMT", name="Lockheed Martin", sector="Industrials")
        # Entry 585.88, SL 569.69 (risk/sh = 16.19), TP1 635.00, ATR 8.0
        tr = PaperTrade(
            run_id=run.id,
            symbol="LMT",
            direction="LONG",
            status="OPEN",
            created_at=t0,
            submitted_at=t0,
            filled_at=t0,
            planned_entry_low=582.0,
            planned_entry_high=586.0,
            planned_limit_price=585.88,
            planned_stop_loss=569.69,
            current_stop_loss=569.69,
            planned_tp1=635.0,
            planned_tp2=650.0,
            atr_at_entry=8.0,
            quantity_planned=6,
            quantity_filled=6,
            quantity_remaining=6,
            entry_fill_price=585.88,
            last_mark_price=585.88,
            risk_amount_usd=round((585.88 - 569.69) * 6, 2),
        )
        run.cash_balance -= round(585.88 * 6, 2)
        session.add(tr)

        # Seed early dip bar (t0+1h, low=582.92 > initial SL) and later BE-trigger bar (t0+4h, high=603.0 > +1R)
        bar1_ts = int((t0 + timedelta(hours=1)).timestamp())
        bar2_ts = int((t0 + timedelta(hours=4)).timestamp())
        session.add_all(
            [
                Candle(symbol="LMT", timeframe="1h", ts=bar1_ts, open=586.0, high=587.0, low=582.92, close=585.0, volume=1e6),
                Candle(symbol="LMT", timeframe="1h", ts=bar2_ts, open=586.0, high=603.0, low=585.5, close=601.0, volume=1e6),
            ]
        )
        session.commit()

        # Tick 1 evaluates bar1 & bar2 -> moves SL to BE (> 586), sets last_evaluated_bar_ts = bar2_ts
        step_virtual_lifecycle(session, run=run, price_overrides={"LMT": 601.0}, now_utc=t0 + timedelta(hours=4, minutes=5))
        session.refresh(tr)
        assert tr.status == "OPEN"
        assert tr.be_moved is True
        assert tr.current_stop_loss > 586.0
        assert tr.last_evaluated_bar_ts == bar2_ts

        # Tick 2 right after must NOT replay bar1 (low 582.92) against tightened BE stop!
        step_virtual_lifecycle(session, run=run, price_overrides={"LMT": 601.0}, now_utc=t0 + timedelta(hours=4, minutes=10))
        session.refresh(tr)
        assert tr.status == "OPEN"

        # Bar 3 pushes high to 616.0 (+1.86R >= 1.5R activation) -> Trailing Stop rises to 616 - 2*8 = 600.0
        bar3_ts = int((t0 + timedelta(hours=5)).timestamp())
        session.add(
            Candle(symbol="LMT", timeframe="1h", ts=bar3_ts, open=601.0, high=616.0, low=600.5, close=612.0, volume=1e6)
        )
        session.commit()
        step_virtual_lifecycle(session, run=run, price_overrides={"LMT": 612.0}, now_utc=t0 + timedelta(hours=5, minutes=5))
        session.refresh(tr)
        assert tr.status == "OPEN"
        assert tr.trailing_active is True
        assert tr.current_stop_loss == 600.0

        # Bar 4 pulls back through trailing stop 600.0 -> exits as TRAILING_STOP with positive profit
        bar4_ts = int((t0 + timedelta(hours=6)).timestamp())
        session.add(
            Candle(symbol="LMT", timeframe="1h", ts=bar4_ts, open=611.0, high=612.0, low=595.0, close=597.0, volume=1e6)
        )
        session.commit()
        step_virtual_lifecycle(session, run=run, price_overrides={"LMT": 597.0}, now_utc=t0 + timedelta(hours=6, minutes=5))
        session.refresh(tr)
        assert tr.status == "CLOSED"
        assert tr.exit_reason == "TRAILING_STOP"
        assert tr.realized_pnl_usd > 0


def test_ibkr_paper_safety_lock_blocks_live_ports_and_accounts():
    with pytest.raises(LiveAccountBlockedError):
        assert_paper_account(["U1234567"], 4002)
    with pytest.raises(LiveAccountBlockedError):
        assert_paper_account(["F987654"], 4002)
    with pytest.raises(LiveAccountBlockedError):
        assert_paper_account([], 4002)
    with pytest.raises(LiveAccountBlockedError):
        assert_paper_account(["DUK527861"], 4001)
    with pytest.raises(LiveAccountBlockedError):
        assert_paper_account(["DUK527861"], 7496)
    with pytest.raises(LiveAccountBlockedError):
        assert_paper_account(["DUK527861", "U9999999"], 4002)

    assert assert_paper_account(["DUK527861"], 4002) == "DUK527861"
    assert assert_paper_account(["DUK527861"], 7497) == "DUK527861"


def test_ibkr_paper_reconcile_and_mode_isolation(tmp_path: Path):
    configure_database(tmp_path / "paper_ibkr_rec.db")
    init_db()
    t0 = datetime(2026, 11, 25, 16, 0, tzinfo=timezone.utc)

    with session_scope() as session:
        rt = load_runtime_settings(session)
        v_run = ensure_active_paper_run(session, rt, now_utc=t0)
        assert v_run.mode == "virtual"
        pipeline._ensure_ticker_row(session, "AAPL", name="Apple")
        session.add(
            PaperTrade(
                run_id=v_run.id,
                symbol="AAPL",
                direction="LONG",
                status="CLOSED",
                planned_limit_price=200.0,
                quantity_planned=5,
                realized_pnl_usd=25.0,
            )
        )
        session.commit()

        # Switching mode while virtual run has trades archives virtual run and creates isolated ibkr_paper run
        rt["paper_trading"]["mode"] = "ibkr_paper"
        save_runtime_settings(session, rt)
        ib_run = ensure_active_paper_run(session, rt, now_utc=t0)
        session.refresh(v_run)
        assert v_run.is_active is False
        assert ib_run.id != v_run.id
        assert ib_run.mode == "ibkr_paper"

        # Seed PENDING_ENTRY linked to IBKR order IDs
        ib_run.cash_balance -= 2000.0
        tr = PaperTrade(
            run_id=ib_run.id,
            symbol="AAPL",
            direction="LONG",
            status="PENDING_ENTRY",
            created_at=t0,
            submitted_at=t0,
            planned_entry_low=199.0,
            planned_entry_high=201.0,
            planned_limit_price=200.0,
            planned_stop_loss=190.0,
            current_stop_loss=190.0,
            planned_tp1=220.0,
            planned_tp2=230.0,
            quantity_planned=10,
            ibkr_parent_order_id=101,
            ibkr_stop_order_id=102,
            ibkr_tp1_order_id=103,
            risk_amount_usd=100.0,
        )
        session.add(tr)
        session.commit()

        # Reconcile entry fill + $1.00 commission + protective SL verified open
        snap_fill = {
            "account": "DUK527861",
            "open_orders": [
                {"order_id": 102, "symbol": "AAPL", "action": "SELL", "order_type": "STP", "aux_price": 190.0},
                {"order_id": 103, "symbol": "AAPL", "action": "SELL", "order_type": "LMT", "lmt_price": 220.0},
            ],
            "positions": {"AAPL": {"quantity": 10.0, "avg_cost": 199.5}},
            "executions": [
                {"order_id": 101, "symbol": "AAPL", "side": "BOT", "shares": 10.0, "price": 199.5, "commission": 1.0}
            ],
        }
        rec1 = reconcile_ibkr_state(session, run=ib_run, runtime_settings=rt, now_utc=t0 + timedelta(minutes=5), snapshot_override=snap_fill)
        session.refresh(tr)
        session.refresh(ib_run)
        assert rec1["filled"] == 1
        assert ib_run.ibkr_account == "DUK527861"
        assert tr.status == "OPEN"
        assert tr.entry_fill_price == 199.5
        assert tr.commission_usd == 1.0

        # Reconcile exit via stop-loss execution
        snap_exit = {
            "account": "DUK527861",
            "open_orders": [],
            "positions": {},
            "executions": [
                {"order_id": 102, "symbol": "AAPL", "side": "SLD", "shares": 10.0, "price": 190.0, "commission": 1.0}
            ],
        }
        rec2 = reconcile_ibkr_state(session, run=ib_run, runtime_settings=rt, now_utc=t0 + timedelta(minutes=30), snapshot_override=snap_exit)
        session.refresh(tr)
        assert rec2["closed"] == 1
        assert tr.status == "CLOSED"
        assert tr.exit_reason == "STOP_LOSS"
        assert tr.exit_fill_price == 190.0
        assert tr.commission_usd == 2.0
        assert tr.realized_pnl_usd == -97.0


def test_telegram_detect_chat_and_authorized_commands(tmp_path: Path, monkeypatch):
    configure_database(tmp_path / "paper_tg.db")
    init_db()

    sent_messages: list[dict] = []
    poll_responses: list[dict] = [
        {"ok": True, "result": [{"update_id": 10, "message": {"chat": {"id": 999001, "type": "private", "username": "trader"}, "text": "/start"}}]},
        {
            "ok": True,
            "result": [
                {"update_id": 11, "message": {"chat": {"id": 888888, "type": "private", "username": "stranger"}, "text": "/halt"}},
                {"update_id": 12, "message": {"chat": {"id": 999001, "type": "private", "username": "trader"}, "text": "/status"}},
                {"update_id": 13, "message": {"chat": {"id": 999001, "type": "private", "username": "trader"}, "text": "/halt"}},
            ],
        },
    ]

    class _FakeResp:
        def __init__(self, status_code: int, payload: dict):
            self.status_code = status_code
            self._payload = payload
            self.text = json.dumps(payload)

        def json(self):
            return self._payload

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, url: str, params=None):
            if url.endswith("/getMe"):
                return _FakeResp(200, {"ok": True, "result": {"username": "swingpulse_test_bot", "first_name": "SwingPulse"}})
            if url.endswith("/getUpdates"):
                if params and params.get("offset") is not None and len(poll_responses) > 1:
                    return _FakeResp(200, poll_responses.pop(0))
                return _FakeResp(200, poll_responses[0])
            return _FakeResp(404, {})

        def post(self, url: str, json=None):
            sent_messages.append({"url": url, "json": json})
            return _FakeResp(200, {"ok": True, "result": {"message_id": len(sent_messages)}})

    monkeypatch.setattr(telegram_bot.httpx, "Client", _FakeClient)
    telegram_bot._last_update_id = None
    telegram_bot._active_token = ""

    det = telegram_bot.detect_telegram_chats("123:testtok")
    assert det["ok"] is True
    assert det["bot_username"] == "swingpulse_test_bot"
    assert det["suggested_chat_id"] == "999001"

    with session_scope() as session:
        rt = load_runtime_settings(session)
        rt["notifiers"].update(
            {
                "telegram_enabled": True,
                "telegram_bot_token": "123:testtok",
                "telegram_chat_id": "999001",
                "telegram_commands_enabled": True,
            }
        )
        save_runtime_settings(session, rt)

    # First poll only primes offset past historical /start update_id=10
    p1 = telegram_bot.poll_telegram_commands_once()
    assert p1["primed"] is True and p1["processed"] == 0

    # Consume initial poll payload and poll authorized/unauthorized batch
    poll_responses.pop(0)
    p2 = telegram_bot.poll_telegram_commands_once()
    assert p2["processed"] == 2
    # 2 command replies (/status + /halt) + 1 Kill-Switch broadcast alert via NotifierPlugin
    assert len(sent_messages) == 3
    assert all(str(m["json"]["chat_id"]) == "999001" for m in sent_messages)
    texts = [m["json"]["text"] for m in sent_messages]
    assert any("Experiment #1" in t for t in texts)
    assert any("Emergency Kill-Switch Activated" in t for t in texts)
    assert any("Kill-Switch Activated" in t for t in texts)

    with session_scope() as session:
        run = ensure_active_paper_run(session)
        assert run.kill_switch_triggered is True
