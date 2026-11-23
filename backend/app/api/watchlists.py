from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import Ticker, Watchlist, WatchlistItem

router = APIRouter(prefix="/watchlists", tags=["watchlists"])


class WatchlistCreateBody(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    color: str = "#10b981"
    horizon: Literal["short_term", "medium_term", "general"] = "general"


class WatchlistUpdateBody(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    color: str | None = None
    horizon: Literal["short_term", "medium_term", "general"] | None = None


@router.get("")
def list_watchlists(db: Session = Depends(get_db)) -> dict[str, Any]:
    lists = db.query(Watchlist).order_by(Watchlist.sort_order.asc(), Watchlist.id.asc()).all()
    items = db.query(WatchlistItem).all()
    by_wl: dict[int, list[str]] = {}
    for item in items:
        by_wl.setdefault(item.watchlist_id, []).append(item.symbol)

    return {
        "watchlists": [
            {
                "id": wl.id,
                "name": wl.name,
                "color": wl.color,
                "horizon": wl.horizon or "general",
                "symbols": sorted(by_wl.get(wl.id, [])),
                "count": len(by_wl.get(wl.id, [])),
            }
            for wl in lists
        ]
    }


@router.post("", status_code=201)
def create_watchlist(body: WatchlistCreateBody, db: Session = Depends(get_db)) -> dict[str, Any]:
    name = body.name.strip()
    existing = db.query(Watchlist).filter(Watchlist.name.ilike(name)).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"Watchlist '{name}' already exists.")
    max_order = db.query(Watchlist).count()
    wl = Watchlist(name=name, color=body.color, horizon=body.horizon, sort_order=max_order)
    db.add(wl)
    db.commit()
    db.refresh(wl)
    return {"id": wl.id, "name": wl.name, "color": wl.color, "horizon": wl.horizon, "symbols": [], "count": 0}


@router.patch("/{watchlist_id}")
def update_watchlist(watchlist_id: int, body: WatchlistUpdateBody, db: Session = Depends(get_db)) -> dict[str, Any]:
    wl = db.get(Watchlist, watchlist_id)
    if wl is None:
        raise HTTPException(status_code=404, detail="Watchlist not found.")
    if body.name is not None:
        new_name = body.name.strip()
        dup = db.query(Watchlist).filter(Watchlist.name.ilike(new_name), Watchlist.id != watchlist_id).first()
        if dup:
            raise HTTPException(status_code=409, detail=f"Watchlist '{new_name}' already exists.")
        wl.name = new_name
    if body.color is not None:
        wl.color = body.color
    if body.horizon is not None:
        wl.horizon = body.horizon
    db.commit()
    symbols = [r[0] for r in db.query(WatchlistItem.symbol).filter(WatchlistItem.watchlist_id == wl.id).all()]
    return {
        "id": wl.id,
        "name": wl.name,
        "color": wl.color,
        "horizon": wl.horizon or "general",
        "symbols": sorted(symbols),
        "count": len(symbols),
    }


@router.delete("/{watchlist_id}")
def delete_watchlist(watchlist_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    wl = db.get(Watchlist, watchlist_id)
    if wl is None:
        raise HTTPException(status_code=404, detail="Watchlist not found.")
    db.delete(wl)
    db.commit()
    return {"deleted": True, "id": watchlist_id}


@router.post("/{watchlist_id}/items/{symbol}")
def add_watchlist_item(watchlist_id: int, symbol: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    wl = db.get(Watchlist, watchlist_id)
    if wl is None:
        raise HTTPException(status_code=404, detail="Watchlist not found.")
    sym = symbol.strip().upper().replace(".", "-")
    if not sym:
        raise HTTPException(status_code=400, detail="Invalid ticker symbol.")

    if db.get(Ticker, sym) is None:
        db.add(Ticker(symbol=sym, name=sym, is_custom=True))
        db.flush()

    existing = db.get(WatchlistItem, (watchlist_id, sym))
    if existing is None:
        db.add(WatchlistItem(watchlist_id=watchlist_id, symbol=sym))
        db.commit()

    symbols = [r[0] for r in db.query(WatchlistItem.symbol).filter(WatchlistItem.watchlist_id == watchlist_id).all()]
    return {"id": wl.id, "symbol": sym, "symbols": sorted(symbols), "count": len(symbols)}


@router.delete("/{watchlist_id}/items/{symbol}")
def remove_watchlist_item(watchlist_id: int, symbol: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    wl = db.get(Watchlist, watchlist_id)
    if wl is None:
        raise HTTPException(status_code=404, detail="Watchlist not found.")
    sym = symbol.strip().upper().replace(".", "-")
    item = db.get(WatchlistItem, (watchlist_id, sym))
    if item is not None:
        db.delete(item)
        db.commit()

    symbols = [r[0] for r in db.query(WatchlistItem.symbol).filter(WatchlistItem.watchlist_id == watchlist_id).all()]
    return {"id": wl.id, "symbol": sym, "symbols": sorted(symbols), "count": len(symbols)}
