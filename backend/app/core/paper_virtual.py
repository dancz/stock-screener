from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
from sqlalchemy.orm import Session

from app.core.paper_risk import (
    as_utc,
    check_kill_switch,
    ensure_active_paper_run,
    evaluate_candidate_gate,
)
from app.core.request_queue import Priority
from app.core.telegram_bot import dispatch_paper_notification
from app.core.trade_ideas import build_trade_ideas, get_cached_live_quotes_ibkr
from app.db.database import session_scope
from app.db.models import (
    AnalysisSnapshot,
    Candle,
    PaperAccountRun,
    PaperEquitySnapshot,
    PaperOrderEvent,
    PaperTrade,
    load_runtime_settings,
    utcnow,
)
from app.plugins.sources.market_data_yahoo import fetch_live_quote

logger = logging.getLogger(__name__)

_paper_lock = threading.Lock()


def get_live_quotes_with_fallback(
    symbols: list[str],
    ibkr_cfg: dict[str, Any] | None = None,
    *,
    allow_yahoo_fallback: bool | None = None,
) -> dict[str, dict[str, Any]]:
    """Fetch IBKR live/delayed quotes first, falling back to Yahoo Finance on missing/10197 symbols."""
    unique = [s.strip().upper().replace(".", "-") for s in dict.fromkeys(symbols) if s and s.strip()]
    if not unique:
        return {}
    out: dict[str, dict[str, Any]] = {}
    if ibkr_cfg and ibkr_cfg.get("enabled", False):
        try:
            out.update(get_cached_live_quotes_ibkr(unique, ibkr_cfg) or {})
        except Exception as exc:
            logger.debug("IBKR live quote lookup failed: %s", exc)

    use_yahoo = (os.environ.get("PYTEST_CURRENT_TEST") is None) if allow_yahoo_fallback is None else allow_yahoo_fallback
    if use_yahoo:
        missing = [s for s in unique if s not in out or not (out[s] or {}).get("price")]
        for sym in missing[:12]:
            try:
                q = fetch_live_quote(sym, priority=Priority.HIGH)
                if q and q.get("price"):
                    out[sym] = q
            except Exception as exc:
                logger.debug("Yahoo live quote fallback failed for %s: %s", sym, exc)
    return out


def _compute_trailing_stop_candidate(
    trade: PaperTrade,
    ref_price: float,
    paper_cfg: dict[str, Any],
    *,
    confirm_close_price: float | None = None,
) -> float | None:
    """Compute tighter ATR trailing stop once trade exceeds trailing_activation_r."""
    if not bool(paper_cfg.get("trailing_stop_enabled", True)):
        return None
    activation_r = float(paper_cfg.get("trailing_activation_r", 1.5))
    cur_r = _compute_current_r(trade, ref_price)
    if cur_r < activation_r:
        return None

    atr_mult = max(0.5, float(paper_cfg.get("trailing_atr_mult", 2.0)))
    entry_px = float(trade.entry_fill_price or trade.planned_limit_price or 0.0)
    eff_atr = float(trade.atr_at_entry or 0.0)
    if eff_atr <= 0 and entry_px > 0:
        eff_atr = abs(entry_px - float(trade.planned_stop_loss or entry_px * 0.96))
    if eff_atr <= 0 or ref_price <= 0:
        return None

    min_step = max(0.05, (entry_px or ref_price) * 0.002)
    guard_px = confirm_close_price if confirm_close_price is not None else ref_price
    if trade.direction == "LONG":
        cand = round(ref_price - atr_mult * eff_atr, 2)
        if cand >= float(trade.current_stop_loss) + min_step and cand < guard_px - 0.01:
            return cand
        return None

    cand = round(ref_price + atr_mult * eff_atr, 2)
    if cand <= float(trade.current_stop_loss) - min_step and cand > guard_px + 0.01:
        return cand
    return None


def _log_event(
    session: Session,
    run_id: int,
    trade_id: int | None,
    symbol: str,
    event_type: str,
    message: str,
    payload: dict[str, Any] | None = None,
    created_at: datetime | None = None,
) -> None:
    session.add(
        PaperOrderEvent(
            run_id=run_id,
            trade_id=trade_id,
            symbol=symbol,
            event_type=event_type,
            message=message,
            payload_json=json.dumps(payload or {}),
            created_at=as_utc(created_at) or utcnow(),
        )
    )


def _compute_slippage_bps(direction: str, planned_limit: float, fill_price: float) -> float:
    if planned_limit <= 0:
        return 0.0
    if direction == "LONG":
        return round(((fill_price - planned_limit) / planned_limit) * 10000.0, 2)
    return round(((planned_limit - fill_price) / planned_limit) * 10000.0, 2)


def _compute_unrealized_pnl(trade: PaperTrade, mark_price: float) -> float:
    if trade.entry_fill_price is None or trade.quantity_remaining <= 0:
        return 0.0
    if trade.direction == "LONG":
        return round((mark_price - float(trade.entry_fill_price)) * trade.quantity_remaining, 2)
    return round((float(trade.entry_fill_price) - mark_price) * trade.quantity_remaining, 2)


def _compute_current_r(trade: PaperTrade, mark_price: float) -> float:
    if trade.risk_amount_usd <= 0:
        return 0.0
    return _compute_unrealized_pnl(trade, mark_price) / float(trade.risk_amount_usd)


def _close_trade(
    session: Session,
    run: PaperAccountRun,
    trade: PaperTrade,
    exit_price: float,
    exit_reason: str,
    now_utc: datetime,
) -> None:
    exit_px = round(float(exit_price), 2)
    qty = int(trade.quantity_remaining or trade.quantity_filled or 0)
    entry_px = float(trade.entry_fill_price or trade.planned_limit_price)
    entry_notional = round(entry_px * qty, 2)
    raw_pnl = (exit_px - entry_px) * qty if trade.direction == "LONG" else (entry_px - exit_px) * qty
    realized_pnl = round(raw_pnl - float(trade.commission_usd or 0.0), 2)
    realized_pct = round((realized_pnl / entry_notional) * 100.0, 2) if entry_notional > 0 else 0.0
    r_mult = round(realized_pnl / float(trade.risk_amount_usd), 2) if trade.risk_amount_usd > 0 else 0.0

    run.cash_balance = round(float(run.cash_balance) + entry_notional + realized_pnl, 2)
    trade.status = "CLOSED"
    trade.exit_fill_price = exit_px
    trade.last_mark_price = exit_px
    trade.quantity_remaining = 0
    trade.unrealized_pnl_usd = 0.0
    trade.realized_pnl_usd = realized_pnl
    trade.realized_pnl_pct = realized_pct
    trade.r_multiple = r_mult
    trade.exit_reason = exit_reason
    trade.closed_at = now_utc

    _log_event(
        session,
        run.id,
        trade.id,
        trade.symbol,
        "ORDER_CLOSED",
        f"Closed {trade.direction} {trade.symbol} @ ${exit_px:.2f} ({exit_reason}), PnL ${realized_pnl:+.2f} ({r_mult:+.2f}R)",
        {"exit_price": exit_px, "exit_reason": exit_reason, "pnl_usd": realized_pnl, "r_multiple": r_mult},
        created_at=now_utc,
    )


def _latest_spy_close(session: Session) -> float | None:
    row = (
        session.query(Candle.close)
        .filter(Candle.symbol == "SPY", Candle.timeframe == "1d")
        .order_by(Candle.ts.desc())
        .first()
    )
    return float(row[0]) if row is not None else None


def recompute_run_equity(session: Session, run: PaperAccountRun, now_utc: datetime | None = None) -> None:
    cur_dt = as_utc(now_utc) or utcnow()
    all_trades = session.query(PaperTrade).filter(PaperTrade.run_id == run.id).all()

    pending_reserved = sum(
        float(t.planned_limit_price) * int(t.quantity_planned)
        for t in all_trades
        if t.status == "PENDING_ENTRY"
    )
    open_trades = [t for t in all_trades if t.status in ("OPEN", "PARTIAL_TP1")]
    open_value = sum(
        (float(t.entry_fill_price or t.planned_limit_price) * int(t.quantity_remaining))
        + float(t.unrealized_pnl_usd or 0.0)
        for t in open_trades
    )
    realized_cum = round(
        sum(float(t.realized_pnl_usd or 0.0) for t in all_trades if t.status == "CLOSED"),
        2,
    )
    unrealized_sum = round(sum(float(t.unrealized_pnl_usd or 0.0) for t in open_trades), 2)

    run.current_equity = round(float(run.cash_balance) + pending_reserved + open_value, 2)
    run.high_water_mark = round(max(float(run.high_water_mark), run.current_equity), 2)

    dd_pct = (
        round(((run.current_equity - run.high_water_mark) / run.high_water_mark) * 100.0, 2)
        if run.high_water_mark > 0
        else 0.0
    )
    cur_date = cur_dt.date().isoformat()
    spy_close = _latest_spy_close(session)

    snap = (
        session.query(PaperEquitySnapshot)
        .filter(PaperEquitySnapshot.run_id == run.id, PaperEquitySnapshot.snapshot_date == cur_date)
        .first()
    )
    if snap is None:
        snap = PaperEquitySnapshot(
            run_id=run.id,
            snapshot_date=cur_date,
            equity=run.current_equity,
            cash=round(float(run.cash_balance), 2),
            open_positions_count=len(open_trades),
            realized_pnl_cum=realized_cum,
            unrealized_pnl=unrealized_sum,
            drawdown_pct=dd_pct,
            spy_close=spy_close,
        )
        session.add(snap)
    else:
        snap.equity = run.current_equity
        snap.cash = round(float(run.cash_balance), 2)
        snap.open_positions_count = len(open_trades)
        snap.realized_pnl_cum = realized_cum
        snap.unrealized_pnl = unrealized_sum
        snap.drawdown_pct = dd_pct
        if spy_close is not None:
            snap.spy_close = spy_close


def _load_post_bars(
    session: Session,
    symbol: str,
    since_dt: datetime,
    bars_overrides: dict[str, list[dict[str, Any]]] | None = None,
    min_ts_exclusive: int | None = None,
) -> list[dict[str, float]]:
    if bars_overrides and symbol in bars_overrides:
        since_ts = int(since_dt.timestamp())
        sorted_bars = sorted(bars_overrides[symbol], key=lambda x: int(x["ts"]))
        return [
            {
                "ts": int(b["ts"]),
                "open": float(b["open"]),
                "high": float(b["high"]),
                "low": float(b["low"]),
                "close": float(b["close"]),
            }
            for b in sorted_bars
            if (int(b["ts"]) > int(min_ts_exclusive) if min_ts_exclusive is not None else int(b["ts"]) >= since_ts)
        ]

    since_ts = max(int(since_dt.timestamp()), int(min_ts_exclusive)) if min_ts_exclusive is not None else int(since_dt.timestamp())
    rows_1h = (
        session.query(Candle.ts, Candle.open, Candle.high, Candle.low, Candle.close)
        .filter(Candle.symbol == symbol, Candle.timeframe == "1h", Candle.ts > since_ts)
        .order_by(Candle.ts.asc())
        .all()
    )
    if rows_1h:
        return [
            {"ts": int(ts), "open": float(o), "high": float(h), "low": float(lo), "close": float(c)}
            for ts, o, h, lo, c in rows_1h
        ]

    # Daily bars represent full sessions; only evaluate strictly later UTC days to avoid lookahead
    next_day_dt = datetime.combine(since_dt.date() + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
    min_day_ts = int(next_day_dt.timestamp())
    if min_ts_exclusive is not None:
        min_day_ts = max(min_day_ts, int(min_ts_exclusive) + 1)
    rows_1d = (
        session.query(Candle.ts, Candle.open, Candle.high, Candle.low, Candle.close)
        .filter(Candle.symbol == symbol, Candle.timeframe == "1d", Candle.ts >= min_day_ts)
        .order_by(Candle.ts.asc())
        .all()
    )
    return [
        {"ts": int(ts), "open": float(o), "high": float(h), "low": float(lo), "close": float(c)}
        for ts, o, h, lo, c in rows_1d
    ]


def _latest_mark_price(
    session: Session,
    symbol: str,
    price_overrides: dict[str, float] | None = None,
    quotes_map: dict[str, dict[str, Any]] | None = None,
) -> float | None:
    if price_overrides and symbol in price_overrides:
        return float(price_overrides[symbol])
    if quotes_map and symbol in quotes_map and quotes_map[symbol].get("price"):
        return float(quotes_map[symbol]["price"])
    bar = (
        session.query(Candle.close)
        .filter(Candle.symbol == symbol)
        .order_by(Candle.ts.desc())
        .first()
    )
    if bar is not None:
        return float(bar[0])
    snap = (
        session.query(AnalysisSnapshot.last_price)
        .filter(AnalysisSnapshot.symbol == symbol)
        .order_by(AnalysisSnapshot.id.desc())
        .first()
    )
    return float(snap[0]) if snap is not None and snap[0] else None


def _current_days_to_earnings(session: Session, symbol: str, now_utc: datetime) -> int | None:
    snap = (
        session.query(AnalysisSnapshot)
        .filter(AnalysisSnapshot.symbol == symbol, AnalysisSnapshot.is_deep_enriched.is_(True))
        .order_by(AnalysisSnapshot.id.desc())
        .first()
    )
    if snap is None:
        return None
    try:
        ev = json.loads(snap.events_insiders_json or "{}")
    except json.JSONDecodeError:
        return None
    next_ed = ev.get("next_earnings_date")
    if isinstance(next_ed, str) and next_ed.strip():
        try:
            ed = datetime.fromisoformat(next_ed.strip()[:10]).date()
            return (ed - now_utc.date()).days
        except ValueError:
            pass
    dte = ev.get("days_to_earnings")
    return int(dte) if dte is not None else None


def step_virtual_lifecycle(
    session: Session,
    run: PaperAccountRun | None = None,
    runtime_settings: dict[str, Any] | None = None,
    *,
    price_overrides: dict[str, float] | None = None,
    bars_overrides: dict[str, list[dict[str, Any]]] | None = None,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Advance PENDING_ENTRY and OPEN virtual trades against bars/live marks."""
    cur_dt = as_utc(now_utc) or utcnow()
    runtime = runtime_settings or load_runtime_settings(session)
    paper_cfg = runtime.get("paper_trading", {})
    target_run = run or ensure_active_paper_run(session, runtime, now_utc=cur_dt)

    active = (
        session.query(PaperTrade)
        .filter(
            PaperTrade.run_id == target_run.id,
            PaperTrade.status.in_(("PENDING_ENTRY", "OPEN", "PARTIAL_TP1")),
        )
        .order_by(PaperTrade.id.asc())
        .all()
    )

    quotes_map: dict[str, dict[str, Any]] = {}
    if active and price_overrides is None and bars_overrides is None:
        ibkr_cfg = runtime.get("ibkr", {})
        syms = list(dict.fromkeys(t.symbol for t in active))
        quotes_map = get_live_quotes_with_fallback(syms, ibkr_cfg)

    filled_count = 0
    expired_count = 0
    closed_count = 0
    be_moved_count = 0
    trailing_moved_count = 0
    was_halted = bool(target_run.kill_switch_triggered)

    for trade in active:
        if trade.status == "PENDING_ENTRY":
            sub_dt = as_utc(trade.submitted_at or trade.created_at) or cur_dt
            bars = _load_post_bars(session, trade.symbol, sub_dt, bars_overrides=bars_overrides)
            filled_bar: dict[str, float] | None = None
            fill_px: float | None = None

            for b in bars:
                if trade.direction == "LONG" and b["low"] <= trade.planned_limit_price:
                    filled_bar = b
                    fill_px = min(b["open"], trade.planned_limit_price) if b["open"] < trade.planned_limit_price else trade.planned_limit_price
                    break
                if trade.direction == "SHORT" and b["high"] >= trade.planned_limit_price:
                    filled_bar = b
                    fill_px = max(b["open"], trade.planned_limit_price) if b["open"] > trade.planned_limit_price else trade.planned_limit_price
                    break

            if fill_px is None:
                mark_px = _latest_mark_price(session, trade.symbol, price_overrides=price_overrides, quotes_map=quotes_map)
                if mark_px is not None:
                    if (trade.direction == "LONG" and mark_px <= trade.planned_limit_price) or (
                        trade.direction == "SHORT" and mark_px >= trade.planned_limit_price
                    ):
                        fill_px = mark_px

            if fill_px is not None:
                fill_px = round(float(fill_px), 2)
                fill_dt = (
                    datetime.fromtimestamp(int(filled_bar["ts"]), tz=timezone.utc)
                    if filled_bar is not None
                    else cur_dt
                )
                qty = int(trade.quantity_planned)
                old_reserved = round(float(trade.planned_limit_price) * qty, 2)
                actual_notional = round(fill_px * qty, 2)
                target_run.cash_balance = round(float(target_run.cash_balance) + old_reserved - actual_notional, 2)

                trade.status = "OPEN"
                trade.entry_fill_price = fill_px
                trade.last_mark_price = fill_px
                trade.quantity_filled = qty
                trade.quantity_remaining = qty
                trade.entry_slippage_bps = _compute_slippage_bps(trade.direction, trade.planned_limit_price, fill_px)
                trade.risk_amount_usd = round(abs(fill_px - trade.current_stop_loss) * qty, 2)
                trade.filled_at = fill_dt
                trade.last_evaluated_bar_ts = int(filled_bar["ts"]) if filled_bar is not None else int(cur_dt.timestamp())
                trade.unrealized_pnl_usd = 0.0
                filled_count += 1

                _log_event(
                    session,
                    target_run.id,
                    trade.id,
                    trade.symbol,
                    "ORDER_FILLED",
                    f"Filled {trade.direction} {qty} {trade.symbol} @ ${fill_px:.2f} (limit ${trade.planned_limit_price:.2f})",
                    {"fill_price": fill_px, "quantity": qty, "slippage_bps": trade.entry_slippage_bps},
                    created_at=fill_dt,
                )
                dispatch_paper_notification(
                    runtime,
                    "orders",
                    f"Virtual Limit Filled: {trade.direction} {trade.symbol}",
                    [
                        f"Qty: {qty} @ ${fill_px:.2f} (limit ${trade.planned_limit_price:.2f})",
                        f"SL: ${trade.current_stop_loss:.2f} · TP1: ${trade.planned_tp1:.2f}",
                    ],
                )
                # Continue to evaluate subsequent bars strictly after fill_dt if any!
            elif cur_dt.date() > sub_dt.date() or (cur_dt - sub_dt) > timedelta(hours=20):
                reserved = round(float(trade.planned_limit_price) * int(trade.quantity_planned), 2)
                target_run.cash_balance = round(float(target_run.cash_balance) + reserved, 2)
                trade.status = "CANCELLED"
                trade.exit_reason = "ENTRY_EXPIRED"
                trade.closed_at = cur_dt
                expired_count += 1
                _log_event(
                    session,
                    target_run.id,
                    trade.id,
                    trade.symbol,
                    "ORDER_CANCELLED",
                    f"Expired unfilled {trade.direction} limit order for {trade.symbol} @ ${trade.planned_limit_price:.2f}",
                    {"exit_reason": "ENTRY_EXPIRED"},
                    created_at=cur_dt,
                )
                continue

        if trade.status in ("OPEN", "PARTIAL_TP1"):
            filled_dt = as_utc(trade.filled_at or trade.created_at) or cur_dt
            bars = _load_post_bars(
                session,
                trade.symbol,
                filled_dt,
                bars_overrides=bars_overrides,
                min_ts_exclusive=trade.last_evaluated_bar_ts,
            )
            exited = False
            for b in bars:
                bar_dt = datetime.fromtimestamp(int(b["ts"]), tz=timezone.utc)
                if trade.direction == "LONG":
                    hit_sl = b["low"] <= trade.current_stop_loss
                    hit_tp = b["high"] >= trade.planned_tp1
                else:
                    hit_sl = b["high"] >= trade.current_stop_loss
                    hit_tp = b["low"] <= trade.planned_tp1

                # Conservative intrabar rule: stop loss takes precedence over target if both hit in same bar
                if hit_sl:
                    exit_px = (
                        b["open"]
                        if (trade.direction == "LONG" and b["open"] < trade.current_stop_loss)
                        or (trade.direction == "SHORT" and b["open"] > trade.current_stop_loss)
                        else trade.current_stop_loss
                    )
                    reason = (
                        "TRAILING_STOP"
                        if getattr(trade, "trailing_active", False)
                        else ("BREAK_EVEN" if trade.be_moved else "STOP_LOSS")
                    )
                    _close_trade(session, target_run, trade, exit_px, reason, bar_dt)
                    trade.last_evaluated_bar_ts = max(int(trade.last_evaluated_bar_ts or 0), int(b["ts"]))
                    closed_count += 1
                    exited = True
                    dispatch_paper_notification(
                        runtime,
                        "orders",
                        f"Virtual Closed: {trade.symbol} ({reason})",
                        [
                            f"Exit: ${float(trade.exit_fill_price or exit_px):.2f}",
                            f"PnL: ${trade.realized_pnl_usd:+.2f} ({trade.r_multiple:+.2f}R)",
                        ],
                    )
                    break

                if hit_tp:
                    exit_px = (
                        b["open"]
                        if (trade.direction == "LONG" and b["open"] > trade.planned_tp1)
                        or (trade.direction == "SHORT" and b["open"] < trade.planned_tp1)
                        else trade.planned_tp1
                    )
                    _close_trade(session, target_run, trade, exit_px, "TP1_HIT", bar_dt)
                    trade.last_evaluated_bar_ts = max(int(trade.last_evaluated_bar_ts or 0), int(b["ts"]))
                    closed_count += 1
                    exited = True
                    dispatch_paper_notification(
                        runtime,
                        "orders",
                        f"Virtual Target Hit: {trade.symbol} (TP1_HIT)",
                        [
                            f"Exit: ${float(trade.exit_fill_price or exit_px):.2f}",
                            f"PnL: ${trade.realized_pnl_usd:+.2f} ({trade.r_multiple:+.2f}R)",
                        ],
                    )
                    break

                # Check Break-Even trigger on favorable excursion if position stays open
                fav_px = b["high"] if trade.direction == "LONG" else b["low"]
                if (
                    not trade.be_moved
                    and _compute_current_r(trade, fav_px) >= float(paper_cfg.get("move_to_be_at_r", 1.0))
                ):
                    offset = max(0.1 * float(trade.atr_at_entry or 0.0), float(trade.entry_fill_price or 0.0) * 0.001)
                    if trade.direction == "LONG":
                        new_sl = round(max(trade.current_stop_loss, float(trade.entry_fill_price) + offset), 2)
                        if new_sl < b["close"]:
                            trade.current_stop_loss = new_sl
                            trade.be_moved = True
                            be_moved_count += 1
                            _log_event(
                                session,
                                target_run.id,
                                trade.id,
                                trade.symbol,
                                "SL_MOVED_BE",
                                f"Moved {trade.symbol} stop loss to Break-Even @ ${new_sl:.2f}",
                                {"new_stop_loss": new_sl},
                                created_at=bar_dt,
                            )
                            dispatch_paper_notification(
                                runtime,
                                "lifecycle",
                                f"Break-Even Stop: {trade.symbol}",
                                [f"Moved SL to ${new_sl:.2f} after favorable excursion."],
                            )
                    else:
                        new_sl = round(min(trade.current_stop_loss, float(trade.entry_fill_price) - offset), 2)
                        if new_sl > b["close"]:
                            trade.current_stop_loss = new_sl
                            trade.be_moved = True
                            be_moved_count += 1
                            _log_event(
                                session,
                                target_run.id,
                                trade.id,
                                trade.symbol,
                                "SL_MOVED_BE",
                                f"Moved {trade.symbol} short stop loss to Break-Even @ ${new_sl:.2f}",
                                {"new_stop_loss": new_sl},
                                created_at=bar_dt,
                            )
                            dispatch_paper_notification(
                                runtime,
                                "lifecycle",
                                f"Break-Even Stop: {trade.symbol}",
                                [f"Moved short SL to ${new_sl:.2f} after favorable excursion."],
                            )

                # Dynamic ATR Trailing Stop inside bar
                cand_trail = _compute_trailing_stop_candidate(trade, fav_px, paper_cfg, confirm_close_price=b["close"])
                if cand_trail is not None:
                    trade.current_stop_loss = cand_trail
                    trade.be_moved = True
                    trade.trailing_active = True
                    trailing_moved_count += 1
                    _log_event(
                        session,
                        target_run.id,
                        trade.id,
                        trade.symbol,
                        "SL_TRAILING",
                        f"Trailed {trade.symbol} stop loss to ${cand_trail:.2f}",
                        {"new_stop_loss": cand_trail},
                        created_at=bar_dt,
                    )
                    dispatch_paper_notification(
                        runtime,
                        "lifecycle",
                        f"Trailing Stop Raised: {trade.symbol}",
                        [f"Updated virtual SL to ${cand_trail:.2f}."],
                    )

                trade.last_evaluated_bar_ts = max(int(trade.last_evaluated_bar_ts or 0), int(b["ts"]))

            if exited:
                continue

            mark_px = _latest_mark_price(session, trade.symbol, price_overrides=price_overrides, quotes_map=quotes_map)
            if mark_px is None and bars:
                mark_px = float(bars[-1]["close"])
            if mark_px is None:
                mark_px = float(trade.last_mark_price or trade.entry_fill_price or trade.planned_limit_price)

            # Live quote / mark price stop or TP check (when no new bars covered it yet)
            if (trade.direction == "LONG" and mark_px <= trade.current_stop_loss) or (
                trade.direction == "SHORT" and mark_px >= trade.current_stop_loss
            ):
                reason = (
                    "TRAILING_STOP"
                    if getattr(trade, "trailing_active", False)
                    else ("BREAK_EVEN" if trade.be_moved else "STOP_LOSS")
                )
                _close_trade(session, target_run, trade, mark_px, reason, cur_dt)
                closed_count += 1
                dispatch_paper_notification(
                    runtime,
                    "orders",
                    f"Virtual Closed: {trade.symbol} ({reason})",
                    [
                        f"Exit: ${float(trade.exit_fill_price or mark_px):.2f}",
                        f"PnL: ${trade.realized_pnl_usd:+.2f} ({trade.r_multiple:+.2f}R)",
                    ],
                )
                continue

            if (trade.direction == "LONG" and mark_px >= trade.planned_tp1) or (
                trade.direction == "SHORT" and mark_px <= trade.planned_tp1
            ):
                _close_trade(session, target_run, trade, mark_px, "TP1_HIT", cur_dt)
                closed_count += 1
                dispatch_paper_notification(
                    runtime,
                    "orders",
                    f"Virtual Target Hit: {trade.symbol} (TP1_HIT)",
                    [
                        f"Exit: ${float(trade.exit_fill_price or mark_px):.2f}",
                        f"PnL: ${trade.realized_pnl_usd:+.2f} ({trade.r_multiple:+.2f}R)",
                    ],
                )
                continue

            trade.last_mark_price = round(mark_px, 2)
            trade.unrealized_pnl_usd = _compute_unrealized_pnl(trade, mark_px)
            cur_r = _compute_current_r(trade, mark_px)

            # Break-Even move at live mark
            if not trade.be_moved and cur_r >= float(paper_cfg.get("move_to_be_at_r", 1.0)):
                offset = max(0.1 * float(trade.atr_at_entry or 0.0), float(trade.entry_fill_price or 0.0) * 0.001)
                if trade.direction == "LONG":
                    new_sl = round(min(mark_px - 0.01, max(trade.current_stop_loss, float(trade.entry_fill_price) + offset)), 2)
                    if new_sl > trade.current_stop_loss:
                        trade.current_stop_loss = new_sl
                        trade.be_moved = True
                        trade.last_evaluated_bar_ts = max(int(trade.last_evaluated_bar_ts or 0), int(cur_dt.timestamp()))
                        be_moved_count += 1
                        _log_event(
                            session,
                            target_run.id,
                            trade.id,
                            trade.symbol,
                            "SL_MOVED_BE",
                            f"Moved {trade.symbol} stop loss to Break-Even @ ${new_sl:.2f}",
                            {"new_stop_loss": new_sl},
                            created_at=cur_dt,
                        )
                        dispatch_paper_notification(
                            runtime,
                            "lifecycle",
                            f"Break-Even Stop: {trade.symbol}",
                            [f"Moved SL to ${new_sl:.2f} ({cur_r:+.2f}R)."],
                        )
                else:
                    new_sl = round(max(mark_px + 0.01, min(trade.current_stop_loss, float(trade.entry_fill_price) - offset)), 2)
                    if new_sl < trade.current_stop_loss:
                        trade.current_stop_loss = new_sl
                        trade.be_moved = True
                        trade.last_evaluated_bar_ts = max(int(trade.last_evaluated_bar_ts or 0), int(cur_dt.timestamp()))
                        be_moved_count += 1
                        _log_event(
                            session,
                            target_run.id,
                            trade.id,
                            trade.symbol,
                            "SL_MOVED_BE",
                            f"Moved {trade.symbol} short stop loss to Break-Even @ ${new_sl:.2f}",
                            {"new_stop_loss": new_sl},
                            created_at=cur_dt,
                        )
                        dispatch_paper_notification(
                            runtime,
                            "lifecycle",
                            f"Break-Even Stop: {trade.symbol}",
                            [f"Moved short SL to ${new_sl:.2f} ({cur_r:+.2f}R)."],
                        )

            # Trailing stop at live mark
            cand_trail_live = _compute_trailing_stop_candidate(trade, mark_px, paper_cfg)
            if cand_trail_live is not None:
                trade.current_stop_loss = cand_trail_live
                trade.be_moved = True
                trade.trailing_active = True
                trade.last_evaluated_bar_ts = max(int(trade.last_evaluated_bar_ts or 0), int(cur_dt.timestamp()))
                trailing_moved_count += 1
                _log_event(
                    session,
                    target_run.id,
                    trade.id,
                    trade.symbol,
                    "SL_TRAILING",
                    f"Trailed {trade.symbol} stop loss to ${cand_trail_live:.2f} ({cur_r:+.2f}R)",
                    {"new_stop_loss": cand_trail_live, "cur_r": round(cur_r, 2)},
                    created_at=cur_dt,
                )
                dispatch_paper_notification(
                    runtime,
                    "lifecycle",
                    f"Trailing Stop Raised: {trade.symbol}",
                    [f"Updated SL to ${cand_trail_live:.2f} ({cur_r:+.2f}R)."],
                )

            # Earnings Exit Guard (close when earnings are <= 1 day away)
            dte = _current_days_to_earnings(session, trade.symbol, cur_dt)
            if dte is not None and 0 <= dte <= 1:
                _close_trade(session, target_run, trade, mark_px, "EARNINGS_EXIT", cur_dt)
                closed_count += 1
                dispatch_paper_notification(
                    runtime,
                    "lifecycle",
                    f"Earnings Exit: {trade.symbol}",
                    [f"Closed before earnings ({dte}d) @ ${mark_px:.2f} | PnL ${trade.realized_pnl_usd:+.2f}"],
                )
                continue

            # Time Stop (release stagnant position after N business days below +0.5R)
            time_stop_days = int(paper_cfg.get("time_stop_days", 15))
            if time_stop_days > 0:
                held_bdays = int(np.busday_count(filled_dt.date(), cur_dt.date()))
                if held_bdays >= time_stop_days and cur_r < 0.5:
                    _close_trade(session, target_run, trade, mark_px, "TIME_STOP", cur_dt)
                    closed_count += 1
                    dispatch_paper_notification(
                        runtime,
                        "lifecycle",
                        f"Time Stop Exit: {trade.symbol}",
                        [f"Held {held_bdays} business days < +0.5R; closed @ ${mark_px:.2f} ({trade.r_multiple:+.2f}R)"],
                    )
                    continue

    recompute_run_equity(session, target_run, now_utc=cur_dt)
    halted, halt_reason = check_kill_switch(session, target_run, paper_cfg, now_utc=cur_dt)
    session.commit()
    if halted and not was_halted and target_run.kill_switch_triggered:
        dispatch_paper_notification(
            runtime,
            "kill_switch",
            "Kill-Switch Triggered",
            [f"Run: {target_run.name}", f"Reason: {halt_reason}"],
        )
    return {
        "run_id": target_run.id,
        "filled": filled_count,
        "expired": expired_count,
        "closed": closed_count,
        "be_moved": be_moved_count,
        "trailing_moved": trailing_moved_count,
        "current_equity": target_run.current_equity,
    }


def run_paper_allocation_cycle(
    *,
    force: bool = False,
    price_overrides: dict[str, float] | None = None,
    now_utc: datetime | None = None,
    db_session: Session | None = None,
) -> dict[str, Any]:
    """Evaluate latest Trade Ideas through Risk Gatekeeper and open virtual paper positions."""
    with _paper_lock:
        cur_dt = as_utc(now_utc) or utcnow()
        own_session = db_session is None
        if own_session:
            ctx = session_scope()
            session = ctx.__enter__()
        else:
            ctx = None
            session = db_session  # type: ignore[assignment]

        try:
            runtime = load_runtime_settings(session)
            paper_cfg = runtime.get("paper_trading", {})

            if not force and not bool(paper_cfg.get("enabled", False)):
                return {"executed": False, "reason": "Paper trading is disabled."}

            mode = str(paper_cfg.get("mode", "virtual") or "virtual")
            if mode not in ("virtual", "ibkr_paper"):
                return {"executed": False, "reason": f"Unsupported execution mode: {mode}"}

            run = ensure_active_paper_run(session, runtime, now_utc=cur_dt)
            if mode == "ibkr_paper":
                from app.core.paper_ibkr import step_ibkr_paper_lifecycle, submit_ibkr_bracket_order

                ibkr_cfg_chk = runtime.get("ibkr", {})
                if not ibkr_cfg_chk.get("enabled", False):
                    return {"executed": False, "reason": "IBKR Gateway is disabled in Settings."}
                lifecycle_summary = step_ibkr_paper_lifecycle(
                    session,
                    run=run,
                    runtime_settings=runtime,
                    price_overrides=price_overrides,
                    now_utc=cur_dt,
                )
            else:
                lifecycle_summary = step_virtual_lifecycle(
                    session,
                    run=run,
                    runtime_settings=runtime,
                    price_overrides=price_overrides,
                    now_utc=cur_dt,
                )

            was_halted = bool(run.kill_switch_triggered)
            blocked, block_reason = check_kill_switch(session, run, paper_cfg, now_utc=cur_dt)
            if blocked:
                if not was_halted and run.kill_switch_triggered:
                    session.commit()
                    dispatch_paper_notification(
                        runtime,
                        "kill_switch",
                        "Kill-Switch Triggered",
                        [f"Run: {run.name}", f"Reason: {block_reason}"],
                    )
                return {
                    "executed": False,
                    "reason": block_reason,
                    "lifecycle": lifecycle_summary,
                    "opened": [],
                    "rejected": [],
                }

            ibkr_cfg = runtime.get("ibkr", {})
            live_fetcher = None
            if price_overrides is None:
                live_fetcher = lambda syms: get_live_quotes_with_fallback(syms, ibkr_cfg)

            min_conv = float(paper_cfg.get("min_conviction", 68.0))
            min_rr = float(paper_cfg.get("min_rr", 1.8))
            quotes_arg = (
                {sym: {"price": px, "source": "Test Override"} for sym, px in price_overrides.items()}
                if price_overrides is not None
                else None
            )

            ideas_payload = build_trade_ideas(
                session,
                limit=25,
                horizon="all",
                min_rr=min_rr,
                min_conviction=0.0,
                hide_conflicted=True,
                hide_stale=True,
                live_quotes=quotes_arg,
                live_quote_fetcher=live_fetcher,
            )

            regime_obj = ideas_payload.get("market_regime") or {}
            market_regime = str(regime_obj.get("regime") or "NEUTRAL")
            candidates = list(ideas_payload.get("buys", []))
            if bool(paper_cfg.get("allow_short", False)):
                candidates.extend(ideas_payload.get("sells", []))
            candidates.sort(
                key=lambda x: (float(x.get("conviction_score") or 0.0), float(x.get("risk_reward") or 0.0)),
                reverse=True,
            )

            # Preload latest snapshot metadata for entry attribution
            snap_by_sym: dict[str, AnalysisSnapshot] = {}
            for item in candidates:
                sym = item["symbol"]
                if sym not in snap_by_sym:
                    snap = (
                        session.query(AnalysisSnapshot)
                        .filter(AnalysisSnapshot.symbol == sym)
                        .order_by(AnalysisSnapshot.id.desc())
                        .first()
                    )
                    if snap is not None:
                        snap_by_sym[sym] = snap

            opened: list[dict[str, Any]] = []
            rejected: list[dict[str, Any]] = []

            for idea in candidates:
                sym = idea["symbol"]
                quote_hint = quotes_arg.get(sym) if quotes_arg else None
                gate = evaluate_candidate_gate(
                    session,
                    run,
                    idea,
                    paper_cfg,
                    market_regime=market_regime,
                    quote=quote_hint,
                    now_utc=cur_dt,
                )
                if not gate["approved"]:
                    rejected.append({"symbol": sym, "direction": idea.get("direction"), "reason": gate["reason"]})
                    continue

                snap = snap_by_sym.get(sym)
                atr_val = 0.0
                signals_json = "[]"
                plugin_scores: dict[str, float] = {}
                if snap is not None:
                    try:
                        tp_dict = json.loads(snap.trade_plan_json or "{}")
                        atr_val = float(tp_dict.get("atr") or 0.0)
                    except json.JSONDecodeError:
                        pass
                    try:
                        sigs = json.loads(snap.signals_json or "[]")
                        signals_json = json.dumps([s.get("code") for s in sigs if isinstance(s, dict) and s.get("code")])
                    except json.JSONDecodeError:
                        pass
                    try:
                        prs = json.loads(snap.plugin_results_json or "{}")
                        plugin_scores = {
                            k: round(float(v.get("score") or 0.0), 1)
                            for k, v in prs.items()
                            if isinstance(v, dict)
                        }
                    except json.JSONDecodeError:
                        pass

                qty = int(gate["quantity"])
                limit_px = float(gate["limit_price"])
                ref_px = float(gate["ref_price"])
                is_immediate = bool(gate["is_immediate"])

                if mode == "ibkr_paper":
                    try:
                        ib_res = submit_ibkr_bracket_order(
                            symbol=sym,
                            direction=gate["direction"],
                            quantity=qty,
                            limit_price=limit_px,
                            stop_loss=gate["stop_loss"],
                            tp_price=gate["tp1"],
                            ibkr_cfg=ibkr_cfg,
                            expected_account=run.ibkr_account,
                        )
                    except Exception as exc:
                        ib_res = {"ok": False, "error": str(exc)}

                    if not ib_res.get("ok"):
                        err_msg = str(ib_res.get("error") or "IBKR order submission failed")
                        rejected.append({"symbol": sym, "direction": idea.get("direction"), "reason": err_msg})
                        _log_event(
                            session,
                            run.id,
                            None,
                            sym,
                            "ORDER_ERROR",
                            f"[IBKR] Rejected {gate['direction']} {qty} {sym}: {err_msg}",
                            {"limit_price": limit_px, "stop_loss": gate["stop_loss"], "tp1": gate["tp1"]},
                            created_at=cur_dt,
                        )
                        continue

                    du_acct = str(ib_res.get("account") or run.ibkr_account or "")
                    if du_acct and run.ibkr_account != du_acct:
                        run.ibkr_account = du_acct
                    ib_filled_qty = int(ib_res.get("filled_qty") or 0)
                    ib_fill_px = ib_res.get("avg_fill_price")
                    is_ib_filled = ib_filled_qty > 0 and ib_fill_px

                    if is_ib_filled:
                        fill_px = round(float(ib_fill_px), 2)
                        entry_notional = round(fill_px * ib_filled_qty, 2)
                        run.cash_balance = round(float(run.cash_balance) - entry_notional, 2)
                        risk_amt = round(abs(fill_px - float(gate["stop_loss"])) * ib_filled_qty, 2)
                        slip_bps = _compute_slippage_bps(gate["direction"], limit_px, fill_px)
                        trade = PaperTrade(
                            run_id=run.id,
                            symbol=sym,
                            scan_run_id=idea.get("scan_run_id"),
                            strategy_horizon="all",
                            direction=gate["direction"],
                            setup_name=str(idea.get("setup_name") or ""),
                            status="OPEN",
                            created_at=cur_dt,
                            submitted_at=cur_dt,
                            filled_at=cur_dt,
                            last_evaluated_bar_ts=int(cur_dt.timestamp()),
                            planned_entry_low=gate["entry_low"],
                            planned_entry_high=gate["entry_high"],
                            planned_limit_price=limit_px,
                            planned_stop_loss=gate["stop_loss"],
                            planned_tp1=gate["tp1"],
                            planned_tp2=gate["tp2"],
                            planned_rr=float(idea.get("risk_reward") or 0.0),
                            current_stop_loss=gate["stop_loss"],
                            atr_at_entry=atr_val,
                            quantity_planned=qty,
                            quantity_filled=ib_filled_qty,
                            quantity_remaining=ib_filled_qty,
                            entry_fill_price=fill_px,
                            last_mark_price=fill_px,
                            entry_slippage_bps=slip_bps,
                            risk_amount_usd=risk_amt,
                            unrealized_pnl_usd=0.0,
                            ibkr_parent_order_id=ib_res.get("parent_order_id"),
                            ibkr_stop_order_id=ib_res.get("stop_order_id"),
                            ibkr_tp1_order_id=ib_res.get("tp1_order_id"),
                            ibkr_perm_ids_json=json.dumps(ib_res.get("perm_ids") or []),
                            conviction_at_entry=float(idea.get("conviction_score") or 0.0),
                            composite_score_at_entry=float(idea.get("composite_score") or 0.0),
                            market_regime_at_entry=market_regime,
                            sector_at_entry=str(idea.get("sector") or ""),
                            signals_at_entry_json=signals_json,
                            plugin_scores_at_entry_json=json.dumps(plugin_scores),
                        )
                        session.add(trade)
                        session.flush()
                        _log_event(
                            session,
                            run.id,
                            trade.id,
                            sym,
                            "ORDER_FILLED",
                            f"[IBKR {du_acct}] Bracket {gate['direction']} {ib_filled_qty} {sym} filled @ ${fill_px:.2f} (SL ${gate['stop_loss']:.2f}, TP1 ${gate['tp1']:.2f})",
                            {"parent_order_id": trade.ibkr_parent_order_id, "conviction": trade.conviction_at_entry},
                            created_at=cur_dt,
                        )
                        dispatch_paper_notification(
                            runtime,
                            "orders",
                            f"IBKR Order Filled: {gate['direction']} {sym}",
                            [
                                f"Account: {du_acct} · Qty: {ib_filled_qty} @ ${fill_px:.2f}",
                                f"SL: ${gate['stop_loss']:.2f} · TP1: ${gate['tp1']:.2f} · Conv: {trade.conviction_at_entry:.0f}",
                            ],
                        )
                    else:
                        reserved = round(limit_px * qty, 2)
                        run.cash_balance = round(float(run.cash_balance) - reserved, 2)
                        trade = PaperTrade(
                            run_id=run.id,
                            symbol=sym,
                            scan_run_id=idea.get("scan_run_id"),
                            strategy_horizon="all",
                            direction=gate["direction"],
                            setup_name=str(idea.get("setup_name") or ""),
                            status="PENDING_ENTRY",
                            created_at=cur_dt,
                            submitted_at=cur_dt,
                            planned_entry_low=gate["entry_low"],
                            planned_entry_high=gate["entry_high"],
                            planned_limit_price=limit_px,
                            planned_stop_loss=gate["stop_loss"],
                            planned_tp1=gate["tp1"],
                            planned_tp2=gate["tp2"],
                            planned_rr=float(idea.get("risk_reward") or 0.0),
                            current_stop_loss=gate["stop_loss"],
                            atr_at_entry=atr_val,
                            quantity_planned=qty,
                            quantity_filled=0,
                            quantity_remaining=0,
                            risk_amount_usd=float(gate["risk_amount_usd"]),
                            ibkr_parent_order_id=ib_res.get("parent_order_id"),
                            ibkr_stop_order_id=ib_res.get("stop_order_id"),
                            ibkr_tp1_order_id=ib_res.get("tp1_order_id"),
                            ibkr_perm_ids_json=json.dumps(ib_res.get("perm_ids") or []),
                            conviction_at_entry=float(idea.get("conviction_score") or 0.0),
                            composite_score_at_entry=float(idea.get("composite_score") or 0.0),
                            market_regime_at_entry=market_regime,
                            sector_at_entry=str(idea.get("sector") or ""),
                            signals_at_entry_json=signals_json,
                            plugin_scores_at_entry_json=json.dumps(plugin_scores),
                        )
                        session.add(trade)
                        session.flush()
                        _log_event(
                            session,
                            run.id,
                            trade.id,
                            sym,
                            "ORDER_SUBMITTED",
                            f"[IBKR {du_acct}] Submitted Bracket {gate['direction']} {qty} {sym} LMT @ ${limit_px:.2f} (SL ${gate['stop_loss']:.2f}, TP1 ${gate['tp1']:.2f})",
                            {"parent_order_id": trade.ibkr_parent_order_id, "conviction": trade.conviction_at_entry},
                            created_at=cur_dt,
                        )
                        dispatch_paper_notification(
                            runtime,
                            "orders",
                            f"IBKR Bracket Submitted: {gate['direction']} {sym}",
                            [
                                f"Account: {du_acct} · Qty: {qty} LMT @ ${limit_px:.2f}",
                                f"SL: ${gate['stop_loss']:.2f} · TP1: ${gate['tp1']:.2f} · Conv: {trade.conviction_at_entry:.0f}",
                            ],
                        )
                elif is_immediate:
                    fill_px = round(ref_px, 2)
                    entry_notional = round(fill_px * qty, 2)
                    run.cash_balance = round(float(run.cash_balance) - entry_notional, 2)
                    risk_amt = round(abs(fill_px - float(gate["stop_loss"])) * qty, 2)
                    slip_bps = _compute_slippage_bps(gate["direction"], limit_px, fill_px)
                    trade = PaperTrade(
                        run_id=run.id,
                        symbol=sym,
                        scan_run_id=idea.get("scan_run_id"),
                        strategy_horizon="all",
                        direction=gate["direction"],
                        setup_name=str(idea.get("setup_name") or ""),
                        status="OPEN",
                        created_at=cur_dt,
                        submitted_at=cur_dt,
                        filled_at=cur_dt,
                        last_evaluated_bar_ts=int(cur_dt.timestamp()),
                        planned_entry_low=gate["entry_low"],
                        planned_entry_high=gate["entry_high"],
                        planned_limit_price=limit_px,
                        planned_stop_loss=gate["stop_loss"],
                        planned_tp1=gate["tp1"],
                        planned_tp2=gate["tp2"],
                        planned_rr=float(idea.get("risk_reward") or 0.0),
                        current_stop_loss=gate["stop_loss"],
                        atr_at_entry=atr_val,
                        quantity_planned=qty,
                        quantity_filled=qty,
                        quantity_remaining=qty,
                        entry_fill_price=fill_px,
                        last_mark_price=fill_px,
                        entry_slippage_bps=slip_bps,
                        risk_amount_usd=risk_amt,
                        unrealized_pnl_usd=0.0,
                        conviction_at_entry=float(idea.get("conviction_score") or 0.0),
                        composite_score_at_entry=float(idea.get("composite_score") or 0.0),
                        market_regime_at_entry=market_regime,
                        sector_at_entry=str(idea.get("sector") or ""),
                        signals_at_entry_json=signals_json,
                        plugin_scores_at_entry_json=json.dumps(plugin_scores),
                    )
                    session.add(trade)
                    session.flush()
                    _log_event(
                        session,
                        run.id,
                        trade.id,
                        sym,
                        "ORDER_FILLED",
                        f"Opened {gate['direction']} {qty} {sym} @ ${fill_px:.2f} (SL ${gate['stop_loss']:.2f}, TP1 ${gate['tp1']:.2f})",
                        {"conviction": trade.conviction_at_entry, "risk_usd": risk_amt, "slippage_bps": slip_bps},
                        created_at=cur_dt,
                    )
                    dispatch_paper_notification(
                        runtime,
                        "orders",
                        f"Virtual Trade Opened: {gate['direction']} {sym}",
                        [
                            f"Qty: {qty} @ ${fill_px:.2f} (Conv {trade.conviction_at_entry:.0f})",
                            f"SL: ${gate['stop_loss']:.2f} · TP1: ${gate['tp1']:.2f} · Risk ${risk_amt:.0f}",
                        ],
                    )
                else:
                    reserved = round(limit_px * qty, 2)
                    run.cash_balance = round(float(run.cash_balance) - reserved, 2)
                    trade = PaperTrade(
                        run_id=run.id,
                        symbol=sym,
                        scan_run_id=idea.get("scan_run_id"),
                        strategy_horizon="all",
                        direction=gate["direction"],
                        setup_name=str(idea.get("setup_name") or ""),
                        status="PENDING_ENTRY",
                        created_at=cur_dt,
                        submitted_at=cur_dt,
                        planned_entry_low=gate["entry_low"],
                        planned_entry_high=gate["entry_high"],
                        planned_limit_price=limit_px,
                        planned_stop_loss=gate["stop_loss"],
                        planned_tp1=gate["tp1"],
                        planned_tp2=gate["tp2"],
                        planned_rr=float(idea.get("risk_reward") or 0.0),
                        current_stop_loss=gate["stop_loss"],
                        atr_at_entry=atr_val,
                        quantity_planned=qty,
                        quantity_filled=0,
                        quantity_remaining=0,
                        risk_amount_usd=float(gate["risk_amount_usd"]),
                        conviction_at_entry=float(idea.get("conviction_score") or 0.0),
                        composite_score_at_entry=float(idea.get("composite_score") or 0.0),
                        market_regime_at_entry=market_regime,
                        sector_at_entry=str(idea.get("sector") or ""),
                        signals_at_entry_json=signals_json,
                        plugin_scores_at_entry_json=json.dumps(plugin_scores),
                    )
                    session.add(trade)
                    session.flush()
                    _log_event(
                        session,
                        run.id,
                        trade.id,
                        sym,
                        "ORDER_SUBMITTED",
                        f"Submitted {gate['direction']} limit {qty} {sym} @ ${limit_px:.2f} (SL ${gate['stop_loss']:.2f}, TP1 ${gate['tp1']:.2f})",
                        {"conviction": trade.conviction_at_entry, "reserved_usd": reserved},
                        created_at=cur_dt,
                    )
                    dispatch_paper_notification(
                        runtime,
                        "orders",
                        f"Virtual Limit Submitted: {gate['direction']} {sym}",
                        [
                            f"Qty: {qty} @ ${limit_px:.2f} (Conv {trade.conviction_at_entry:.0f})",
                            f"SL: ${gate['stop_loss']:.2f} · TP1: ${gate['tp1']:.2f}",
                        ],
                    )

                opened.append(
                    {
                        "id": trade.id,
                        "symbol": sym,
                        "direction": trade.direction,
                        "status": trade.status,
                        "quantity": qty,
                        "limit_price": limit_px,
                        "fill_price": trade.entry_fill_price,
                    }
                )

            if rejected:
                _log_event(
                    session,
                    run.id,
                    None,
                    "",
                    "GATE_SUMMARY",
                    f"Allocation cycle evaluated {len(candidates)} ideas: {len(opened)} opened, {len(rejected)} filtered out.",
                    {"rejected_sample": rejected[:10]},
                    created_at=cur_dt,
                )

            recompute_run_equity(session, run, now_utc=cur_dt)
            session.commit()
            return {
                "executed": True,
                "run_id": run.id,
                "opened": opened,
                "rejected": rejected,
                "lifecycle": lifecycle_summary,
                "current_equity": run.current_equity,
                "cash_balance": run.cash_balance,
            }
        except Exception:
            if own_session and ctx is not None:
                session.rollback()
            raise
        finally:
            if own_session and ctx is not None:
                ctx.__exit__(None, None, None)


def close_paper_trade_manually(
    session: Session,
    trade_id: int,
    *,
    price_override: float | None = None,
    now_utc: datetime | None = None,
) -> PaperTrade | None:
    cur_dt = as_utc(now_utc) or utcnow()
    trade = session.get(PaperTrade, trade_id)
    if trade is None:
        return None
    run = session.get(PaperAccountRun, trade.run_id)
    if run is None:
        return None
    runtime = load_runtime_settings(session)
    ibkr_cfg = runtime.get("ibkr", {})

    if trade.status == "PENDING_ENTRY":
        if run.mode == "ibkr_paper" and price_override is None and ibkr_cfg.get("enabled", False):
            from app.core.paper_ibkr import close_ibkr_trade

            close_ibkr_trade(
                symbol=trade.symbol,
                direction=trade.direction,
                quantity=int(trade.quantity_planned),
                parent_order_id=trade.ibkr_parent_order_id,
                stop_order_id=trade.ibkr_stop_order_id,
                tp1_order_id=trade.ibkr_tp1_order_id,
                is_pending=True,
                ibkr_cfg=ibkr_cfg,
                expected_account=run.ibkr_account,
            )
        reserved = round(float(trade.planned_limit_price) * int(trade.quantity_planned), 2)
        run.cash_balance = round(float(run.cash_balance) + reserved, 2)
        trade.status = "CANCELLED"
        trade.exit_reason = "MANUAL_CANCEL"
        trade.closed_at = cur_dt
        _log_event(
            session,
            run.id,
            trade.id,
            trade.symbol,
            "ORDER_CANCELLED",
            f"Manually cancelled pending {trade.symbol} entry order.",
            created_at=cur_dt,
        )
    elif trade.status in ("OPEN", "PARTIAL_TP1"):
        exit_px = price_override
        if exit_px is None and run.mode == "ibkr_paper" and ibkr_cfg.get("enabled", False):
            from app.core.paper_ibkr import close_ibkr_trade

            ib_res = close_ibkr_trade(
                symbol=trade.symbol,
                direction=trade.direction,
                quantity=int(trade.quantity_remaining or trade.quantity_filled),
                parent_order_id=trade.ibkr_parent_order_id,
                stop_order_id=trade.ibkr_stop_order_id,
                tp1_order_id=trade.ibkr_tp1_order_id,
                is_pending=False,
                ibkr_cfg=ibkr_cfg,
                expected_account=run.ibkr_account,
            )
            if ib_res.get("fill_price"):
                exit_px = float(ib_res["fill_price"])
        if exit_px is None:
            quotes = get_live_quotes_with_fallback([trade.symbol], ibkr_cfg)
            exit_px = _latest_mark_price(session, trade.symbol, quotes_map=quotes)
        if exit_px is None:
            exit_px = float(trade.last_mark_price or trade.entry_fill_price or trade.planned_limit_price)
        _close_trade(session, run, trade, float(exit_px), "MANUAL_CLOSE", cur_dt)
        dispatch_paper_notification(
            runtime,
            "orders",
            f"Manually Closed: {trade.symbol}",
            [f"Exit @ ${float(exit_px):.2f} | PnL ${trade.realized_pnl_usd:+.2f} ({trade.r_multiple:+.2f}R)"],
        )

    recompute_run_equity(session, run, now_utc=cur_dt)
    session.commit()
    return trade


def set_paper_kill_switch(
    session: Session,
    *,
    halt: bool,
    reason: str = "",
    close_all: bool = False,
    price_overrides: dict[str, float] | None = None,
    now_utc: datetime | None = None,
) -> PaperAccountRun:
    cur_dt = as_utc(now_utc) or utcnow()
    runtime = load_runtime_settings(session)
    run = ensure_active_paper_run(session, runtime, now_utc=cur_dt)
    if halt:
        run.kill_switch_triggered = True
        run.kill_switch_reason = reason or "Emergency stop triggered manually."
        _log_event(
            session,
            run.id,
            None,
            "",
            "KILL_SWITCH",
            run.kill_switch_reason,
            {"close_all": close_all},
            created_at=cur_dt,
        )
        if close_all:
            active = (
                session.query(PaperTrade)
                .filter(PaperTrade.run_id == run.id, PaperTrade.status.in_(("PENDING_ENTRY", "OPEN", "PARTIAL_TP1")))
                .all()
            )
            for t in active:
                px = (price_overrides or {}).get(t.symbol) if price_overrides else None
                close_paper_trade_manually(session, t.id, price_override=px, now_utc=cur_dt)
        dispatch_paper_notification(
            runtime,
            "kill_switch",
            "Emergency Kill-Switch Activated",
            [f"Run: {run.name}", f"Reason: {run.kill_switch_reason}", f"Closed all positions: {close_all}"],
        )
    else:
        run.kill_switch_triggered = False
        run.kill_switch_reason = ""
        run.high_water_mark = round(max(float(run.high_water_mark), float(run.current_equity)), 2)
        _log_event(
            session,
            run.id,
            None,
            "",
            "KILL_SWITCH_RESUMED",
            "Paper trading resumed manually.",
            created_at=cur_dt,
        )
        dispatch_paper_notification(
            runtime,
            "kill_switch",
            "Paper Trading Resumed",
            [f"Run: {run.name} resumed autonomous entries."],
        )

    recompute_run_equity(session, run, now_utc=cur_dt)
    session.commit()
    return run


def reset_paper_run(
    session: Session,
    *,
    name: str | None = None,
    initial_capital: float | None = None,
    now_utc: datetime | None = None,
) -> PaperAccountRun:
    """Close current active PaperAccountRun and start a fresh experiment run."""
    cur_dt = as_utc(now_utc) or utcnow()
    runtime = load_runtime_settings(session)
    eq = float(initial_capital or runtime.get("account", {}).get("equity", 25000.0) or 25000.0)
    mode = str(runtime.get("paper_trading", {}).get("mode", "virtual") or "virtual")

    active_runs = session.query(PaperAccountRun).filter(PaperAccountRun.is_active.is_(True)).all()
    for prev in active_runs:
        prev.is_active = False
        prev.ended_at = cur_dt

    total_runs = session.query(PaperAccountRun).count()
    run_name = (name or "").strip() or f"Experiment #{total_runs + 1} ({'IBKR Paper' if mode == 'ibkr_paper' else 'Virtual'})"
    new_run = PaperAccountRun(
        name=run_name,
        mode=mode,
        initial_capital=eq,
        current_equity=eq,
        cash_balance=eq,
        high_water_mark=eq,
        is_active=True,
        started_at=cur_dt,
        config_snapshot_json=json.dumps(runtime.get("paper_trading", {})),
    )
    session.add(new_run)
    session.flush()
    session.add(
        PaperEquitySnapshot(
            run_id=new_run.id,
            snapshot_date=cur_dt.date().isoformat(),
            equity=eq,
            cash=eq,
            open_positions_count=0,
            realized_pnl_cum=0.0,
            unrealized_pnl=0.0,
            drawdown_pct=0.0,
            spy_close=_latest_spy_close(session),
        )
    )
    _log_event(
        session,
        new_run.id,
        None,
        "",
        "RUN_STARTED",
        f"Started {run_name} with ${eq:,.2f} starting capital.",
        {"initial_capital": eq, "mode": mode},
        created_at=cur_dt,
    )
    session.commit()
    return new_run
