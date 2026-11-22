from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.serializers import (
    get_latest_market_regime,
    get_watchlist_membership_map,
    latest_snapshot_subquery,
    serialize_snapshot_summary,
)
from app.core.registry import registry
from app.db.database import get_db
from app.db.models import AnalysisSnapshot, ScanRun, Ticker, WatchlistItem, load_runtime_settings

router = APIRouter(tags=["screener"])


@router.get("/screener")
def get_screener(
    preset: str = Query(default="all_around_swing"),
    watchlist_id: int | None = Query(default=None),
    signal: str | None = Query(default=None),
    bias: str | None = Query(default=None),
    exclude_earnings_days: int | None = Query(default=None),
    search: str | None = Query(default=None),
    min_score: float | None = Query(default=None),
    limit: int = Query(default=250, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    runtime_settings = load_runtime_settings(db)
    presets = runtime_settings.get("presets", {})
    subq = latest_snapshot_subquery(db)

    query = (
        db.query(AnalysisSnapshot, Ticker)
        .join(subq, AnalysisSnapshot.id == subq.c.max_id)
        .join(Ticker, Ticker.symbol == AnalysisSnapshot.symbol)
    )

    if watchlist_id is not None:
        wl_syms = [
            row[0]
            for row in db.query(WatchlistItem.symbol).filter(WatchlistItem.watchlist_id == watchlist_id).all()
        ]
        if not wl_syms:
            return {
                "preset": preset,
                "presets": {k: {"name": v["name"], "description": v.get("description", "")} for k, v in presets.items()},
                "total": 0,
                "items": [],
                "market_regime": get_latest_market_regime(db),
                "last_scan": _serialize_last_scan(db),
            }
        query = query.filter(AnalysisSnapshot.symbol.in_(wl_syms))

    if bias and bias in ("bullish", "bearish", "neutral"):
        query = query.filter(AnalysisSnapshot.bias == bias)

    if search and search.strip():
        term = f"%{search.strip().upper()}%"
        query = query.filter((Ticker.symbol.ilike(term)) | (Ticker.name.ilike(term)))

    rows = query.all()
    wl_map = get_watchlist_membership_map(db)
    wanted_signals = {s.strip().upper() for s in signal.split(",") if s.strip()} if signal else set()

    items: list[dict[str, Any]] = []
    for snap, ticker in rows:
        item = serialize_snapshot_summary(snap, ticker, preset, wl_map.get(snap.symbol, []))
        if min_score is not None and item["score"] < min_score:
            continue
        if wanted_signals:
            item_codes = set(item["signal_codes"])
            if not (item_codes & wanted_signals):
                continue
        if exclude_earnings_days is not None:
            dte = item.get("days_to_earnings")
            if dte is not None and 0 <= dte <= exclude_earnings_days:
                continue
        items.append(item)

    items.sort(key=lambda x: x["score"], reverse=True)
    return {
        "preset": preset,
        "presets": {k: {"name": v["name"], "description": v.get("description", "")} for k, v in presets.items()},
        "total": len(items),
        "items": items[:limit],
        "market_regime": get_latest_market_regime(db),
        "last_scan": _serialize_last_scan(db),
    }


@router.get("/market-regime")
def get_market_regime(db: Session = Depends(get_db)) -> dict[str, Any]:
    return {"market_regime": get_latest_market_regime(db)}


@router.get("/plugins")
def list_plugins(db: Session = Depends(get_db)) -> dict[str, Any]:
    runtime_settings = load_runtime_settings(db)
    enabled_map = runtime_settings.get("enabled_plugins", {})
    return {"plugins": registry.describe_all(enabled_map)}


def _serialize_last_scan(db: Session) -> dict[str, Any] | None:
    run = db.query(ScanRun).order_by(ScanRun.id.desc()).first()
    if run is None:
        return None
    return {
        "id": run.id,
        "mode": run.mode,
        "status": run.status,
        "stage": run.stage,
        "total_tickers": run.total_tickers,
        "processed_tickers": run.processed_tickers,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }
