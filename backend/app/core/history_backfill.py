from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func

from app.config import get_active_history_periods
from app.core.pipeline import upsert_candles
from app.core.request_queue import Priority
from app.db.database import session_scope
from app.db.models import Candle, Ticker, WatchlistItem, load_runtime_settings, utcnow
from app.plugins.sources.market_data_ibkr import (
    fetch_historical_bars_ibkr,
    is_gateway_reachable,
    period_to_ibkr_duration,
)
from app.plugins.sources.market_data_yahoo import (
    fetch_daily_batch,
    fetch_intraday_1h,
    fetch_weekly_bars,
    resample_daily_to_weekly,
)

logger = logging.getLogger(__name__)

_backfill_lock = threading.Lock()
_backfill_status: dict[str, Any] = {
    "running": False,
    "profile": "dev",
    "total_tickers": 0,
    "hydrated_tickers": 0,
    "last_batch_symbols": [],
    "last_run_at": None,
    "last_source": None,
    "message": "Idle",
}


def get_backfill_status() -> dict[str, Any]:
    with _backfill_lock:
        status = dict(_backfill_status)
    try:
        with session_scope() as session:
            runtime = load_runtime_settings(session)
            status["profile"] = runtime.get("history", {}).get("profile", "dev")
            total = session.query(Ticker).count()
            hydrated = session.query(Ticker).filter(Ticker.history_backfilled_at.is_not(None)).count()
            status["total_tickers"] = total
            status["hydrated_tickers"] = hydrated
    except Exception:
        pass
    return status


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _period_to_ibkr_duration(period: str, fallback: str) -> str:
    p = period.strip().lower()
    if p.endswith("y"):
        return f"{p[:-1]} Y"
    if p.endswith("d"):
        days = int(p[:-1]) if p[:-1].isdigit() else 120
        return f"{max(1, round(days / 365))} Y" if days >= 365 else f"{days} D"
    return fallback


def run_history_backfill_batch(max_symbols: int | None = None, force: bool = False) -> dict[str, Any]:
    """Incrementally backfill/refresh extended 1D, 1W, and 1H bars for stored tickers."""
    with _backfill_lock:
        if _backfill_status["running"]:
            return dict(_backfill_status)
        _backfill_status.update({"running": True, "message": "Selecting next history backfill batch..."})

    processed_syms: list[str] = []
    used_sources: set[str] = set()

    try:
        with session_scope() as session:
            runtime = load_runtime_settings(session)
            hist_cfg = runtime.get("history", {})
            ibkr_cfg = runtime.get("ibkr", {})
            profile = str(hist_cfg.get("profile", "dev"))
            batch_size = max_symbols or int(hist_cfg.get("backfill_batch_size", 5))
            daily_period, intraday_period, weekly_period = get_active_history_periods(runtime)

            if not force and profile != "production" and not hist_cfg.get("backfill_enabled", False):
                _backfill_status.update({"running": False, "profile": profile, "message": "Backfill paused in dev mode."})
                return get_backfill_status()

            watchlist_syms = {row[0] for row in session.query(WatchlistItem.symbol).all()}
            stale_cutoff = utcnow() - timedelta(hours=12)

            # Prioritize: 1) Watchlist tickers not yet backfilled, 2) Un-backfilled tickers, 3) Oldest stale tickers
            all_tickers = session.query(Ticker).all()
            candidates: list[tuple[int, float, Ticker]] = []
            for tk in all_tickers:
                if tk.symbol.startswith("^"):
                    continue
                is_wl = 0 if tk.symbol in watchlist_syms else 1
                hb_dt = _as_utc(tk.history_backfilled_at)
                if hb_dt is None:
                    candidates.append((is_wl, 0.0, tk))
                elif force or hb_dt < stale_cutoff:
                    candidates.append((is_wl + 2, hb_dt.timestamp(), tk))

            candidates.sort(key=lambda item: (item[0], item[1]))
            selected = [item[2] for item in candidates[:batch_size]]

            if not selected:
                _backfill_status.update(
                    {
                        "running": False,
                        "profile": profile,
                        "last_run_at": datetime.now(timezone.utc).isoformat(),
                        "message": "All tracked tickers are up to date.",
                    }
                )
                return get_backfill_status()

            use_ibkr = (
                bool(ibkr_cfg.get("enabled", False))
                and bool(hist_cfg.get("prefer_ibkr_history", True))
                and is_gateway_reachable(str(ibkr_cfg.get("host", "127.0.0.1")), int(ibkr_cfg.get("port", 4002)))
            )
            host = str(ibkr_cfg.get("host", "127.0.0.1"))
            port = int(ibkr_cfg.get("port", 4002))
            client_id = int(ibkr_cfg.get("client_id", 17))

            min_deep_bars = 900 if profile == "production" else 400

            for idx, tk in enumerate(selected):
                sym = tk.symbol
                _backfill_status["message"] = f"Hydrating history for {sym} ({idx + 1}/{len(selected)})..."
                existing_1d_count = (
                    session.query(func.count(Candle.ts))
                    .filter(Candle.symbol == sym, Candle.timeframe == "1d")
                    .scalar()
                    or 0
                )
                need_deep = tk.history_backfilled_at is None or existing_1d_count < min_deep_bars

                d_period = daily_period if need_deep else "1mo"
                h_period = intraday_period if need_deep else "14d"

                # 1. Daily bars
                df_1d = None
                if use_ibkr:
                    dur = period_to_ibkr_duration(d_period, "5 Y") if need_deep else "1 M"
                    df_1d = fetch_historical_bars_ibkr(
                        sym, "1d", dur, host, port, client_id, priority=Priority.LOW, max_wait=15.0
                    )
                    if df_1d is not None and not df_1d.empty:
                        used_sources.add("IBKR")

                if df_1d is None or df_1d.empty:
                    fetched = fetch_daily_batch([sym], period=d_period, chunk_size=1, priority=Priority.LOW)
                    df_1d = fetched.get(sym)
                    if df_1d is not None and not df_1d.empty:
                        used_sources.add("Yahoo")

                if df_1d is not None and not df_1d.empty:
                    upsert_candles(session, sym, "1d", df_1d)

                # 2. Weekly bars (on initial deep backfill or missing weekly cache)
                existing_1w_count = (
                    session.query(func.count(Candle.ts))
                    .filter(Candle.symbol == sym, Candle.timeframe == "1w")
                    .scalar()
                    or 0
                )
                if existing_1w_count < 100 or need_deep:
                    df_1w = fetch_weekly_bars(sym, period=weekly_period, priority=Priority.LOW)
                    if (df_1w is None or df_1w.empty) and df_1d is not None and not df_1d.empty:
                        df_1w = resample_daily_to_weekly(df_1d)
                    if df_1w is not None and not df_1w.empty:
                        upsert_candles(session, sym, "1w", df_1w)

                # 3. 1H intraday bars
                df_1h = None
                if use_ibkr and need_deep:
                    df_1h = fetch_historical_bars_ibkr(
                        sym,
                        "1h",
                        period_to_ibkr_duration(h_period, "1 Y"),
                        host,
                        port,
                        client_id,
                        priority=Priority.LOW,
                        max_wait=15.0,
                    )
                if df_1h is None or df_1h.empty:
                    df_1h = fetch_intraday_1h(sym, period=h_period, priority=Priority.LOW)
                if df_1h is not None and not df_1h.empty:
                    upsert_candles(session, sym, "1h", df_1h)

                tk.history_backfilled_at = utcnow()
                processed_syms.append(sym)
                session.commit()

        src_label = " + ".join(sorted(used_sources)) if used_sources else "None"
        _backfill_status.update(
            {
                "running": False,
                "last_batch_symbols": processed_syms,
                "last_run_at": datetime.now(timezone.utc).isoformat(),
                "last_source": src_label,
                "message": f"Hydrated {', '.join(processed_syms)} via {src_label}",
            }
        )
    except Exception as exc:
        logger.warning("History backfill batch error: %s", exc)
        _backfill_status.update({"running": False, "message": f"Backfill error: {exc}"})

    return get_backfill_status()
