from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.serializers import serialize_snapshot_detail
from app.config import get_active_history_periods
from app.core.pipeline import enrich_single_ticker, load_candles_df, upsert_candles
from app.db.database import get_db
from app.db.models import AnalysisSnapshot, Ticker, TickerNote, load_runtime_settings, utcnow
from app.plugins.sources.market_data_ibkr import fetch_live_quote_ibkr
from app.plugins.sources.market_data_yahoo import (
    fetch_intraday_1h,
    fetch_live_quote,
    fetch_weekly_bars,
    resample_daily_to_weekly,
)

router = APIRouter(prefix="/stocks", tags=["stocks"])


class NoteUpdateBody(BaseModel):
    note_text: str = ""
    tags: list[str] = Field(default_factory=list)


@router.get("/{symbol}/quote")
def get_live_stock_quote(symbol: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    sym = symbol.strip().upper().replace(".", "-")
    runtime = load_runtime_settings(db)
    ibkr_cfg = runtime.get("ibkr", {})

    quote: dict[str, Any] | None = None
    if ibkr_cfg.get("enabled", False):
        quote = fetch_live_quote_ibkr(
            symbol=sym,
            host=str(ibkr_cfg.get("host", "127.0.0.1")),
            port=int(ibkr_cfg.get("port", 4002)),
            client_id=int(ibkr_cfg.get("client_id", 17)),
        )

    if quote is None:
        quote = fetch_live_quote(sym)

    if quote is None:
        raise HTTPException(status_code=404, detail=f"Live quote unavailable for {sym}.")
    return quote


@router.get("/{symbol}")
def get_stock_detail(
    symbol: str,
    enrich: bool = Query(default=True),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    sym = symbol.strip().upper().replace(".", "-")
    snap = (
        db.query(AnalysisSnapshot)
        .filter(AnalysisSnapshot.symbol == sym)
        .order_by(AnalysisSnapshot.id.desc())
        .first()
    )

    if (snap is None or not snap.is_deep_enriched) and enrich:
        enriched = enrich_single_ticker(sym, force_refresh=(snap is None))
        if enriched is not None:
            db.expire_all()
            snap = (
                db.query(AnalysisSnapshot)
                .filter(AnalysisSnapshot.symbol == sym)
                .order_by(AnalysisSnapshot.id.desc())
                .first()
            )

    if snap is None:
        raise HTTPException(status_code=404, detail=f"No market data found for ticker {sym}.")

    ticker = db.get(Ticker, sym)
    if ticker is None:
        raise HTTPException(status_code=404, detail=f"Ticker {sym} not found.")

    return serialize_snapshot_detail(db, snap, ticker)


@router.get("/{symbol}/candles")
def get_stock_candles(
    symbol: str,
    timeframe: str = Query(default="1d", pattern="^(1w|1d|1h)$"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    sym = symbol.strip().upper().replace(".", "-")
    runtime = load_runtime_settings(db)
    _, intraday_period, weekly_period = get_active_history_periods(runtime)

    df = load_candles_df(db, sym, timeframe)
    if df.empty and timeframe == "1h":
        fetched = fetch_intraday_1h(sym, period=intraday_period)
        if not fetched.empty:
            if db.get(Ticker, sym) is None:
                db.add(Ticker(symbol=sym, name=sym, is_custom=True))
                db.flush()
            upsert_candles(db, sym, "1h", fetched)
            db.commit()
            df = fetched
    elif df.empty and timeframe == "1w":
        fetched = fetch_weekly_bars(sym, period=weekly_period)
        if fetched.empty:
            daily_df = load_candles_df(db, sym, "1d")
            if not daily_df.empty:
                fetched = resample_daily_to_weekly(daily_df)
        if not fetched.empty:
            if db.get(Ticker, sym) is None:
                db.add(Ticker(symbol=sym, name=sym, is_custom=True))
                db.flush()
            upsert_candles(db, sym, "1w", fetched)
            db.commit()
            df = fetched

    candles = [
        {
            "time": int(row.ts),
            "open": round(float(row.open), 4),
            "high": round(float(row.high), 4),
            "low": round(float(row.low), 4),
            "close": round(float(row.close), 4),
            "volume": float(row.volume),
        }
        for row in df.itertuples(index=False)
    ]
    return {
        "symbol": sym,
        "timeframe": timeframe,
        "bar_count": len(candles),
        "first_ts": candles[0]["time"] if candles else None,
        "last_ts": candles[-1]["time"] if candles else None,
        "candles": candles,
    }


@router.post("/{symbol}/refresh")
def refresh_stock(symbol: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    sym = symbol.strip().upper().replace(".", "-")
    res = enrich_single_ticker(sym, force_refresh=True)
    if res is None:
        raise HTTPException(status_code=404, detail=f"Unable to fetch market data for ticker {sym}.")

    db.expire_all()
    snap = (
        db.query(AnalysisSnapshot)
        .filter(AnalysisSnapshot.symbol == sym)
        .order_by(AnalysisSnapshot.id.desc())
        .first()
    )
    ticker = db.get(Ticker, sym)
    if snap is None or ticker is None:
        raise HTTPException(status_code=404, detail=f"Snapshot unavailable for {sym}.")
    return serialize_snapshot_detail(db, snap, ticker)


@router.put("/{symbol}/notes")
def update_stock_notes(symbol: str, body: NoteUpdateBody, db: Session = Depends(get_db)) -> dict[str, Any]:
    sym = symbol.strip().upper().replace(".", "-")
    if db.get(Ticker, sym) is None:
        db.add(Ticker(symbol=sym, name=sym, is_custom=True))
        db.flush()

    clean_tags = [t.strip() for t in dict.fromkeys(body.tags) if t.strip()]
    note = db.get(TickerNote, sym)
    if note is None:
        note = TickerNote(symbol=sym, note_text=body.note_text, tags_json=json.dumps(clean_tags), updated_at=utcnow())
        db.add(note)
    else:
        note.note_text = body.note_text
        note.tags_json = json.dumps(clean_tags)
        note.updated_at = utcnow()
    db.commit()
    return {"symbol": sym, "note_text": note.note_text, "tags": clean_tags}
