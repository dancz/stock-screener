from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship

from app.config import get_default_runtime_settings
from app.db.database import Base, get_engine


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Ticker(Base):
    __tablename__ = "tickers"

    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    sector: Mapped[str] = mapped_column(String(64), default="")
    industry: Mapped[str] = mapped_column(String(128), default="")
    primary_benchmark: Mapped[str] = mapped_column(String(16), default="SPY")
    sector_etf: Mapped[str] = mapped_column(String(16), default="SPY")
    discovery_sources_json: Mapped[str] = mapped_column(Text, default="[]")
    is_custom: Mapped[bool] = mapped_column(Boolean, default=False)
    last_enriched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    history_backfilled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    candles: Mapped[list["Candle"]] = relationship(back_populates="ticker", cascade="all, delete-orphan")
    snapshots: Mapped[list["AnalysisSnapshot"]] = relationship(back_populates="ticker", cascade="all, delete-orphan")


class Candle(Base):
    __tablename__ = "candles"

    symbol: Mapped[str] = mapped_column(String(16), ForeignKey("tickers.symbol", ondelete="CASCADE"), primary_key=True)
    timeframe: Mapped[str] = mapped_column(String(8), primary_key=True)  # "1d" | "1h"
    ts: Mapped[int] = mapped_column(Integer, primary_key=True)  # UTC unix epoch seconds
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float, default=0.0)

    ticker: Mapped["Ticker"] = relationship(back_populates="candles")

    __table_args__ = (Index("ix_candles_symbol_tf_ts", "symbol", "timeframe", "ts"),)


class ScanRun(Base):
    __tablename__ = "scan_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mode: Mapped[str] = mapped_column(String(32), default="full")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="running")  # running | completed | failed
    stage: Mapped[str] = mapped_column(String(64), default="initializing")
    total_tickers: Mapped[int] = mapped_column(Integer, default=0)
    processed_tickers: Mapped[int] = mapped_column(Integer, default=0)
    market_regime_json: Mapped[str] = mapped_column(Text, default="{}")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class AnalysisSnapshot(Base):
    __tablename__ = "analysis_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scan_run_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("scan_runs.id", ondelete="SET NULL"), nullable=True)
    symbol: Mapped[str] = mapped_column(String(16), ForeignKey("tickers.symbol", ondelete="CASCADE"), index=True)
    analyzed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    last_price: Mapped[float] = mapped_column(Float, default=0.0)
    change_pct: Mapped[float] = mapped_column(Float, default=0.0)
    composite_score: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    bias: Mapped[str] = mapped_column(String(16), default="neutral")
    preset_scores_json: Mapped[str] = mapped_column(Text, default="{}")
    plugin_results_json: Mapped[str] = mapped_column(Text, default="{}")
    signals_json: Mapped[str] = mapped_column(Text, default="[]")
    risk_flags_json: Mapped[str] = mapped_column(Text, default="[]")
    overlays_json: Mapped[str] = mapped_column(Text, default="[]")
    trade_plan_json: Mapped[str] = mapped_column(Text, default="{}")
    option_proposals_json: Mapped[str] = mapped_column(Text, default="[]")
    fundamentals_json: Mapped[str] = mapped_column(Text, default="{}")
    events_insiders_json: Mapped[str] = mapped_column(Text, default="{}")
    is_deep_enriched: Mapped[bool] = mapped_column(Boolean, default=False)

    ticker: Mapped["Ticker"] = relationship(back_populates="snapshots")


class Watchlist(Base):
    __tablename__ = "watchlists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    color: Mapped[str] = mapped_column(String(24), default="#10b981")
    horizon: Mapped[str] = mapped_column(String(24), default="general")  # short_term | medium_term | general
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    items: Mapped[list["WatchlistItem"]] = relationship(back_populates="watchlist", cascade="all, delete-orphan")


class WatchlistItem(Base):
    __tablename__ = "watchlist_items"

    watchlist_id: Mapped[int] = mapped_column(Integer, ForeignKey("watchlists.id", ondelete="CASCADE"), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16), ForeignKey("tickers.symbol", ondelete="CASCADE"), primary_key=True)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    watchlist: Mapped["Watchlist"] = relationship(back_populates="items")


class TickerNote(Base):
    __tablename__ = "ticker_notes"

    symbol: Mapped[str] = mapped_column(String(16), ForeignKey("tickers.symbol", ondelete="CASCADE"), primary_key=True)
    note_text: Mapped[str] = mapped_column(Text, default="")
    tags_json: Mapped[str] = mapped_column(Text, default="[]")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value_json: Mapped[str] = mapped_column(Text, default="{}")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


def _ensure_column(engine: Engine, table_name: str, column_name: str, ddl_type: str) -> None:
    """Idempotently add missing columns to existing SQLite databases."""
    with engine.begin() as conn:
        cols = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table_name})")).fetchall()}
        if column_name not in cols:
            conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {ddl_type}"))


def init_db(db_session: Session | None = None) -> None:
    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    _ensure_column(engine, "tickers", "history_backfilled_at", "DATETIME")
    _ensure_column(engine, "watchlists", "horizon", "VARCHAR(24) DEFAULT 'general'")

    from app.db.database import SessionLocal

    own_session = db_session is None
    session = db_session or SessionLocal()
    try:
        defaults = get_default_runtime_settings()
        existing_keys = {row.key for row in session.query(AppSetting.key).all()}
        for key, value in defaults.items():
            if key not in existing_keys:
                session.add(AppSetting(key=key, value_json=json.dumps(value)))

        # Seed starter watchlists (short-term, medium-term, general) if none exist
        if session.query(Watchlist).count() == 0:
            starter_lists = [
                ("Short-Term Trades", "#f59e0b", "short_term", 0, ["NVDA", "META", "AAPL"]),
                ("Medium-Term Investing", "#38bdf8", "medium_term", 1, ["MSFT", "AAPL", "JPM"]),
                ("Core Swing", "#10b981", "general", 2, ["AAPL", "NVDA", "MSFT", "META", "JPM"]),
            ]
            seed_meta = {
                "AAPL": ("Apple Inc.", "Information Technology", "XLK", "QQQ"),
                "NVDA": ("NVIDIA Corp.", "Information Technology", "XLK", "QQQ"),
                "MSFT": ("Microsoft Corp.", "Information Technology", "XLK", "QQQ"),
                "META": ("Meta Platforms Inc.", "Communication Services", "XLC", "QQQ"),
                "JPM": ("JPMorgan Chase & Co.", "Financials", "XLF", "SPY"),
            }
            for sym, (name, sector, etf, bench) in seed_meta.items():
                if session.get(Ticker, sym) is None:
                    session.add(
                        Ticker(
                            symbol=sym,
                            name=name,
                            sector=sector,
                            primary_benchmark=bench,
                            sector_etf=etf,
                            discovery_sources_json=json.dumps(["watchlist"]),
                        )
                    )
            session.flush()
            for wl_name, wl_color, wl_horizon, wl_order, wl_syms in starter_lists:
                wl = Watchlist(name=wl_name, color=wl_color, horizon=wl_horizon, sort_order=wl_order)
                session.add(wl)
                session.flush()
                for sym in wl_syms:
                    session.add(WatchlistItem(watchlist_id=wl.id, symbol=sym))

        session.commit()
    finally:
        if own_session:
            session.close()


def load_runtime_settings(session: Session) -> dict[str, Any]:
    defaults = get_default_runtime_settings()
    rows = session.query(AppSetting).all()
    for row in rows:
        try:
            parsed = json.loads(row.value_json)
            if isinstance(parsed, dict) and isinstance(defaults.get(row.key), dict):
                merged = copy_merge_dict(defaults[row.key], parsed)
                defaults[row.key] = merged
            else:
                defaults[row.key] = parsed
        except json.JSONDecodeError:
            continue
    return defaults


def copy_merge_dict(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = copy_merge_dict(result[k], v)
        else:
            result[k] = v
    return result


def save_runtime_settings(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    current = load_runtime_settings(session)
    for key, val in payload.items():
        if key not in current:
            continue
        if isinstance(val, dict) and isinstance(current[key], dict):
            merged = copy_merge_dict(current[key], val)
        else:
            merged = val
        row = session.get(AppSetting, key)
        if row is None:
            session.add(AppSetting(key=key, value_json=json.dumps(merged)))
        else:
            row.value_json = json.dumps(merged)
            row.updated_at = utcnow()
    session.commit()
    return load_runtime_settings(session)
