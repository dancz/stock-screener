from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.serializers import serialize_snapshot_detail
from app.core.pipeline import enrich_single_ticker, load_candles_df, upsert_candles
from app.db.database import get_db
from app.db.models import AnalysisSnapshot, Ticker, TickerNote, utcnow
from app.plugins.sources.market_data_yahoo import fetch_intraday_1h

router = APIRouter(prefix="/stocks", tags=["stocks"])


class NoteUpdateBody(BaseModel):
    note_text: str = ""
    tags: list[str] = Field(default_factory=list)


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
    timeframe: str = Query(default="1d", pattern="^(1d|1h)$"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    sym = symbol.strip().upper().replace(".", "-")
    df = load_candles_df(db, sym, timeframe)
    if df.empty and timeframe == "1h":
        fetched = fetch_intraday_1h(sym, period="60d")
        if not fetched.empty:
            if db.get(Ticker, sym) is None:
                db.add(Ticker(symbol=sym, name=sym, is_custom=True))
                db.flush()
            upsert_candles(db, sym, "1h", fetched)
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
    return {"symbol": sym, "timeframe": timeframe, "candles": candles}


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
