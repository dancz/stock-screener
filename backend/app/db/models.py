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
    UniqueConstraint,
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


class PaperAccountRun(Base):
    __tablename__ = "paper_account_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(80), default="Experiment #1 (Virtual)")
    mode: Mapped[str] = mapped_column(String(16), default="virtual")
    ibkr_account: Mapped[str] = mapped_column(String(24), default="")
    initial_capital: Mapped[float] = mapped_column(Float, default=25000.0)
    current_equity: Mapped[float] = mapped_column(Float, default=25000.0)
    cash_balance: Mapped[float] = mapped_column(Float, default=25000.0)
    high_water_mark: Mapped[float] = mapped_column(Float, default=25000.0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    kill_switch_triggered: Mapped[bool] = mapped_column(Boolean, default=False)
    kill_switch_reason: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    config_snapshot_json: Mapped[str] = mapped_column(Text, default="{}")

    trades: Mapped[list["PaperTrade"]] = relationship(back_populates="run", cascade="all, delete-orphan")
    events: Mapped[list["PaperOrderEvent"]] = relationship(back_populates="run", cascade="all, delete-orphan")
    equity_snapshots: Mapped[list["PaperEquitySnapshot"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class PaperTrade(Base):
    __tablename__ = "paper_trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("paper_account_runs.id", ondelete="CASCADE"), index=True
    )
    symbol: Mapped[str] = mapped_column(String(16), ForeignKey("tickers.symbol", ondelete="CASCADE"), index=True)
    scan_run_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("scan_runs.id", ondelete="SET NULL"), nullable=True
    )
    strategy_horizon: Mapped[str] = mapped_column(String(24), default="all")
    direction: Mapped[str] = mapped_column(String(8), default="LONG")
    setup_name: Mapped[str] = mapped_column(String(80), default="")
    status: Mapped[str] = mapped_column(String(24), default="PENDING_ENTRY", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    filled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    planned_entry_low: Mapped[float] = mapped_column(Float, default=0.0)
    planned_entry_high: Mapped[float] = mapped_column(Float, default=0.0)
    planned_limit_price: Mapped[float] = mapped_column(Float, default=0.0)
    planned_stop_loss: Mapped[float] = mapped_column(Float, default=0.0)
    planned_tp1: Mapped[float] = mapped_column(Float, default=0.0)
    planned_tp2: Mapped[float] = mapped_column(Float, default=0.0)
    planned_rr: Mapped[float] = mapped_column(Float, default=0.0)
    current_stop_loss: Mapped[float] = mapped_column(Float, default=0.0)
    atr_at_entry: Mapped[float] = mapped_column(Float, default=0.0)
    be_moved: Mapped[bool] = mapped_column(Boolean, default=False)
    trailing_active: Mapped[bool] = mapped_column(Boolean, default=False)
    last_evaluated_bar_ts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quantity_planned: Mapped[int] = mapped_column(Integer, default=0)
    quantity_filled: Mapped[int] = mapped_column(Integer, default=0)
    quantity_remaining: Mapped[int] = mapped_column(Integer, default=0)
    entry_fill_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_fill_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_mark_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    entry_slippage_bps: Mapped[float | None] = mapped_column(Float, nullable=True)
    commission_usd: Mapped[float] = mapped_column(Float, default=0.0)
    risk_amount_usd: Mapped[float] = mapped_column(Float, default=0.0)
    realized_pnl_usd: Mapped[float] = mapped_column(Float, default=0.0)
    realized_pnl_pct: Mapped[float] = mapped_column(Float, default=0.0)
    unrealized_pnl_usd: Mapped[float] = mapped_column(Float, default=0.0)
    r_multiple: Mapped[float] = mapped_column(Float, default=0.0)
    exit_reason: Mapped[str] = mapped_column(String(32), default="")
    ibkr_parent_order_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ibkr_stop_order_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ibkr_tp1_order_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ibkr_tp2_order_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ibkr_perm_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    conviction_at_entry: Mapped[float] = mapped_column(Float, default=0.0)
    composite_score_at_entry: Mapped[float] = mapped_column(Float, default=0.0)
    market_regime_at_entry: Mapped[str] = mapped_column(String(24), default="NEUTRAL")
    sector_at_entry: Mapped[str] = mapped_column(String(64), default="")
    signals_at_entry_json: Mapped[str] = mapped_column(Text, default="[]")
    plugin_scores_at_entry_json: Mapped[str] = mapped_column(Text, default="{}")

    run: Mapped["PaperAccountRun"] = relationship(back_populates="trades")


class PaperOrderEvent(Base):
    __tablename__ = "paper_order_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("paper_account_runs.id", ondelete="CASCADE"), index=True
    )
    trade_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("paper_trades.id", ondelete="CASCADE"), nullable=True, index=True
    )
    symbol: Mapped[str] = mapped_column(String(16), default="")
    event_type: Mapped[str] = mapped_column(String(32))
    message: Mapped[str] = mapped_column(Text, default="")
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    run: Mapped["PaperAccountRun"] = relationship(back_populates="events")


class PaperEquitySnapshot(Base):
    __tablename__ = "paper_equity_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("paper_account_runs.id", ondelete="CASCADE"), index=True
    )
    snapshot_date: Mapped[str] = mapped_column(String(10), index=True)
    equity: Mapped[float] = mapped_column(Float, default=0.0)
    cash: Mapped[float] = mapped_column(Float, default=0.0)
    open_positions_count: Mapped[int] = mapped_column(Integer, default=0)
    realized_pnl_cum: Mapped[float] = mapped_column(Float, default=0.0)
    unrealized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    drawdown_pct: Mapped[float] = mapped_column(Float, default=0.0)
    spy_close: Mapped[float | None] = mapped_column(Float, nullable=True)

    run: Mapped["PaperAccountRun"] = relationship(back_populates="equity_snapshots")

    __table_args__ = (UniqueConstraint("run_id", "snapshot_date", name="uq_paper_equity_run_date"),)


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
    _ensure_column(engine, "paper_trades", "trailing_active", "BOOLEAN DEFAULT 0")
    _ensure_column(engine, "paper_trades", "last_evaluated_bar_ts", "INTEGER")

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

        if session.query(PaperAccountRun).count() == 0:
            eq = float(defaults.get("account", {}).get("equity", 25000.0))
            run = PaperAccountRun(
                name="Experiment #1 (Virtual)",
                mode="virtual",
                initial_capital=eq,
                current_equity=eq,
                cash_balance=eq,
                high_water_mark=eq,
                is_active=True,
                config_snapshot_json=json.dumps(defaults.get("paper_trading", {})),
            )
            session.add(run)
            session.flush()
            session.add(
                PaperEquitySnapshot(
                    run_id=run.id,
                    snapshot_date=utcnow().date().isoformat(),
                    equity=eq,
                    cash=eq,
                    open_positions_count=0,
                    realized_pnl_cum=0.0,
                    unrealized_pnl=0.0,
                    drawdown_pct=0.0,
                )
            )

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
