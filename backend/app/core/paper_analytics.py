from __future__ import annotations

import csv
import io
import json
import math
from typing import Any

import numpy as np
from sqlalchemy.orm import Session

from app.core.paper_risk import as_utc, ensure_active_paper_run
from app.db.models import (
    PaperAccountRun,
    PaperEquitySnapshot,
    PaperOrderEvent,
    PaperTrade,
    load_runtime_settings,
)


def _resolve_run(session: Session, run_id: int | None) -> PaperAccountRun:
    if run_id is not None:
        run = session.get(PaperAccountRun, run_id)
        if run is not None:
            return run
    return ensure_active_paper_run(session)


def serialize_paper_trade(trade: PaperTrade) -> dict[str, Any]:
    try:
        signals = json.loads(trade.signals_at_entry_json or "[]")
    except json.JSONDecodeError:
        signals = []
    return {
        "id": trade.id,
        "run_id": trade.run_id,
        "symbol": trade.symbol,
        "scan_run_id": trade.scan_run_id,
        "direction": trade.direction,
        "setup_name": trade.setup_name,
        "status": trade.status,
        "created_at": trade.created_at.isoformat() if trade.created_at else None,
        "submitted_at": trade.submitted_at.isoformat() if trade.submitted_at else None,
        "filled_at": trade.filled_at.isoformat() if trade.filled_at else None,
        "closed_at": trade.closed_at.isoformat() if trade.closed_at else None,
        "planned_entry_low": trade.planned_entry_low,
        "planned_entry_high": trade.planned_entry_high,
        "planned_limit_price": trade.planned_limit_price,
        "planned_stop_loss": trade.planned_stop_loss,
        "current_stop_loss": trade.current_stop_loss,
        "planned_tp1": trade.planned_tp1,
        "planned_tp2": trade.planned_tp2,
        "planned_rr": trade.planned_rr,
        "be_moved": bool(trade.be_moved),
        "trailing_active": bool(getattr(trade, "trailing_active", False)),
        "ibkr_parent_order_id": trade.ibkr_parent_order_id,
        "ibkr_stop_order_id": trade.ibkr_stop_order_id,
        "ibkr_tp1_order_id": trade.ibkr_tp1_order_id,
        "quantity_planned": trade.quantity_planned,
        "quantity_filled": trade.quantity_filled,
        "quantity_remaining": trade.quantity_remaining,
        "entry_fill_price": trade.entry_fill_price,
        "exit_fill_price": trade.exit_fill_price,
        "last_mark_price": trade.last_mark_price,
        "entry_slippage_bps": trade.entry_slippage_bps,
        "commission_usd": trade.commission_usd,
        "risk_amount_usd": trade.risk_amount_usd,
        "realized_pnl_usd": trade.realized_pnl_usd,
        "realized_pnl_pct": trade.realized_pnl_pct,
        "unrealized_pnl_usd": trade.unrealized_pnl_usd,
        "r_multiple": trade.r_multiple,
        "exit_reason": trade.exit_reason,
        "conviction_at_entry": trade.conviction_at_entry,
        "composite_score_at_entry": trade.composite_score_at_entry,
        "market_regime_at_entry": trade.market_regime_at_entry,
        "sector_at_entry": trade.sector_at_entry,
        "signals_at_entry": signals,
    }


def serialize_paper_event(ev: PaperOrderEvent) -> dict[str, Any]:
    return {
        "id": ev.id,
        "run_id": ev.run_id,
        "trade_id": ev.trade_id,
        "symbol": ev.symbol,
        "event_type": ev.event_type,
        "message": ev.message,
        "created_at": ev.created_at.isoformat() if ev.created_at else None,
    }


def build_equity_curve(session: Session, run_id: int | None = None) -> list[dict[str, Any]]:
    run = _resolve_run(session, run_id)
    snaps = (
        session.query(PaperEquitySnapshot)
        .filter(PaperEquitySnapshot.run_id == run.id)
        .order_by(PaperEquitySnapshot.snapshot_date.asc())
        .all()
    )
    first_spy = next((float(s.spy_close) for s in snaps if s.spy_close and s.spy_close > 0), None)
    curve: list[dict[str, Any]] = []
    for s in snaps:
        spy_eq = (
            round(float(run.initial_capital) * (float(s.spy_close) / first_spy), 2)
            if first_spy and s.spy_close
            else None
        )
        curve.append(
            {
                "date": s.snapshot_date,
                "equity": round(float(s.equity), 2),
                "cash": round(float(s.cash), 2),
                "drawdown_pct": round(float(s.drawdown_pct), 2),
                "open_positions_count": int(s.open_positions_count),
                "realized_pnl_cum": round(float(s.realized_pnl_cum), 2),
                "unrealized_pnl": round(float(s.unrealized_pnl), 2),
                "spy_equity": spy_eq,
            }
        )
    return curve


def build_paper_overview(session: Session, run_id: int | None = None) -> dict[str, Any]:
    run = _resolve_run(session, run_id)
    runtime = load_runtime_settings(session)
    paper_cfg = runtime.get("paper_trading", {})

    trades = session.query(PaperTrade).filter(PaperTrade.run_id == run.id).all()
    closed = [t for t in trades if t.status == "CLOSED"]
    open_trades = [t for t in trades if t.status in ("OPEN", "PARTIAL_TP1")]
    pending_trades = [t for t in trades if t.status == "PENDING_ENTRY"]
    cancelled_expired = [t for t in trades if t.status == "CANCELLED" and t.exit_reason == "ENTRY_EXPIRED"]

    wins = [t for t in closed if float(t.realized_pnl_usd) > 0]
    losses = [t for t in closed if float(t.realized_pnl_usd) < 0]
    closed_cnt = len(closed)

    win_rate = round((len(wins) / closed_cnt) * 100.0, 1) if closed_cnt > 0 else 0.0
    sum_wins_usd = sum(float(t.realized_pnl_usd) for t in wins)
    sum_losses_usd = abs(sum(float(t.realized_pnl_usd) for t in losses))
    profit_factor = round(sum_wins_usd / sum_losses_usd, 2) if sum_losses_usd > 0 else (round(sum_wins_usd, 2) if sum_wins_usd > 0 else None)

    avg_win_usd = round(sum_wins_usd / len(wins), 2) if wins else 0.0
    avg_loss_usd = round(-sum_losses_usd / len(losses), 2) if losses else 0.0
    avg_win_r = round(sum(float(t.r_multiple) for t in wins) / len(wins), 2) if wins else 0.0
    avg_loss_r = round(sum(float(t.r_multiple) for t in losses) / len(losses), 2) if losses else 0.0
    expectancy_r = round(sum(float(t.r_multiple) for t in closed) / closed_cnt, 2) if closed_cnt > 0 else 0.0
    expectancy_usd = round(sum(float(t.realized_pnl_usd) for t in closed) / closed_cnt, 2) if closed_cnt > 0 else 0.0

    largest_win_usd = round(max((float(t.realized_pnl_usd) for t in wins), default=0.0), 2)
    largest_loss_usd = round(min((float(t.realized_pnl_usd) for t in losses), default=0.0), 2)

    hold_days_list: list[float] = []
    for t in closed:
        f_dt = as_utc(t.filled_at)
        c_dt = as_utc(t.closed_at)
        if f_dt is not None and c_dt is not None:
            hold_days_list.append(max(0.0, (c_dt - f_dt).total_seconds() / 86400.0))
    avg_hold_days = round(sum(hold_days_list) / len(hold_days_list), 1) if hold_days_list else 0.0

    slippages = [float(t.entry_slippage_bps) for t in trades if t.entry_slippage_bps is not None]
    avg_slippage_bps = round(sum(slippages) / len(slippages), 2) if slippages else None

    filled_ever = len([t for t in trades if int(t.quantity_filled or 0) > 0])
    attempted_fills = filled_ever + len(cancelled_expired)
    fill_rate_pct = round((filled_ever / attempted_fills) * 100.0, 1) if attempted_fills > 0 else None

    curve = build_equity_curve(session, run.id)
    max_dd_pct = round(min((p["drawdown_pct"] for p in curve), default=0.0), 2)
    curr_dd_pct = (
        round(((float(run.current_equity) - float(run.high_water_mark)) / float(run.high_water_mark)) * 100.0, 2)
        if run.high_water_mark > 0
        else 0.0
    )

    sharpe_ratio: float | None = None
    if len(curve) >= 3:
        eqs = np.array([p["equity"] for p in curve], dtype=float)
        rets = np.diff(eqs) / np.where(eqs[:-1] > 0, eqs[:-1], 1.0)
        std = float(np.std(rets, ddof=1))
        if std > 1e-9:
            sharpe_ratio = round((float(np.mean(rets)) / std) * math.sqrt(252.0), 2)

    spy_return_pct: float | None = None
    alpha_vs_spy_pct: float | None = None
    total_return_usd = round(float(run.current_equity) - float(run.initial_capital), 2)
    total_return_pct = (
        round((total_return_usd / float(run.initial_capital)) * 100.0, 2) if run.initial_capital > 0 else 0.0
    )
    spy_vals = [p["spy_equity"] for p in curve if p.get("spy_equity")]
    if len(spy_vals) >= 2 and spy_vals[0]:
        spy_return_pct = round(((float(spy_vals[-1]) - float(spy_vals[0])) / float(spy_vals[0])) * 100.0, 2)
        alpha_vs_spy_pct = round(total_return_pct - spy_return_pct, 2)

    return {
        "run": {
            "id": run.id,
            "name": run.name,
            "mode": run.mode,
            "ibkr_account": run.ibkr_account,
            "initial_capital": round(float(run.initial_capital), 2),
            "current_equity": round(float(run.current_equity), 2),
            "cash_balance": round(float(run.cash_balance), 2),
            "high_water_mark": round(float(run.high_water_mark), 2),
            "is_active": bool(run.is_active),
            "kill_switch_triggered": bool(run.kill_switch_triggered),
            "kill_switch_reason": run.kill_switch_reason,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "ended_at": run.ended_at.isoformat() if run.ended_at else None,
        },
        "paper_settings": paper_cfg,
        "kpis": {
            "total_return_usd": total_return_usd,
            "total_return_pct": total_return_pct,
            "spy_return_pct": spy_return_pct,
            "alpha_vs_spy_pct": alpha_vs_spy_pct,
            "realized_pnl_usd": round(sum(float(t.realized_pnl_usd) for t in closed), 2),
            "unrealized_pnl_usd": round(sum(float(t.unrealized_pnl_usd) for t in open_trades), 2),
            "open_count": len(open_trades),
            "pending_count": len(pending_trades),
            "closed_count": closed_cnt,
            "wins_count": len(wins),
            "losses_count": len(losses),
            "win_rate_pct": win_rate,
            "profit_factor": profit_factor,
            "expectancy_r": expectancy_r,
            "expectancy_usd": expectancy_usd,
            "avg_win_usd": avg_win_usd,
            "avg_loss_usd": avg_loss_usd,
            "avg_win_r": avg_win_r,
            "avg_loss_r": avg_loss_r,
            "largest_win_usd": largest_win_usd,
            "largest_loss_usd": largest_loss_usd,
            "max_drawdown_pct": max_dd_pct,
            "current_drawdown_pct": curr_dd_pct,
            "sharpe_ratio": sharpe_ratio,
            "avg_hold_days": avg_hold_days,
            "avg_slippage_bps": avg_slippage_bps,
            "fill_rate_pct": fill_rate_pct,
        },
    }


def _aggregate_bucket(rows: list[PaperTrade]) -> dict[str, Any]:
    cnt = len(rows)
    wins = sum(1 for t in rows if float(t.realized_pnl_usd) > 0)
    total_pnl = round(sum(float(t.realized_pnl_usd) for t in rows), 2)
    avg_r = round(sum(float(t.r_multiple) for t in rows) / cnt, 2) if cnt > 0 else 0.0
    sum_pos = sum(float(t.realized_pnl_usd) for t in rows if float(t.realized_pnl_usd) > 0)
    sum_neg = abs(sum(float(t.realized_pnl_usd) for t in rows if float(t.realized_pnl_usd) < 0))
    pf = round(sum_pos / sum_neg, 2) if sum_neg > 0 else (round(sum_pos, 2) if sum_pos > 0 else None)
    return {
        "trades": cnt,
        "wins": wins,
        "win_rate_pct": round((wins / cnt) * 100.0, 1) if cnt > 0 else 0.0,
        "avg_r": avg_r,
        "total_pnl_usd": total_pnl,
        "profit_factor": pf,
    }


def build_signal_attribution(session: Session, run_id: int | None = None) -> dict[str, Any]:
    run = _resolve_run(session, run_id)
    closed = (
        session.query(PaperTrade)
        .filter(PaperTrade.run_id == run.id, PaperTrade.status == "CLOSED")
        .all()
    )

    sig_buckets: dict[str, list[PaperTrade]] = {}
    conv_buckets: dict[str, list[PaperTrade]] = {"60-69": [], "70-79": [], "80+": []}
    dir_buckets: dict[str, list[PaperTrade]] = {}
    regime_buckets: dict[str, list[PaperTrade]] = {}
    sector_buckets: dict[str, list[PaperTrade]] = {}
    exit_buckets: dict[str, list[PaperTrade]] = {}

    for t in closed:
        try:
            sigs = json.loads(t.signals_at_entry_json or "[]")
        except json.JSONDecodeError:
            sigs = []
        for s in sigs:
            sig_buckets.setdefault(str(s), []).append(t)

        c = float(t.conviction_at_entry or 0.0)
        band = "80+" if c >= 80.0 else ("70-79" if c >= 70.0 else "60-69")
        conv_buckets[band].append(t)

        dir_buckets.setdefault(t.direction, []).append(t)
        regime_buckets.setdefault(t.market_regime_at_entry or "NEUTRAL", []).append(t)
        sector_buckets.setdefault(t.sector_at_entry or "Unknown", []).append(t)
        exit_buckets.setdefault(t.exit_reason or "OTHER", []).append(t)

    def _to_list(m: dict[str, list[PaperTrade]], sort_by_trades: bool = True) -> list[dict[str, Any]]:
        out = [{"key": k, **_aggregate_bucket(v)} for k, v in m.items() if v]
        if sort_by_trades:
            out.sort(key=lambda x: (x["trades"], x["avg_r"]), reverse=True)
        return out

    return {
        "run_id": run.id,
        "closed_trades": len(closed),
        "by_signal": _to_list(sig_buckets),
        "by_conviction_band": _to_list(conv_buckets, sort_by_trades=False),
        "by_direction": _to_list(dir_buckets),
        "by_regime": _to_list(regime_buckets),
        "by_sector": _to_list(sector_buckets),
        "by_exit_reason": _to_list(exit_buckets),
    }


def export_trades_csv(session: Session, run_id: int | None = None) -> str:
    run = _resolve_run(session, run_id)
    trades = (
        session.query(PaperTrade)
        .filter(PaperTrade.run_id == run.id)
        .order_by(PaperTrade.id.asc())
        .all()
    )
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "id",
            "symbol",
            "direction",
            "status",
            "setup_name",
            "qty_planned",
            "qty_filled",
            "limit_price",
            "entry_fill_price",
            "exit_fill_price",
            "stop_loss",
            "tp1",
            "tp2",
            "planned_rr",
            "entry_slippage_bps",
            "commission_usd",
            "risk_amount_usd",
            "realized_pnl_usd",
            "realized_pnl_pct",
            "r_multiple",
            "exit_reason",
            "conviction_at_entry",
            "market_regime",
            "sector",
            "filled_at",
            "closed_at",
        ]
    )
    for t in trades:
        writer.writerow(
            [
                t.id,
                t.symbol,
                t.direction,
                t.status,
                t.setup_name,
                t.quantity_planned,
                t.quantity_filled,
                t.planned_limit_price,
                t.entry_fill_price,
                t.exit_fill_price,
                t.current_stop_loss,
                t.planned_tp1,
                t.planned_tp2,
                t.planned_rr,
                t.entry_slippage_bps,
                t.commission_usd,
                t.risk_amount_usd,
                t.realized_pnl_usd,
                t.realized_pnl_pct,
                t.r_multiple,
                t.exit_reason,
                t.conviction_at_entry,
                t.market_regime_at_entry,
                t.sector_at_entry,
                t.filled_at.isoformat() if t.filled_at else "",
                t.closed_at.isoformat() if t.closed_at else "",
            ]
        )
    return buf.getvalue()
