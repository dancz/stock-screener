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
        "trade_plan": json.loads(snap.trade_plan_json or "{}"),
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
    run = (
        session.query(ScanRun)
        .filter(ScanRun.market_regime_json != "{}")
        .order_by(ScanRun.id.desc())
        .first()
    )
    if not run:
        return None
    try:
        data = json.loads(run.market_regime_json or "{}")
        return data or None
    except json.JSONDecodeError:
        return None
