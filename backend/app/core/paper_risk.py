from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import (
    Candle,
    PaperAccountRun,
    PaperEquitySnapshot,
    PaperOrderEvent,
    PaperTrade,
    load_runtime_settings,
    utcnow,
)

BLOCKING_WATCHOUT_PREFIXES = (
    "imminent earnings",
    "not aligned with",
    "weak fundamental grade (d)",
    "extreme 1d move",
    "older scan",
)


def as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def ensure_active_paper_run(
    session: Session,
    runtime_settings: dict[str, Any] | None = None,
    now_utc: datetime | None = None,
) -> PaperAccountRun:
    """Return active PaperAccountRun matching the configured execution mode, rotating cleanly on mode switch."""
    runtime = runtime_settings or load_runtime_settings(session)
    desired_mode = str(runtime.get("paper_trading", {}).get("mode", "virtual") or "virtual")
    cur_dt = as_utc(now_utc) or utcnow()

    run = (
        session.query(PaperAccountRun)
        .filter(PaperAccountRun.is_active.is_(True))
        .order_by(PaperAccountRun.id.desc())
        .first()
    )
    if run is not None:
        if run.mode == desired_mode or runtime_settings is None:
            return run
        trade_cnt = session.query(PaperTrade).filter(PaperTrade.run_id == run.id).count()
        if trade_cnt == 0:
            run.mode = desired_mode
            if run.name.startswith("Experiment #"):
                run.name = f"Experiment #{run.id} ({'IBKR Paper' if desired_mode == 'ibkr_paper' else 'Virtual'})"
            run.config_snapshot_json = json.dumps(runtime.get("paper_trading", {}))
            session.flush()
            return run
        # Isolate runs by execution mode so virtual vs IBKR Paper results never mix
        run.is_active = False
        run.ended_at = cur_dt

    eq = float(runtime.get("account", {}).get("equity", 25000.0) or 25000.0)
    count = session.query(PaperAccountRun).count()
    run = PaperAccountRun(
        name=f"Experiment #{count + 1} ({'IBKR Paper' if desired_mode == 'ibkr_paper' else 'Virtual'})",
        mode=desired_mode,
        initial_capital=eq,
        current_equity=eq,
        cash_balance=eq,
        high_water_mark=eq,
        is_active=True,
        started_at=cur_dt,
        config_snapshot_json=json.dumps(runtime.get("paper_trading", {})),
    )
    session.add(run)
    session.flush()
    session.add(
        PaperEquitySnapshot(
            run_id=run.id,
            snapshot_date=cur_dt.date().isoformat(),
            equity=eq,
            cash=eq,
            open_positions_count=0,
            realized_pnl_cum=0.0,
            unrealized_pnl=0.0,
            drawdown_pct=0.0,
        )
    )
    session.flush()
    return run


def compute_avg_dollar_volume_20d(session: Session, symbol: str) -> float | None:
    """Return 20-day mean dollar volume from cached 1d bars, or None if fewer than 5 bars exist."""
    rows = (
        session.query(Candle.close, Candle.volume)
        .filter(Candle.symbol == symbol.upper(), Candle.timeframe == "1d")
        .order_by(Candle.ts.desc())
        .limit(20)
        .all()
    )
    if len(rows) < 5:
        return None
    return float(sum(float(c) * float(v) for c, v in rows) / len(rows))


def check_kill_switch(
    session: Session,
    run: PaperAccountRun,
    paper_cfg: dict[str, Any],
    now_utc: datetime | None = None,
) -> tuple[bool, str]:
    """Evaluate permanent max-drawdown kill-switch and temporary daily loss limit."""
    if run.kill_switch_triggered:
        return True, run.kill_switch_reason or "Kill-switch is active."

    cur_dt = as_utc(now_utc) or utcnow()
    max_dd_limit = -abs(float(paper_cfg.get("max_drawdown_pct", -6.0)))
    if run.high_water_mark > 0:
        dd_pct = ((run.current_equity - run.high_water_mark) / run.high_water_mark) * 100.0
        if dd_pct <= max_dd_limit:
            reason = f"Max drawdown limit breached ({dd_pct:.2f}% <= {max_dd_limit:.2f}%)"
            run.kill_switch_triggered = True
            run.kill_switch_reason = reason
            session.add(
                PaperOrderEvent(
                    run_id=run.id,
                    trade_id=None,
                    symbol="",
                    event_type="KILL_SWITCH",
                    message=reason,
                    payload_json=json.dumps({"dd_pct": round(dd_pct, 2), "limit": max_dd_limit}),
                    created_at=cur_dt,
                )
            )
            return True, reason

    daily_limit = -abs(float(paper_cfg.get("daily_loss_limit_pct", -2.0)))
    cur_date = cur_dt.date().isoformat()
    prev_snap = (
        session.query(PaperEquitySnapshot)
        .filter(PaperEquitySnapshot.run_id == run.id, PaperEquitySnapshot.snapshot_date < cur_date)
        .order_by(PaperEquitySnapshot.snapshot_date.desc())
        .first()
    )
    base_equity = float(prev_snap.equity) if prev_snap is not None else float(run.initial_capital)
    if base_equity > 0:
        day_pct = ((run.current_equity - base_equity) / base_equity) * 100.0
        if day_pct <= daily_limit:
            return True, f"Daily loss limit reached ({day_pct:.2f}% <= {daily_limit:.2f}%)"

    return False, ""


def evaluate_candidate_gate(
    session: Session,
    run: PaperAccountRun,
    idea: dict[str, Any],
    paper_cfg: dict[str, Any],
    *,
    market_regime: str = "NEUTRAL",
    quote: dict[str, Any] | None = None,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Validate a single trade idea against risk gates and compute position sizing."""
    cur_dt = as_utc(now_utc) or utcnow()
    sym = str(idea.get("symbol", "")).strip().upper()
    direction = str(idea.get("direction", "LONG")).upper()

    if not sym or direction not in ("LONG", "SHORT"):
        return {"approved": False, "reason": "Missing symbol or non-directional trade plan"}

    if not idea.get("is_latest_scan", True):
        return {"approved": False, "reason": "Snapshot is from an older scan"}

    age_h = idea.get("snapshot_age_hours")
    if age_h is not None and float(age_h) > 16.0:
        return {"approved": False, "reason": f"Snapshot too old ({age_h}h > 16h)"}

    if direction == "SHORT" and not bool(paper_cfg.get("allow_short", False)):
        return {"approved": False, "reason": "Short trades are disabled in paper_trading settings"}

    base_min_conv = float(paper_cfg.get("min_conviction", 68.0))
    eff_min_conv = max(base_min_conv, 75.0) if (market_regime == "RISK_OFF" and direction == "LONG") else base_min_conv
    conv = float(idea.get("conviction_score") or 0.0)
    if conv < eff_min_conv:
        return {"approved": False, "reason": f"Conviction {conv:.1f} below threshold {eff_min_conv:.1f}"}

    min_rr = float(paper_cfg.get("min_rr", 1.8))
    rr = float(idea.get("risk_reward") or 0.0)
    if rr < min_rr:
        return {"approved": False, "reason": f"R:R {rr:.2f}x below minimum {min_rr:.2f}x"}

    if bool(idea.get("has_conflict", False)):
        return {"approved": False, "reason": "Conflicting technical/earnings signals"}

    min_dte = int(paper_cfg.get("min_days_to_earnings", 7))
    dte = idea.get("days_to_earnings")
    if dte is not None and 0 <= int(dte) <= min_dte:
        return {"approved": False, "reason": f"Earnings in {int(dte)}d (<= {min_dte}d guard)"}

    watchouts = [str(w).lower() for w in (idea.get("watchouts") or [])]
    for w in watchouts:
        if any(prefix in w for prefix in BLOCKING_WATCHOUT_PREFIXES):
            return {"approved": False, "reason": f"Blocked by watchout: {w}"}
        if direction == "SHORT" and "elevated short interest" in w:
            return {"approved": False, "reason": f"Short squeeze guard: {w}"}

    live_status = idea.get("live_status")
    if live_status in ("MISSED_ENTRY", "TARGET_HIT", "STOP_HIT"):
        return {"approved": False, "reason": f"Live status is {live_status}"}

    max_spread_pct = float(paper_cfg.get("max_spread_pct", 0.4))
    if quote is not None:
        bid = quote.get("bid")
        ask = quote.get("ask")
        if bid and ask and float(bid) > 0 and float(ask) >= float(bid):
            mid = (float(bid) + float(ask)) / 2.0
            spread_pct = ((float(ask) - float(bid)) / mid) * 100.0
            if spread_pct > max_spread_pct:
                return {"approved": False, "reason": f"Bid/ask spread too wide ({spread_pct:.2f}% > {max_spread_pct:.2f}%)"}

    entry_low = float(idea.get("entry_low") or 0.0)
    entry_high = float(idea.get("entry_high") or 0.0)
    stop_loss = float(idea.get("stop_loss") or 0.0)
    tp1 = float(idea.get("tp1") or 0.0)
    tp2 = float(idea.get("tp2") or 0.0)
    ref_price = float(idea.get("live_price") or (quote or {}).get("price") or idea.get("last_price") or 0.0)

    if ref_price <= 0 or entry_low <= 0 or entry_high <= 0 or stop_loss <= 0 or tp1 <= 0:
        return {"approved": False, "reason": "Incomplete entry/stop/target geometry"}

    if direction == "LONG":
        if ref_price <= stop_loss:
            return {"approved": False, "reason": f"Price ${ref_price:.2f} already below stop ${stop_loss:.2f}"}
        if ref_price > entry_high * 1.02:
            return {"approved": False, "reason": f"Price ${ref_price:.2f} chased >2% above entry high ${entry_high:.2f}"}
        limit_price = round(min(ref_price, entry_high), 2)
        if limit_price <= stop_loss:
            return {"approved": False, "reason": "Limit price must be above stop loss"}
    else:
        if ref_price >= stop_loss:
            return {"approved": False, "reason": f"Price ${ref_price:.2f} already above stop ${stop_loss:.2f}"}
        if ref_price < entry_low * 0.98:
            return {"approved": False, "reason": f"Price ${ref_price:.2f} >2% below short entry low ${entry_low:.2f}"}
        limit_price = round(max(ref_price, entry_low), 2)
        if limit_price >= stop_loss:
            return {"approved": False, "reason": "Short limit price must be below stop loss"}

    # Portfolio constraints
    active_trades = (
        session.query(PaperTrade)
        .filter(PaperTrade.run_id == run.id, PaperTrade.status.in_(("PENDING_ENTRY", "OPEN", "PARTIAL_TP1")))
        .all()
    )
    if any(t.symbol == sym for t in active_trades):
        return {"approved": False, "reason": f"Active/pending trade already exists for {sym}"}

    # 5-day cooldown after losing trade on same symbol
    cooldown_cutoff = cur_dt - timedelta(days=5)
    recent_loss = (
        session.query(PaperTrade)
        .filter(
            PaperTrade.run_id == run.id,
            PaperTrade.symbol == sym,
            PaperTrade.status == "CLOSED",
            PaperTrade.realized_pnl_usd < 0.0,
        )
        .order_by(PaperTrade.closed_at.desc())
        .first()
    )
    if recent_loss is not None:
        closed_at = as_utc(recent_loss.closed_at)
        if closed_at is not None and closed_at > cooldown_cutoff:
            return {"approved": False, "reason": f"5-day loss cooldown active on {sym}"}

    day_start = cur_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    todays_orders = (
        session.query(PaperTrade)
        .filter(
            PaperTrade.run_id == run.id,
            PaperTrade.status != "REJECTED",
            PaperTrade.created_at >= day_start,
        )
        .count()
    )
    max_daily = int(paper_cfg.get("max_daily_new_orders", 5))
    if todays_orders >= max_daily:
        return {"approved": False, "reason": f"Max daily orders reached ({todays_orders}/{max_daily})"}

    base_max_pos = int(paper_cfg.get("max_open_positions", 6))
    eff_max_pos = max(2, base_max_pos // 2) if (market_regime == "RISK_OFF" and direction == "LONG") else base_max_pos
    if len(active_trades) >= eff_max_pos:
        return {"approved": False, "reason": f"Max open positions reached ({len(active_trades)}/{eff_max_pos})"}

    sector = str(idea.get("sector") or "").strip()
    max_sec = int(paper_cfg.get("max_positions_per_sector", 2))
    if sector and max_sec > 0:
        same_sector_cnt = sum(1 for t in active_trades if (t.sector_at_entry or "").strip() == sector)
        if same_sector_cnt >= max_sec:
            return {"approved": False, "reason": f"Sector cap reached for {sector} ({same_sector_cnt}/{max_sec})"}

    min_dv = float(paper_cfg.get("min_dollar_volume", 20_000_000.0))
    if min_dv > 0:
        avg_dv = compute_avg_dollar_volume_20d(session, sym)
        if avg_dv is not None and avg_dv < min_dv:
            return {"approved": False, "reason": f"20D avg dollar volume ${avg_dv / 1e6:.1f}M < ${min_dv / 1e6:.1f}M"}

    # Position sizing
    risk_pct = float(paper_cfg.get("risk_per_trade_pct", 1.0))
    max_pos_pct = float(paper_cfg.get("max_position_pct_equity", 15.0))
    risk_budget = float(run.current_equity) * (risk_pct / 100.0)
    risk_per_share = abs(limit_price - stop_loss)
    if risk_per_share <= 0:
        return {"approved": False, "reason": "Zero stop-loss distance"}

    qty_by_risk = math.floor(risk_budget / risk_per_share)
    max_notional = float(run.current_equity) * (max_pos_pct / 100.0)
    avail_cash = max(0.0, float(run.cash_balance) * 0.98)
    qty_by_cap = math.floor(min(max_notional, avail_cash) / limit_price)
    quantity = int(min(qty_by_risk, qty_by_cap))
    if quantity < 1:
        return {
            "approved": False,
            "reason": f"Insufficient size/cash (qty={quantity}, cash=${run.cash_balance:.0f}, limit=${limit_price:.2f})",
        }

    is_immediate = (ref_price <= limit_price) if direction == "LONG" else (ref_price >= limit_price)
    return {
        "approved": True,
        "reason": "Approved",
        "symbol": sym,
        "direction": direction,
        "limit_price": limit_price,
        "ref_price": round(ref_price, 2),
        "quantity": quantity,
        "risk_per_share": round(risk_per_share, 4),
        "risk_amount_usd": round(risk_per_share * quantity, 2),
        "entry_low": round(entry_low, 2),
        "entry_high": round(entry_high, 2),
        "stop_loss": round(stop_loss, 2),
        "tp1": round(tp1, 2),
        "tp2": round(tp2, 2),
        "is_immediate": is_immediate,
    }
