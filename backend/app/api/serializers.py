from __future__ import annotations

import json
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models import AnalysisSnapshot, ScanRun, Ticker, TickerNote, WatchlistItem


def latest_snapshot_subquery(session: Session):
    return (
        session.query(
            AnalysisSnapshot.symbol.label("sym"),
            func.max(AnalysisSnapshot.id).label("max_id"),
        )
        .group_by(AnalysisSnapshot.symbol)
        .subquery()
    )


def get_watchlist_membership_map(session: Session) -> dict[str, list[int]]:
    mapping: dict[str, list[int]] = {}
    for wl_id, sym in session.query(WatchlistItem.watchlist_id, WatchlistItem.symbol).all():
        mapping.setdefault(sym, []).append(wl_id)
    return mapping


def _normalize_trade_plan(raw_plan: dict[str, Any], spot_price: float | None = None) -> dict[str, Any]:
    """Ensure legacy TradePlan snapshots include computed v2/v3 gain, loss, spot R:R & ATR fields."""
    if not raw_plan or "entry_low" not in raw_plan:
        return {}
    plan = dict(raw_plan)
    entry_low = float(plan.get("entry_low") or 0.0)
    entry_high = float(plan.get("entry_high") or entry_low)
    mid = (entry_low + entry_high) / 2.0
    stop_loss = float(plan.get("stop_loss") or 0.0)
    tp1 = float(plan.get("tp1") or 0.0)
    tp2 = float(plan.get("tp2") or 0.0)
    atr = float(plan.get("atr") or 0.0)
    direction = str(plan.get("direction") or "NEUTRAL")

    if mid > 0:
        if plan.get("potential_gain_pct") is None:
            plan["potential_gain_pct"] = round(abs(tp2 - mid) / mid * 100.0, 2)
        if plan.get("tp1_gain_pct") is None:
            plan["tp1_gain_pct"] = round(abs(tp1 - mid) / mid * 100.0, 2)
        if plan.get("potential_loss_pct") is None:
            plan["potential_loss_pct"] = round(abs(mid - stop_loss) / mid * 100.0, 2)
        if plan.get("tp1_risk_reward") is None:
            mid_risk = abs(mid - stop_loss)
            plan["tp1_risk_reward"] = round(abs(tp1 - mid) / mid_risk, 2) if mid_risk > 0 else 0.0
    else:
        plan.setdefault("potential_gain_pct", 0.0)
        plan.setdefault("tp1_gain_pct", 0.0)
        plan.setdefault("potential_loss_pct", 0.0)
        plan.setdefault("tp1_risk_reward", 0.0)

    if spot_price and spot_price > 0 and stop_loss > 0:
        spot_risk = (stop_loss - spot_price) if direction == "SHORT" else (spot_price - stop_loss)
        if spot_risk > 0:
            r1 = max(0.0, (spot_price - tp1) if direction == "SHORT" else (tp1 - spot_price))
            r2 = max(0.0, (spot_price - tp2) if direction == "SHORT" else (tp2 - spot_price))
            if plan.get("spot_risk_pct") is None:
                plan["spot_risk_pct"] = round((spot_risk / spot_price) * 100.0, 2)
            if plan.get("spot_rr_tp1") is None:
                plan["spot_rr_tp1"] = round(r1 / spot_risk, 2)
            if plan.get("spot_rr_tp2") is None:
                plan["spot_rr_tp2"] = round(r2 / spot_risk, 2)
            if plan.get("stop_distance_atr") is None and atr > 0:
                plan["stop_distance_atr"] = round(spot_risk / atr, 2)

    plan.setdefault("spot_risk_pct", None)
    plan.setdefault("spot_rr_tp1", None)
    plan.setdefault("spot_rr_tp2", None)
    plan.setdefault("stop_distance_atr", None)
    plan.setdefault("target_crosses_barrier", False)
    plan.setdefault("index_aligned", False)
    plan.setdefault("index_trend", "neutral")
    plan.setdefault("dist_to_support_pct", None)
    plan.setdefault("dist_to_resistance_pct", None)
    return plan


def serialize_snapshot_summary(
    snap: AnalysisSnapshot,
    ticker: Ticker,
    preset_id: str,
    watchlist_ids: list[int],
) -> dict[str, Any]:
    preset_scores = json.loads(snap.preset_scores_json or "{}")
    signals = json.loads(snap.signals_json or "[]")
    risk_flags = json.loads(snap.risk_flags_json or "[]")
    events_insiders = json.loads(snap.events_insiders_json or "{}")
    trade_plan = _normalize_trade_plan(json.loads(snap.trade_plan_json or "{}"), spot_price=snap.last_price)

    score = float(preset_scores.get(preset_id, snap.composite_score))
    return {
        "symbol": snap.symbol,
        "name": ticker.name or snap.symbol,
        "sector": ticker.sector or "",
        "primary_benchmark": ticker.primary_benchmark,
        "sector_etf": ticker.sector_etf,
        "last_price": snap.last_price,
        "change_pct": snap.change_pct,
        "score": round(score, 1),
        "composite_score": snap.composite_score,
        "preset_scores": preset_scores,
        "bias": snap.bias,
        "trade_direction": trade_plan.get("direction", "NEUTRAL"),
        "risk_reward": float(trade_plan.get("risk_reward") or 0.0),
        "potential_gain_pct": float(trade_plan.get("potential_gain_pct") or 0.0),
        "potential_loss_pct": float(trade_plan.get("potential_loss_pct") or 0.0),
        "dist_to_support_pct": trade_plan.get("dist_to_support_pct"),
        "dist_to_resistance_pct": trade_plan.get("dist_to_resistance_pct"),
        "index_aligned": bool(trade_plan.get("index_aligned", False)),
        "index_trend": trade_plan.get("index_trend", "neutral"),
        "top_signals": signals[:3],
        "signal_codes": [s.get("code") for s in signals],
        "risk_flags": risk_flags,
        "days_to_earnings": events_insiders.get("days_to_earnings"),
        "watchlist_ids": watchlist_ids,
        "is_deep_enriched": snap.is_deep_enriched,
        "analyzed_at": snap.analyzed_at.isoformat() if snap.analyzed_at else None,
    }


def serialize_snapshot_detail(
    session: Session,
    snap: AnalysisSnapshot,
    ticker: Ticker,
) -> dict[str, Any]:
    note_row = session.get(TickerNote, ticker.symbol)
    wl_ids = [
        row[0]
        for row in session.query(WatchlistItem.watchlist_id)
        .filter(WatchlistItem.symbol == ticker.symbol)
        .all()
    ]
    return {
        "symbol": snap.symbol,
        "name": ticker.name or snap.symbol,
        "sector": ticker.sector or "",
        "industry": ticker.industry or "",
        "primary_benchmark": ticker.primary_benchmark,
        "sector_etf": ticker.sector_etf,
        "discovery_sources": json.loads(ticker.discovery_sources_json or "[]"),
        "last_price": snap.last_price,
        "change_pct": snap.change_pct,
        "composite_score": snap.composite_score,
        "preset_scores": json.loads(snap.preset_scores_json or "{}"),
        "bias": snap.bias,
        "signals": json.loads(snap.signals_json or "[]"),
        "risk_flags": json.loads(snap.risk_flags_json or "[]"),
        "overlays": json.loads(snap.overlays_json or "[]"),
        "trade_plan": _normalize_trade_plan(json.loads(snap.trade_plan_json or "{}"), spot_price=snap.last_price),
        "options": json.loads(snap.option_proposals_json or "{}"),
        "fundamentals": json.loads(snap.fundamentals_json or "{}"),
        "events_insiders": json.loads(snap.events_insiders_json or "{}"),
        "plugin_results": json.loads(snap.plugin_results_json or "{}"),
        "note_text": note_row.note_text if note_row else "",
        "tags": json.loads(note_row.tags_json or "[]") if note_row else [],
        "watchlist_ids": wl_ids,
        "is_deep_enriched": snap.is_deep_enriched,
        "analyzed_at": snap.analyzed_at.isoformat() if snap.analyzed_at else None,
    }


def get_latest_market_regime(session: Session) -> dict[str, Any] | None:
    runs = (
        session.query(ScanRun)
        .filter(ScanRun.market_regime_json != "{}")
        .order_by(ScanRun.id.desc())
        .limit(10)
        .all()
    )
    fallback_data: dict[str, Any] | None = None
    for run in runs:
        try:
            data = json.loads(run.market_regime_json or "{}")
            if not isinstance(data, dict) or not data:
                continue
            if fallback_data is None:
                fallback_data = data
            if data.get("benchmarks"):
                return data
        except json.JSONDecodeError:
            continue
    return fallback_data
