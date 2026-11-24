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
from app.core.trade_ideas import build_trade_ideas, get_cached_live_quotes_ibkr
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
    index_aligned_only: bool = Query(default=False),
    sort_by: str = Query(default="score", pattern="^(score|risk_reward|potential_gain|tight_entry)$"),
    search: str | None = Query(default=None),
    min_score: float | None = Query(default=None),
    limit: int = Query(default=250, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    runtime_settings = load_runtime_settings(db)
    presets = runtime_settings.get("presets", {})
    preset_meta = {
        k: {"name": v["name"], "description": v.get("description", ""), "weights": v.get("weights", {})}
        for k, v in presets.items()
    }
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
                "presets": preset_meta,
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
        if index_aligned_only and not item.get("index_aligned"):
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

    if sort_by == "risk_reward":
        items.sort(key=lambda x: (bool(x.get("index_aligned")), x.get("risk_reward", 0.0), x["score"]), reverse=True)
    elif sort_by == "potential_gain":
        items.sort(
            key=lambda x: (x.get("risk_reward", 0.0) >= 1.5, x.get("potential_gain_pct", 0.0), x.get("risk_reward", 0.0)),
            reverse=True,
        )
    elif sort_by == "tight_entry":
        items.sort(
            key=lambda x: (
                1 if x.get("risk_reward", 0.0) >= 1.5 else 0,
                -(x.get("potential_loss_pct") if x.get("potential_loss_pct", 0.0) > 0 else 999.0),
                x["score"],
            ),
            reverse=True,
        )
    else:
        items.sort(key=lambda x: x["score"], reverse=True)

    return {
        "preset": preset,
        "sort_by": sort_by,
        "presets": preset_meta,
        "total": len(items),
        "items": items[:limit],
        "market_regime": get_latest_market_regime(db),
        "last_scan": _serialize_last_scan(db),
    }


@router.get("/trade-ideas")
def get_trade_ideas(
    limit: int = Query(default=10, ge=1, le=50),
    horizon: str = Query(default="all", pattern="^(all|short_term|medium_term)$"),
    min_rr: float = Query(default=1.5, ge=0.5, le=10.0),
    exclude_earnings_days: int | None = Query(default=None, ge=0, le=60),
    index_aligned_only: bool = Query(default=False),
    watchlist_only: bool = Query(default=False),
    hide_conflicted: bool = Query(default=True),
    hide_stale: bool = Query(default=True),
    check_live_prices: bool = Query(default=True),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    live_fetcher = None
    if check_live_prices:
        runtime_settings = load_runtime_settings(db)
        ibkr_cfg = runtime_settings.get("ibkr", {})
        if ibkr_cfg.get("enabled", False):
            live_fetcher = lambda syms: get_cached_live_quotes_ibkr(syms, ibkr_cfg)

    data = build_trade_ideas(
        db,
        limit=limit,
        horizon=horizon,  # type: ignore[arg-type]
        min_rr=min_rr,
        exclude_earnings_days=exclude_earnings_days,
        index_aligned_only=index_aligned_only,
        watchlist_only=watchlist_only,
        hide_conflicted=hide_conflicted,
        hide_stale=hide_stale,
        live_quote_fetcher=live_fetcher,
    )
    data["last_scan"] = _serialize_last_scan(db)
    return data


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
