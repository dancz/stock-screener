from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Literal

import pandas as pd
from sqlalchemy import func, text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.config import get_active_history_periods
from app.core.contracts import (
    AnalysisContext,
    AnalyzerResult,
    DiscoveredTicker,
    MarketRegime,
    OptionAnalysisContext,
)
from app.core.market_regime import BENCHMARKS, SECTORS, compute_market_regime
from app.core.registry import registry
from app.core.request_queue import Priority, get_queue_stats
from app.core.scoring import compute_scores_and_summary
from app.core.trade_planner import build_trade_plan
from app.db.database import session_scope
from app.db.models import (
    AnalysisSnapshot,
    Candle,
    PaperTrade,
    ScanRun,
    Ticker,
    WatchlistItem,
    load_runtime_settings,
    utcnow,
)
from app.plugins.sources.finnhub_feed import fetch_finnhub_enrichment
from app.plugins.sources.market_data_ibkr import (
    fetch_historical_bars_ibkr,
    fetch_option_chain_with_greeks,
    is_gateway_reachable,
    period_to_ibkr_duration,
)
from app.plugins.sources.market_data_yahoo import (
    fetch_daily_batch,
    fetch_fundamentals_and_events,
    fetch_intraday_1h,
    fetch_option_chain,
)
from app.plugins.sources.openinsider_feed import fetch_openinsider_map
from app.plugins.sources.sec_edgar_form4 import fetch_sec_form4_filings
from app.plugins.sources.universe_sp500_ndx import (
    FALLBACK_UNIVERSE,
    QUICK_LEADERS,
    get_primary_benchmark,
    map_sector_to_etf,
)

logger = logging.getLogger(__name__)

_scan_lock = threading.Lock()
_scan_status: dict[str, Any] = {
    "running": False,
    "scan_run_id": None,
    "mode": None,
    "stage": "idle",
    "processed": 0,
    "total": 0,
    "message": "Idle",
    "started_at": None,
    "finished_at": None,
    "error": None,
}


def get_scan_status() -> dict[str, Any]:
    with _scan_lock:
        status = dict(_scan_status)
    status["queues"] = get_queue_stats()
    return status


def _resolve_ibkr_cfg(runtime_settings: dict[str, Any] | None) -> dict[str, Any] | None:
    if not runtime_settings:
        return None
    ibkr_cfg = runtime_settings.get("ibkr", {})
    if not ibkr_cfg.get("enabled", False):
        return None
    host = str(ibkr_cfg.get("host", "127.0.0.1"))
    port = int(ibkr_cfg.get("port", 4002))
    client_id = int(ibkr_cfg.get("client_id", 17))
    if not is_gateway_reachable(host, port, timeout=0.25):
        return None
    return {"host": host, "port": port, "client_id": client_id}


def _update_scan_status(**kwargs: Any) -> None:
    with _scan_lock:
        _scan_status.update(kwargs)


def upsert_candles(session: Session, symbol: str, timeframe: str, df: pd.DataFrame) -> None:
    if df is None or df.empty:
        return
    records = [
        {
            "symbol": symbol,
            "timeframe": timeframe,
            "ts": int(row.ts),
            "open": float(row.open),
            "high": float(row.high),
            "low": float(row.low),
            "close": float(row.close),
            "volume": float(row.volume),
        }
        for row in df.itertuples(index=False)
    ]
    if not records:
        return
    # Batch insert/update via SQLite ON CONFLICT DO UPDATE
    chunk_size = 150
    for i in range(0, len(records), chunk_size):
        chunk = records[i : i + chunk_size]
        stmt = sqlite_insert(Candle).values(chunk)
        stmt = stmt.on_conflict_do_update(
            index_elements=["symbol", "timeframe", "ts"],
            set_={
                "open": stmt.excluded.open,
                "high": stmt.excluded.high,
                "low": stmt.excluded.low,
                "close": stmt.excluded.close,
                "volume": stmt.excluded.volume,
            },
        )
        session.execute(stmt)


def load_candles_df(session: Session, symbol: str, timeframe: str) -> pd.DataFrame:
    rows = (
        session.query(Candle.ts, Candle.open, Candle.high, Candle.low, Candle.close, Candle.volume)
        .filter(Candle.symbol == symbol, Candle.timeframe == timeframe)
        .order_by(Candle.ts.asc())
        .all()
    )
    if not rows:
        return pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume"])
    return pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])


def get_candle_coverage(session: Session, symbols: list[str], timeframe: str) -> dict[str, tuple[int, int]]:
    """Return {symbol: (bar_count, max_ts)} for symbols already cached in SQLite."""
    unique = [s.strip().upper() for s in dict.fromkeys(symbols) if s and s.strip()]
    if not unique:
        return {}
    coverage: dict[str, tuple[int, int]] = {}
    for i in range(0, len(unique), 400):
        chunk = unique[i : i + 400]
        rows = (
            session.query(Candle.symbol, func.count(Candle.ts), func.max(Candle.ts))
            .filter(Candle.timeframe == timeframe, Candle.symbol.in_(chunk))
            .group_by(Candle.symbol)
            .all()
        )
        for sym, cnt, max_ts in rows:
            if cnt and max_ts is not None:
                coverage[str(sym)] = (int(cnt), int(max_ts))
    return coverage


def merge_candle_frames(existing_df: pd.DataFrame | None, delta_df: pd.DataFrame | None) -> pd.DataFrame:
    """Merge existing DB candles with newly fetched delta candles, deduplicated by ts."""
    cols = ["ts", "open", "high", "low", "close", "volume"]
    has_existing = existing_df is not None and not existing_df.empty
    has_delta = delta_df is not None and not delta_df.empty
    if not has_existing and not has_delta:
        return pd.DataFrame(columns=cols)
    if not has_existing:
        return delta_df[cols].drop_duplicates(subset=["ts"], keep="last").sort_values("ts").reset_index(drop=True)  # type: ignore[index]
    if not has_delta:
        return existing_df[cols].drop_duplicates(subset=["ts"], keep="last").sort_values("ts").reset_index(drop=True)  # type: ignore[index]

    merged = pd.concat([existing_df[cols], delta_df[cols]], ignore_index=True)  # type: ignore[index]
    merged["ts"] = merged["ts"].astype(int)
    merged = merged.drop_duplicates(subset=["ts"], keep="last").sort_values("ts").reset_index(drop=True)
    return merged


def has_historical_adjustment_mismatch(
    existing_df: pd.DataFrame | None,
    delta_df: pd.DataFrame | None,
    tol: float = 0.03,
    now_ts: int | None = None,
) -> bool:
    """Detect stock splits or major auto_adjust shifts on overlapping historical bars."""
    if existing_df is None or existing_df.empty or delta_df is None or delta_df.empty:
        return False
    cur_ts = int(now_ts if now_ts is not None else datetime.now(timezone.utc).timestamp())
    old_map = {int(ts): float(c) for ts, c in zip(existing_df["ts"], existing_df["close"])}
    new_map = {int(ts): float(c) for ts, c in zip(delta_df["ts"], delta_df["close"])}
    overlap = sorted(set(old_map.keys()) & set(new_map.keys()))
    if not overlap:
        return False

    if len(overlap) >= 2:
        check_ts = overlap[:-1]
    elif (cur_ts - overlap[0]) > 86400:
        check_ts = overlap
    else:
        return False

    for ts in check_ts:
        old_close = old_map[ts]
        new_close = new_map[ts]
        if old_close > 0 and abs((new_close / old_close) - 1.0) > tol:
            return True
    return False


def fetch_daily_incremental(
    session: Session,
    symbols: list[str],
    full_period: str = "2y",
    min_bars_for_delta: int = 220,
    chunk_size: int = 60,
    now_ts: int | None = None,
    ensure_ticker_fn: Callable[[str], Any] | None = None,
    ibkr_cfg: dict[str, Any] | None = None,
    prefer_ibkr: bool = False,
    ibkr_fallback: bool = True,
    ibkr_max_fallback: int = 25,
    priority: int = Priority.NORMAL,
) -> tuple[dict[str, pd.DataFrame], dict[str, int]]:
    """Fetch only missing/delta daily OHLCV bars based on SQLite coverage and auto-resync on splits."""
    unique = [s.strip().upper() for s in dict.fromkeys(symbols) if s and s.strip()]
    stats = {
        "delta_5d": 0,
        "delta_1mo": 0,
        "full": 0,
        "ibkr_fetched": 0,
        "resynced": 0,
        "cache_fallback": 0,
    }
    if not unique:
        return {}, stats

    cur_ts = int(now_ts if now_ts is not None else datetime.now(timezone.utc).timestamp())
    coverage = get_candle_coverage(session, unique, "1d")

    delta_5d_syms: list[str] = []
    delta_1mo_syms: list[str] = []
    full_syms: list[str] = []

    for sym in unique:
        cov = coverage.get(sym)
        if cov is not None and cov[0] >= min_bars_for_delta:
            age_s = max(0, cur_ts - cov[1])
            if age_s <= 4 * 86400:
                delta_5d_syms.append(sym)
                continue
            if age_s <= 22 * 86400:
                delta_1mo_syms.append(sym)
                continue
        full_syms.append(sym)

    stats["delta_5d"] = len(delta_5d_syms)
    stats["delta_1mo"] = len(delta_1mo_syms)
    stats["full"] = len(full_syms)

    def _persist(sym: str, df: pd.DataFrame) -> None:
        if ensure_ticker_fn is not None:
            ensure_ticker_fn(sym)
        upsert_candles(session, sym, "1d", df)

    def _yahoo_batch(syms: list[str], req_period: str) -> dict[str, pd.DataFrame]:
        if not syms:
            return {}
        if priority != Priority.NORMAL:
            return fetch_daily_batch(syms, period=req_period, chunk_size=chunk_size, priority=priority)
        return fetch_daily_batch(syms, period=req_period, chunk_size=chunk_size)

    prefetched: dict[str, pd.DataFrame] = {}
    if prefer_ibkr and ibkr_cfg:
        for bucket_syms, req_p in ((delta_5d_syms, "5d"), (delta_1mo_syms, "1mo")):
            dur = period_to_ibkr_duration(req_p, "5 D")
            for sym in bucket_syms:
                ib_df = fetch_historical_bars_ibkr(
                    sym,
                    "1d",
                    dur,
                    host=str(ibkr_cfg["host"]),
                    port=int(ibkr_cfg["port"]),
                    client_id=int(ibkr_cfg["client_id"]),
                    priority=priority,
                    max_wait=4.0,
                )
                if ib_df is not None and not ib_df.empty:
                    prefetched[sym] = ib_df
                    stats["ibkr_fetched"] += 1

    fetched_full = _yahoo_batch(full_syms, full_period)
    delta_frames: dict[str, pd.DataFrame] = dict(prefetched)
    yahoo_5d = [s for s in delta_5d_syms if s not in prefetched]
    yahoo_1mo = [s for s in delta_1mo_syms if s not in prefetched]
    if yahoo_5d:
        delta_frames.update(_yahoo_batch(yahoo_5d, "5d"))
    if yahoo_1mo:
        delta_frames.update(_yahoo_batch(yahoo_1mo, "1mo"))

    # IBKR HMDS fallback for tickers where Yahoo returned nothing
    if ibkr_fallback and ibkr_cfg:
        delta_5d_set = set(delta_5d_syms)
        missing_delta = [
            s
            for s in (delta_5d_syms + delta_1mo_syms)
            if s not in delta_frames or delta_frames[s] is None or delta_frames[s].empty
        ]
        missing_full = [
            s for s in full_syms if s not in fetched_full or fetched_full[s] is None or fetched_full[s].empty
        ]
        fb_targets = [(s, "5d" if s in delta_5d_set else "1mo", delta_frames) for s in missing_delta] + [
            (s, full_period, fetched_full) for s in missing_full
        ]
        for sym, req_p, target_map in fb_targets[: max(0, int(ibkr_max_fallback))]:
            ib_df = fetch_historical_bars_ibkr(
                sym,
                "1d",
                period_to_ibkr_duration(req_p, "5 D"),
                host=str(ibkr_cfg["host"]),
                port=int(ibkr_cfg["port"]),
                client_id=int(ibkr_cfg["client_id"]),
                priority=priority,
                max_wait=3.0,
            )
            if ib_df is not None and not ib_df.empty:
                target_map[sym] = ib_df
                stats["ibkr_fetched"] += 1

    result: dict[str, pd.DataFrame] = {}
    for sym in full_syms:
        df = fetched_full.get(sym)
        if df is not None and not df.empty:
            _persist(sym, df)
            result[sym] = df
        else:
            existing_df = load_candles_df(session, sym, "1d")
            if not existing_df.empty:
                result[sym] = existing_df
                stats["cache_fallback"] += 1

    resync_syms: list[str] = []
    for sym in delta_5d_syms + delta_1mo_syms:
        existing_df = load_candles_df(session, sym, "1d")
        delta_df = delta_frames.get(sym)
        if delta_df is None or delta_df.empty:
            if not existing_df.empty:
                result[sym] = existing_df
                stats["cache_fallback"] += 1
            continue
        if not existing_df.empty and has_historical_adjustment_mismatch(existing_df, delta_df, now_ts=cur_ts):
            resync_syms.append(sym)
            continue
        _persist(sym, delta_df)
        result[sym] = merge_candle_frames(existing_df, delta_df)

    if resync_syms:
        logger.warning(
            "Adjustment/split mismatch detected for %d symbol(s); performing full %s resync: %s",
            len(resync_syms),
            full_period,
            ", ".join(resync_syms),
        )
        resync_frames = _yahoo_batch(resync_syms, full_period)
        for sym in resync_syms:
            full_df = resync_frames.get(sym)
            if full_df is not None and not full_df.empty:
                session.query(Candle).filter(Candle.symbol == sym, Candle.timeframe == "1d").delete(
                    synchronize_session=False
                )
                _persist(sym, full_df)
                result[sym] = full_df
                stats["resynced"] += 1
            else:
                existing_df = load_candles_df(session, sym, "1d")
                if not existing_df.empty:
                    result[sym] = existing_df
                    stats["cache_fallback"] += 1

    return result, stats


def fetch_intraday_1h_incremental(
    session: Session,
    symbol: str,
    full_period: str = "120d",
    min_bars_for_delta: int = 80,
    now_ts: int | None = None,
    ensure_ticker_fn: Callable[[str], Any] | None = None,
    ibkr_cfg: dict[str, Any] | None = None,
    prefer_ibkr: bool = False,
    priority: int = Priority.NORMAL,
) -> tuple[pd.DataFrame, str]:
    """Fetch only recent 1H bars when sufficient intraday history already exists in SQLite."""
    sym = symbol.strip().upper()
    cur_ts = int(now_ts if now_ts is not None else datetime.now(timezone.utc).timestamp())
    cov = get_candle_coverage(session, [sym], "1h").get(sym)
    req_period = full_period
    if cov is not None and cov[0] >= min_bars_for_delta:
        age_s = max(0, cur_ts - cov[1])
        if age_s <= 3 * 86400:
            req_period = "5d"
        elif age_s <= 20 * 86400:
            req_period = "1mo"

    existing_df = load_candles_df(session, sym, "1h")

    def _yahoo_1h(req_p: str) -> pd.DataFrame:
        if priority != Priority.NORMAL:
            return fetch_intraday_1h(sym, period=req_p, priority=priority)
        return fetch_intraday_1h(sym, period=req_p)

    delta_df: pd.DataFrame | None = None
    if prefer_ibkr and ibkr_cfg and req_period != full_period:
        delta_df = fetch_historical_bars_ibkr(
            sym,
            "1h",
            period_to_ibkr_duration(req_period, "5 D"),
            host=str(ibkr_cfg["host"]),
            port=int(ibkr_cfg["port"]),
            client_id=int(ibkr_cfg["client_id"]),
            priority=priority,
            max_wait=4.0,
        )
    if delta_df is None or delta_df.empty:
        delta_df = _yahoo_1h(req_period)
    if (delta_df is None or delta_df.empty) and ibkr_cfg and not prefer_ibkr:
        delta_df = fetch_historical_bars_ibkr(
            sym,
            "1h",
            period_to_ibkr_duration(req_period, "5 D"),
            host=str(ibkr_cfg["host"]),
            port=int(ibkr_cfg["port"]),
            client_id=int(ibkr_cfg["client_id"]),
            priority=priority,
            max_wait=3.0,
        )
    if delta_df is None or delta_df.empty:
        return existing_df, "cache_fallback"

    def _persist(df: pd.DataFrame) -> None:
        if ensure_ticker_fn is not None:
            ensure_ticker_fn(sym)
        upsert_candles(session, sym, "1h", df)

    if (
        req_period != full_period
        and not existing_df.empty
        and has_historical_adjustment_mismatch(existing_df, delta_df, now_ts=cur_ts)
    ):
        logger.warning("1H adjustment mismatch for %s; resyncing full %s history.", sym, full_period)
        full_df = _yahoo_1h(full_period)
        if full_df is not None and not full_df.empty:
            session.query(Candle).filter(Candle.symbol == sym, Candle.timeframe == "1h").delete(
                synchronize_session=False
            )
            _persist(full_df)
            return full_df, "resync"

    _persist(delta_df)
    return merge_candle_frames(existing_df, delta_df), req_period


_RADAR_SIGNAL_CODES = frozenset(
    {
        "LOWER_CHANNEL_TOUCH",
        "CHANNEL_BREAKOUT_UP",
        "CHANNEL_BREAKDOWN",
        "SMC_AT_BULLISH_OB",
        "SMC_AT_BEARISH_OB",
        "SR_BREAK_RETEST",
        "TTM_SQUEEZE_FIRED",
        "INSIDER_CLUSTER_BUY",
        "CEO_CFO_BUY",
    }
)
_CORE_INDEX_TAGS = frozenset({"sp500", "ndx100", "sp500+ndx100"})


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _update_swing_radar_leases(
    session: Session,
    ticker_rows: dict[str, Ticker],
    final_bundles: dict[str, dict[str, Any]],
    discovered_map: dict[str, dict[str, Any]],
    runtime_settings: dict[str, Any],
    now_utc: datetime | None = None,
) -> dict[str, int]:
    """Grant, extend, and expire rolling Swing Radar leases for dynamic non-index discoveries."""
    sched_cfg = runtime_settings.get("schedule", {})
    if not bool(sched_cfg.get("radar_enabled", True)):
        return {"active": 0, "extended": 0, "probation": 0, "expired": 0}

    cur_dt = _as_utc(now_utc) or utcnow()
    ttl_days = max(1, int(sched_cfg.get("radar_ttl_days", 14)))
    probation_days = max(1, int(sched_cfg.get("radar_probation_days", 7)))
    max_tickers = max(10, int(sched_cfg.get("radar_max_tickers", 120)))
    min_score = float(sched_cfg.get("radar_min_score", 66.0))

    extended_cnt = 0
    probation_cnt = 0
    expired_cnt = 0

    for sym, tk in ticker_rows.items():
        if sym in BENCHMARKS or sym in SECTORS:
            continue
        disc_tags = list(discovered_map.get(sym, {}).get("tags", []))
        try:
            stored_tags = set(json.loads(tk.discovery_sources_json or "[]"))
        except json.JSONDecodeError:
            stored_tags = set()
        all_tags = stored_tags | set(disc_tags)

        # Core S&P 500 / Nasdaq 100 members are always scanned in every full pass
        if all_tags & _CORE_INDEX_TAGS:
            if tk.radar_until is not None or "radar" in stored_tags:
                tk.radar_until = None
                tk.radar_reason = ""
                stored_tags.discard("radar")
                tk.discovery_sources_json = json.dumps(sorted(stored_tags))
            continue

        bundle = final_bundles.get(sym)
        if not bundle:
            continue

        comp = float(bundle.get("composite_score") or 0.0)
        short_rr_score = float(bundle.get("preset_scores", {}).get("short_term_high_rr") or 0.0)
        matching_sig_label = ""
        for sig in bundle.get("signals", []) or []:
            if isinstance(sig, dict) and sig.get("code") in _RADAR_SIGNAL_CODES:
                matching_sig_label = str(sig.get("label") or sig.get("code") or "")
                break

        cur_until = _as_utc(tk.radar_until)
        is_dynamic_discovery = any(("mover" in t.lower()) or ("insider" in t.lower()) for t in disc_tags)
        qualifies_full = bool(
            comp >= min_score
            or short_rr_score >= (min_score + 2.0)
            or (matching_sig_label and comp >= 58.0)
        )
        qualifies_probation = bool(
            is_dynamic_discovery and (cur_until is None or cur_until <= cur_dt) and comp >= 52.0
        )

        if qualifies_full:
            tk.radar_until = cur_dt + timedelta(days=ttl_days)
            if matching_sig_label:
                tk.radar_reason = f"Score {comp:.1f} ({matching_sig_label})"[:120]
            else:
                tk.radar_reason = f"Score {comp:.1f} / Short-RR {short_rr_score:.1f}"[:120]
            stored_tags.add("radar")
            tk.discovery_sources_json = json.dumps(sorted(stored_tags))
            extended_cnt += 1
        elif qualifies_probation:
            tk.radar_until = cur_dt + timedelta(days=probation_days)
            tk.radar_reason = f"Mover/Insider probation (Score {comp:.1f})"[:120]
            stored_tags.add("radar")
            tk.discovery_sources_json = json.dumps(sorted(stored_tags))
            probation_cnt += 1

    # Flush ORM changes (autoflush=False) before querying all active Radar rows to enforce cap
    session.flush()
    all_radar = session.query(Ticker).filter(Ticker.radar_until.isnot(None)).all()
    still_active: list[Ticker] = []
    for tk in all_radar:
        ru = _as_utc(tk.radar_until)
        if ru is None or ru <= cur_dt:
            tk.radar_until = None
            tk.radar_reason = ""
            try:
                tags = set(json.loads(tk.discovery_sources_json or "[]"))
                tags.discard("radar")
                tk.discovery_sources_json = json.dumps(sorted(tags))
            except json.JSONDecodeError:
                pass
            expired_cnt += 1
        else:
            still_active.append(tk)

    if len(still_active) > max_tickers:
        still_active.sort(
            key=lambda t: (
                float((final_bundles.get(t.symbol) or {}).get("composite_score") or 0.0),
                _as_utc(t.radar_until) or cur_dt,
            ),
            reverse=True,
        )
        for tk in still_active[max_tickers:]:
            tk.radar_until = None
            tk.radar_reason = ""
            try:
                tags = set(json.loads(tk.discovery_sources_json or "[]"))
                tags.discard("radar")
                tk.discovery_sources_json = json.dumps(sorted(tags))
            except json.JSONDecodeError:
                pass
            expired_cnt += 1
        still_active = still_active[:max_tickers]

    return {
        "active": len(still_active),
        "extended": extended_cnt,
        "probation": probation_cnt,
        "expired": expired_cnt,
    }


def _load_cached_fundamentals_events(
    session: Session,
    ticker: Ticker,
    max_age_hours: float = 12.0,
    now_dt: datetime | None = None,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Reuse recent Stage-2 fundamentals & events from latest deep snapshot, updating days_to_earnings."""
    if ticker.last_enriched_at is None:
        return None
    cur_dt = now_dt if now_dt is not None else utcnow()
    if cur_dt.tzinfo is None:
        cur_dt = cur_dt.replace(tzinfo=timezone.utc)
    enriched_at = ticker.last_enriched_at
    if enriched_at.tzinfo is None:
        enriched_at = enriched_at.replace(tzinfo=timezone.utc)
    if (cur_dt - enriched_at).total_seconds() > max_age_hours * 3600.0:
        return None

    snap = (
        session.query(AnalysisSnapshot)
        .filter(AnalysisSnapshot.symbol == ticker.symbol, AnalysisSnapshot.is_deep_enriched.is_(True))
        .order_by(AnalysisSnapshot.id.desc())
        .first()
    )
    if snap is None:
        return None

    try:
        fundamentals = json.loads(snap.fundamentals_json or "{}")
        events_raw = json.loads(snap.events_insiders_json or "{}")
    except json.JSONDecodeError:
        return None

    if not isinstance(fundamentals, dict) or not any(v is not None for v in fundamentals.values()):
        return None
    if not isinstance(events_raw, dict):
        events_raw = {}

    events = {k: v for k, v in events_raw.items() if k != "insiders"}
    next_ed = events.get("next_earnings_date")
    if isinstance(next_ed, str) and next_ed.strip():
        try:
            ed_date = datetime.fromisoformat(next_ed.strip()[:10]).date()
            dte = (ed_date - cur_dt.date()).days
            events["days_to_earnings"] = dte
            if dte <= 1:
                return None
        except ValueError:
            pass

    return fundamentals, events


def prune_old_snapshots(session: Session, keep_per_symbol: int = 5) -> int:
    """Prune stale AnalysisSnapshot rows beyond the most recent keep_per_symbol per symbol."""
    if keep_per_symbol < 1:
        return 0
    res = session.execute(
        text(
            """
            DELETE FROM analysis_snapshots
            WHERE id IN (
                SELECT id FROM (
                    SELECT id, ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY id DESC) AS rn
                    FROM analysis_snapshots
                ) WHERE rn > :keep
            )
            """
        ),
        {"keep": int(keep_per_symbol)},
    )
    deleted = int(res.rowcount or 0)
    if deleted > 0:
        logger.info("Pruned %d old analysis_snapshots rows (kept latest %d per symbol).", deleted, keep_per_symbol)
    return deleted


def _ensure_ticker_row(
    session: Session,
    symbol: str,
    name: str = "",
    sector: str = "",
    industry: str = "",
    source_tags: list[str] | None = None,
    is_custom: bool = False,
) -> Ticker:
    sym = symbol.upper()
    row = session.get(Ticker, sym)
    is_ndx = any("ndx" in t for t in (source_tags or []))
    sector_etf = map_sector_to_etf(sector)
    bench = get_primary_benchmark(sym, is_ndx=is_ndx, sector=sector)
    if row is None:
        row = Ticker(
            symbol=sym,
            name=name or sym,
            sector=sector,
            industry=industry,
            primary_benchmark=bench,
            sector_etf=sector_etf,
            discovery_sources_json=json.dumps(source_tags or []),
            is_custom=is_custom,
        )
        session.add(row)
        session.flush()
    else:
        if name and (not row.name or row.name == sym):
            row.name = name
        if sector and not row.sector:
            row.sector = sector
            row.sector_etf = map_sector_to_etf(sector)
            row.primary_benchmark = get_primary_benchmark(sym, is_ndx=is_ndx, sector=sector)
        if industry and not row.industry:
            row.industry = industry
        if source_tags:
            existing_tags = set(json.loads(row.discovery_sources_json or "[]"))
            existing_tags.update(source_tags)
            row.discovery_sources_json = json.dumps(sorted(existing_tags))
        if is_custom:
            row.is_custom = True
    return row


def _fetch_and_store_benchmarks(
    session: Session,
    daily_period: str = "2y",
    ibkr_cfg: dict[str, Any] | None = None,
) -> tuple[dict[str, pd.DataFrame], MarketRegime]:
    ref_symbols = list(BENCHMARKS.keys()) + list(SECTORS.keys())
    merged_frames, bench_stats = fetch_daily_incremental(
        session,
        ref_symbols,
        full_period=daily_period,
        min_bars_for_delta=220,
        chunk_size=15,
        ensure_ticker_fn=lambda sym: _ensure_ticker_row(
            session, sym, name=BENCHMARKS.get(sym) or SECTORS.get(sym) or sym
        ),
        ibkr_cfg=ibkr_cfg,
        prefer_ibkr=bool(ibkr_cfg),
        ibkr_fallback=True,
        priority=Priority.HIGH,
    )

    # Retry core benchmarks missing completely (neither fetched nor cached in DB)
    missing_core = [sym for sym in BENCHMARKS.keys() if sym not in merged_frames or merged_frames[sym].empty]
    for sym in missing_core:
        retry_map = fetch_daily_batch([sym], period=daily_period, chunk_size=1, priority=Priority.HIGH)
        retry_df = retry_map.get(sym)
        if retry_df is not None and not retry_df.empty:
            _ensure_ticker_row(session, sym, name=BENCHMARKS.get(sym) or SECTORS.get(sym) or sym)
            upsert_candles(session, sym, "1d", retry_df)
            merged_frames[sym] = retry_df

    logger.info(
        "Benchmarks/sectors incremental sync (%s): delta_5d=%d delta_1mo=%d full=%d ibkr=%d resynced=%d cache_fallback=%d",
        daily_period,
        bench_stats["delta_5d"],
        bench_stats["delta_1mo"],
        bench_stats["full"],
        bench_stats["ibkr_fetched"],
        bench_stats["resynced"],
        bench_stats["cache_fallback"],
    )
    if bench_stats["cache_fallback"] > 0:
        logger.warning(
            "Using cached SQLite benchmark/sector candles for %d symbol(s) after empty fetch.",
            bench_stats["cache_fallback"],
        )

    regime = compute_market_regime(merged_frames)
    if not regime.benchmarks:
        recent_runs = (
            session.query(ScanRun)
            .filter(ScanRun.market_regime_json != "{}")
            .order_by(ScanRun.id.desc())
            .limit(10)
            .all()
        )
        for run in recent_runs:
            try:
                parsed = json.loads(run.market_regime_json or "{}")
                if isinstance(parsed, dict) and parsed.get("benchmarks"):
                    regime = MarketRegime(**parsed)
                    break
            except Exception:
                continue

    return merged_frames, regime


def _load_benchmarks_from_db(session: Session) -> dict[str, pd.DataFrame]:
    ref_symbols = list(BENCHMARKS.keys()) + list(SECTORS.keys())
    frames: dict[str, pd.DataFrame] = {}
    for sym in ref_symbols:
        df = load_candles_df(session, sym, "1d")
        if not df.empty:
            frames[sym] = df
    return frames


def _run_analysis_bundle(
    symbol: str,
    daily_df: pd.DataFrame,
    intraday_df: pd.DataFrame | None,
    ticker_meta: Ticker,
    bench_frames: dict[str, pd.DataFrame],
    fundamentals: dict[str, Any],
    events: dict[str, Any],
    insiders: list[dict[str, Any]],
    market_regime: MarketRegime | None,
    runtime_settings: dict[str, Any],
    option_chain_df: pd.DataFrame | None,
    option_source: str,
    include_deep: bool,
) -> dict[str, Any]:
    enabled_map = runtime_settings.get("enabled_plugins", {})
    analyzers = registry.get_analyzers(enabled_map)

    bench_sym = ticker_meta.primary_benchmark or "SPY"
    sec_sym = ticker_meta.sector_etf or "SPY"

    def _pick_df(key: str) -> pd.DataFrame | None:
        df = bench_frames.get(key)
        if df is not None and not df.empty:
            return df
        spy = bench_frames.get("SPY")
        return spy if spy is not None and not spy.empty else None

    ctx = AnalysisContext(
        symbol=symbol,
        daily_df=daily_df,
        intraday_df=intraday_df,
        benchmark_symbol=bench_sym,
        benchmark_df=_pick_df(bench_sym),
        sector_etf_symbol=sec_sym,
        sector_df=_pick_df(sec_sym),
        fundamentals=fundamentals,
        events=events,
        insiders=insiders,
        market_regime=market_regime,
        settings=runtime_settings,
    )

    plugin_results: dict[str, AnalyzerResult] = {}
    for analyzer in analyzers:
        if analyzer.requires_deep_data and not include_deep and not fundamentals and not events:
            # Skip heavy plugins with empty deep payload in Stage 1 or run light placeholder
            pass
        try:
            res = analyzer.analyze(ctx)
            plugin_results[analyzer.plugin_id] = res
        except Exception as exc:
            logger.exception("Analyzer %s failed on %s: %s", analyzer.plugin_id, symbol, exc)

    agg = compute_scores_and_summary(plugin_results, runtime_settings)
    spot = float(daily_df["close"].iloc[-1])
    prev = float(daily_df["close"].iloc[-2]) if len(daily_df) >= 2 else spot
    chg_pct = ((spot / prev) - 1.0) * 100.0 if prev > 0 else 0.0

    trade_plan, plan_overlay = build_trade_plan(
        spot,
        plugin_results,
        agg["bias"],
        benchmark_df=ctx.benchmark_df,
    )
    overlays = list(agg["overlays"]) + [plan_overlay]

    # Options strategy proposals (only during deep enrichment)
    option_payload: dict[str, Any] = {"iv_metrics": {}, "proposals": []}
    if include_deep:
        pa_hints = plugin_results["price_action_sr"].trade_hints if "price_action_sr" in plugin_results else None
        ch_hints = plugin_results["trend_channels"].trade_hints if "trend_channels" in plugin_results else None
        opt_ctx = OptionAnalysisContext(
            symbol=symbol,
            spot_price=spot,
            daily_df=daily_df,
            chain_df=option_chain_df,
            bias=agg["bias"],
            days_to_earnings=events.get("days_to_earnings"),
            nearest_support=pa_hints.nearest_support if pa_hints else None,
            nearest_resistance=pa_hints.nearest_resistance if pa_hints else None,
            channel_lower=ch_hints.channel_lower if ch_hints else None,
            channel_upper=ch_hints.channel_upper if ch_hints else None,
            data_source=option_source,
        )
        all_proposals = []
        iv_metrics: dict[str, Any] = {}
        for opt_plugin in registry.get_option_plugins(enabled_map):
            try:
                out = opt_plugin.propose(opt_ctx)
                iv_metrics = out.get("iv_metrics", iv_metrics)
                for rf in out.get("risk_flags", []):
                    if all(existing.code != rf.code for existing in agg["risk_flags"]):
                        agg["risk_flags"].append(rf)
                all_proposals.extend(out.get("proposals", []))
            except Exception as exc:
                logger.warning("Option plugin %s failed on %s: %s", opt_plugin.plugin_id, symbol, exc)
        option_payload = {
            "iv_metrics": iv_metrics,
            "proposals": [p.model_dump() for p in all_proposals],
        }

    return {
        "last_price": round(spot, 2),
        "change_pct": round(chg_pct, 2),
        "composite_score": agg["composite_score"],
        "bias": agg["bias"],
        "preset_scores": agg["preset_scores"],
        "plugin_results": {k: v.model_dump() for k, v in plugin_results.items()},
        "signals": [s.model_dump() for s in agg["signals"]],
        "risk_flags": [r.model_dump() for r in agg["risk_flags"]],
        "overlays": [o.model_dump() for o in overlays],
        "trade_plan": trade_plan.model_dump(),
        "option_payload": option_payload,
        "fundamentals": fundamentals,
        "events_insiders": {**events, "insiders": insiders},
    }


def _save_snapshot(
    session: Session,
    scan_run_id: int | None,
    symbol: str,
    bundle: dict[str, Any],
    is_deep: bool,
) -> AnalysisSnapshot:
    snap = AnalysisSnapshot(
        scan_run_id=scan_run_id,
        symbol=symbol,
        analyzed_at=utcnow(),
        last_price=bundle["last_price"],
        change_pct=bundle["change_pct"],
        composite_score=bundle["composite_score"],
        bias=bundle["bias"],
        preset_scores_json=json.dumps(bundle["preset_scores"]),
        plugin_results_json=json.dumps(bundle["plugin_results"]),
        signals_json=json.dumps(bundle["signals"]),
        risk_flags_json=json.dumps(bundle["risk_flags"]),
        overlays_json=json.dumps(bundle["overlays"]),
        trade_plan_json=json.dumps(bundle["trade_plan"]),
        option_proposals_json=json.dumps(bundle["option_payload"]),
        fundamentals_json=json.dumps(bundle["fundamentals"]),
        events_insiders_json=json.dumps(bundle["events_insiders"]),
        is_deep_enriched=is_deep,
    )
    session.add(snap)
    return snap


def _fetch_options_with_ibkr_fallback(
    symbol: str,
    spot_price: float,
    days_to_earnings: int | None,
    runtime_settings: dict[str, Any],
    priority: int = Priority.NORMAL,
) -> tuple[pd.DataFrame | None, str]:
    ibkr_cfg = runtime_settings.get("ibkr", {})
    if ibkr_cfg.get("enabled", False):
        chain_df = fetch_option_chain_with_greeks(
            symbol=symbol,
            spot_price=spot_price,
            host=str(ibkr_cfg.get("host", "127.0.0.1")),
            port=int(ibkr_cfg.get("port", 4002)),
            client_id=int(ibkr_cfg.get("client_id", 17)),
            priority=priority,
        )
        if chain_df is not None and not chain_df.empty:
            return chain_df, "IBKR Gateway"

    yahoo_chain = fetch_option_chain(symbol, days_to_earnings=days_to_earnings, priority=priority)
    return yahoo_chain, "Yahoo + BS"


def run_scan(mode: Literal["full", "quick", "watchlists"] = "full") -> int | None:
    """Execute full, quick, or watchlists-only two-stage scan."""
    with _scan_lock:
        if _scan_status["running"]:
            return _scan_status.get("scan_run_id")
        _scan_status.update(
            {
                "running": True,
                "mode": mode,
                "stage": "initializing",
                "processed": 0,
                "total": 0,
                "message": f"Starting {mode} scan...",
                "started_at": datetime.now(timezone.utc).isoformat(),
                "finished_at": None,
                "error": None,
            }
        )

    registry.discover_all()
    scan_run_id: int | None = None

    try:
        with session_scope() as session:
            runtime_settings = load_runtime_settings(session)
            scan_run = ScanRun(mode=mode, status="running", stage="discovery")
            session.add(scan_run)
            session.flush()
            scan_run_id = scan_run.id
            _update_scan_status(scan_run_id=scan_run_id)

            # Collect watchlist + custom + active/pending PaperTrade symbols
            watchlist_syms = {row[0] for row in session.query(WatchlistItem.symbol).all()}
            custom_syms = {row[0] for row in session.query(Ticker.symbol).filter(Ticker.is_custom.is_(True)).all()}
            active_paper_syms = {
                row[0]
                for row in session.query(PaperTrade.symbol)
                .filter(PaperTrade.status.in_(("PENDING_ENTRY", "OPEN", "PARTIAL_TP1")))
                .all()
            }
            priority_syms = watchlist_syms | custom_syms | active_paper_syms

            daily_period, intraday_period, _ = get_active_history_periods(runtime_settings)
            ibkr_cfg = _resolve_ibkr_cfg(runtime_settings)

            # 1. Benchmarks & Market Regime
            _update_scan_status(stage="benchmarks", message="Fetching market regime (SPY/QQQ/IWM/VIX & Sectors)...")
            bench_frames, market_regime = _fetch_and_store_benchmarks(
                session, daily_period=daily_period, ibkr_cfg=ibkr_cfg
            )
            scan_run.market_regime_json = market_regime.model_dump_json()
            session.commit()

            # 2. Discover candidate universe
            _update_scan_status(stage="discovery", message="Discovering universe & dynamic swing movers...")
            discovered_map: dict[str, dict[str, Any]] = {}

            def add_disc(sym: str, name: str = "", sector: str = "", industry: str = "", tag: str = "universe") -> None:
                s = sym.upper()
                if s in BENCHMARKS or s in SECTORS:
                    return
                entry = discovered_map.setdefault(
                    s, {"symbol": s, "name": name or s, "sector": sector, "industry": industry, "tags": []}
                )
                if name and entry["name"] == s:
                    entry["name"] = name
                if sector and not entry["sector"]:
                    entry["sector"] = sector
                if industry and not entry["industry"]:
                    entry["industry"] = industry
                if tag not in entry["tags"]:
                    entry["tags"].append(tag)

            now_scan_utc = utcnow()
            sched_cfg = runtime_settings.get("schedule", {})
            radar_enabled = bool(sched_cfg.get("radar_enabled", True))
            radar_cap = int(sched_cfg.get("radar_max_tickers", 120))
            active_radar_rows: list[Ticker] = []
            if radar_enabled and mode in ("full", "quick"):
                radar_candidates = (
                    session.query(Ticker)
                    .filter(Ticker.radar_until.isnot(None))
                    .order_by(Ticker.radar_until.desc())
                    .all()
                )
                active_radar_rows = [
                    tk for tk in radar_candidates if (_as_utc(tk.radar_until) or now_scan_utc) > now_scan_utc
                ]

            if mode == "watchlists":
                for sym in priority_syms:
                    add_disc(sym, tag="watchlist")
            elif mode == "quick":
                for sym, name, sector, _ in FALLBACK_UNIVERSE[:35]:
                    add_disc(sym, name=name, sector=sector, tag="quick_leaders")
                for src in registry.get_sources(runtime_settings.get("enabled_plugins")):
                    if src.plugin_id in ("finviz_movers", "yahoo_movers", "openinsider_feed"):
                        for item in src.discover(runtime_settings)[:20]:
                            add_disc(item.symbol, item.name, item.sector, item.industry, item.source_tag)
                if active_radar_rows:
                    radar_syms = [tk.symbol for tk in active_radar_rows]
                    latest_scores: dict[str, float] = {}
                    subq = (
                        session.query(AnalysisSnapshot.symbol, func.max(AnalysisSnapshot.id).label("max_id"))
                        .filter(AnalysisSnapshot.symbol.in_(radar_syms))
                        .group_by(AnalysisSnapshot.symbol)
                        .subquery()
                    )
                    for r_sym, r_score in (
                        session.query(AnalysisSnapshot.symbol, AnalysisSnapshot.composite_score)
                        .join(subq, AnalysisSnapshot.id == subq.c.max_id)
                        .all()
                    ):
                        latest_scores[r_sym] = float(r_score or 0.0)
                    active_radar_rows.sort(
                        key=lambda tk: (latest_scores.get(tk.symbol, 0.0), _as_utc(tk.radar_until) or now_scan_utc),
                        reverse=True,
                    )
                    for tk in active_radar_rows[:25]:
                        add_disc(tk.symbol, name=tk.name, sector=tk.sector, industry=tk.industry, tag="radar")
                for sym in priority_syms:
                    add_disc(sym, tag="watchlist")
            else:
                for src in registry.get_sources(runtime_settings.get("enabled_plugins")):
                    for item in src.discover(runtime_settings):
                        add_disc(item.symbol, item.name, item.sector, item.industry, item.source_tag)
                for tk in active_radar_rows[:radar_cap]:
                    add_disc(tk.symbol, name=tk.name, sector=tk.sector, industry=tk.industry, tag="radar")
                for sym in priority_syms:
                    add_disc(sym, tag="watchlist")

            symbols = sorted(discovered_map.keys())
            scan_run.total_tickers = len(symbols)
            _update_scan_status(total=len(symbols), stage="stage1_candles", message=f"Downloading daily OHLCV for {len(symbols)} tickers...")

            # Upsert ticker metadata
            ticker_rows: dict[str, Ticker] = {}
            for sym, info in discovered_map.items():
                ticker_rows[sym] = _ensure_ticker_row(
                    session,
                    sym,
                    name=info["name"],
                    sector=info["sector"],
                    industry=info["industry"],
                    source_tags=info["tags"],
                )
            session.commit()

            # Load OpenInsider map once for Stage 1
            insider_map = (
                fetch_openinsider_map()
                if runtime_settings.get("enabled_plugins", {}).get("openinsider_feed", True)
                else {}
            )

            # 3. Stage 1: Incremental Daily OHLCV + Fast Technical Pass
            daily_frames, d1_stats = fetch_daily_incremental(
                session,
                symbols,
                full_period=daily_period,
                min_bars_for_delta=220,
                chunk_size=15,
                ibkr_cfg=ibkr_cfg,
                prefer_ibkr=(mode == "watchlists"),
                ibkr_fallback=True,
                priority=Priority.NORMAL,
            )
            logger.info(
                "Stage 1 1D sync (%d symbols, full_period=%s): delta_5d=%d delta_1mo=%d full=%d ibkr=%d resynced=%d cache_fallback=%d",
                len(symbols),
                daily_period,
                d1_stats["delta_5d"],
                d1_stats["delta_1mo"],
                d1_stats["full"],
                d1_stats["ibkr_fetched"],
                d1_stats["resynced"],
                d1_stats["cache_fallback"],
            )
            _update_scan_status(
                stage="stage1_analyze",
                message=(
                    f"Running Stage 1 on {len(daily_frames)} tickers "
                    f"(5d={d1_stats['delta_5d']}, 1mo={d1_stats['delta_1mo']}, full={d1_stats['full']}, ibkr={d1_stats['ibkr_fetched']}, cached={d1_stats['cache_fallback']})..."
                ),
            )

            stage1_bundles: dict[str, dict[str, Any]] = {}
            for idx, (sym, df) in enumerate(daily_frames.items(), start=1):
                if len(df) < 15:
                    continue
                bundle = _run_analysis_bundle(
                    symbol=sym,
                    daily_df=df,
                    intraday_df=None,
                    ticker_meta=ticker_rows[sym],
                    bench_frames=bench_frames,
                    fundamentals={},
                    events={},
                    insiders=insider_map.get(sym, []),
                    market_regime=market_regime,
                    runtime_settings=runtime_settings,
                    option_chain_df=None,
                    option_source="Yahoo + BS",
                    include_deep=False,
                )
                stage1_bundles[sym] = bundle
                if idx % 25 == 0:
                    _update_scan_status(processed=idx)
            session.commit()

            # 4. Stage 2: Deep Enrichment for Top 50 Composite + Top 45 LONG + Top 35 SHORT + Watchlists/Paper
            ranked_stage1 = sorted(
                stage1_bundles.items(),
                key=lambda kv: kv[1]["composite_score"],
                reverse=True,
            )
            if mode == "full":
                def _stage1_idea_rank(item: tuple[str, dict[str, Any]]) -> float:
                    b = item[1]
                    comp = float(b.get("composite_score") or 0.0)
                    s_rr = float(b.get("preset_scores", {}).get("short_term_high_rr", comp) or comp)
                    rr_val = min(max(float(b.get("trade_plan", {}).get("risk_reward") or 0.0), 0.0), 4.5)
                    tp1_val = float(b.get("trade_plan", {}).get("tp1_risk_reward") or 0.0)
                    tp1_pen = -6.0 if 0.0 < tp1_val < 0.8 else 0.0
                    return 0.5 * comp + 0.5 * s_rr + max(0.0, (rr_val - 1.5) * 3.0) + tp1_pen

                top_comp = [sym for sym, _ in ranked_stage1[:50]]
                long_cands = sorted(
                    [
                        kv
                        for kv in stage1_bundles.items()
                        if kv[1].get("trade_plan", {}).get("direction") == "LONG"
                        and float(kv[1].get("trade_plan", {}).get("risk_reward") or 0.0) >= 1.5
                    ],
                    key=_stage1_idea_rank,
                    reverse=True,
                )
                short_cands = sorted(
                    [
                        kv
                        for kv in stage1_bundles.items()
                        if kv[1].get("trade_plan", {}).get("direction") == "SHORT"
                        and float(kv[1].get("trade_plan", {}).get("risk_reward") or 0.0) >= 1.5
                    ],
                    key=_stage1_idea_rank,
                    reverse=True,
                )
                deep_symbols = (
                    set(top_comp)
                    | {sym for sym, _ in long_cands[:45]}
                    | {sym for sym, _ in short_cands[:35]}
                    | (priority_syms & set(stage1_bundles.keys()))
                )
            else:
                deep_symbols = set(stage1_bundles.keys())
            deep_list = [sym for sym, _ in ranked_stage1 if sym in deep_symbols]

            _update_scan_status(
                stage="stage2_deep",
                processed=0,
                total=len(deep_list),
                message=f"Deep enriching Top {len(deep_list)} candidates & watchlists (1H, Fundamentals, Earnings, Options)...",
            )

            deep_enriched_bundles: dict[str, dict[str, Any]] = {}
            cached_fundamentals_count = 0
            finnhub_key = str(runtime_settings.get("data_sources", {}).get("finnhub_api_key", "")).strip()
            sec_ua = str(runtime_settings.get("data_sources", {}).get("sec_user_agent", "StockScreener/1.0"))
            use_sec = runtime_settings.get("enabled_plugins", {}).get("sec_edgar_form4", False)

            for idx, sym in enumerate(deep_list, start=1):
                try:
                    df_1d = daily_frames[sym]
                    df_1h, _h1_mode = fetch_intraday_1h_incremental(
                        session,
                        sym,
                        full_period=intraday_period,
                        min_bars_for_delta=80,
                        ibkr_cfg=ibkr_cfg,
                        prefer_ibkr=(mode == "watchlists"),
                        priority=Priority.NORMAL,
                    )

                    tk = ticker_rows[sym]
                    cached_fe = _load_cached_fundamentals_events(session, tk, max_age_hours=12.0)
                    if cached_fe is not None:
                        fundamentals, events = cached_fe
                        cached_fundamentals_count += 1
                    else:
                        fundamentals, events, meta = fetch_fundamentals_and_events(sym)
                        if meta.get("name") and tk.name == sym:
                            tk.name = meta["name"]
                        if meta.get("sector") and not tk.sector:
                            tk.sector = meta["sector"]
                            tk.sector_etf = map_sector_to_etf(meta["sector"])
                            tk.primary_benchmark = get_primary_benchmark(sym, sector=meta["sector"])
                        if meta.get("industry") and not tk.industry:
                            tk.industry = meta["industry"]
                        if any(v is not None for v in fundamentals.values()):
                            tk.last_enriched_at = utcnow()

                        if finnhub_key:
                            fh = fetch_finnhub_enrichment(sym, finnhub_key)
                            if fh.get("news"):
                                events["news"] = fh["news"]
                            if fh.get("analyst_breakdown"):
                                events["analyst_breakdown"] = fh["analyst_breakdown"]

                    insiders = list(insider_map.get(sym, []))
                    if use_sec and not insiders:
                        insiders = fetch_sec_form4_filings(sym, sec_ua)

                    spot = float(df_1d["close"].iloc[-1])
                    chain_df, opt_source = _fetch_options_with_ibkr_fallback(
                        sym, spot, events.get("days_to_earnings"), runtime_settings
                    )

                    bundle = _run_analysis_bundle(
                        symbol=sym,
                        daily_df=df_1d,
                        intraday_df=df_1h if not df_1h.empty else None,
                        ticker_meta=tk,
                        bench_frames=bench_frames,
                        fundamentals=fundamentals,
                        events=events,
                        insiders=insiders,
                        market_regime=market_regime,
                        runtime_settings=runtime_settings,
                        option_chain_df=chain_df,
                        option_source=opt_source,
                        include_deep=True,
                    )
                    deep_enriched_bundles[sym] = bundle
                    _save_snapshot(session, scan_run_id, sym, bundle, is_deep=True)
                except Exception as exc:
                    logger.warning("Deep enrichment error for %s: %s", sym, exc)
                    _save_snapshot(session, scan_run_id, sym, stage1_bundles[sym], is_deep=False)

                _update_scan_status(processed=idx, message=f"Deep enriched {sym} ({idx}/{len(deep_list)})")
                if idx % 10 == 0:
                    session.commit()

            # Save remaining Stage-1-only snapshots so full universe is browsable
            for sym, bundle in stage1_bundles.items():
                if sym not in deep_enriched_bundles:
                    _save_snapshot(session, scan_run_id, sym, bundle, is_deep=False)

            # Update Swing Radar rolling leases based on fresh Stage-1/Stage-2 scores
            final_bundles = {**stage1_bundles, **deep_enriched_bundles}
            radar_stats = _update_swing_radar_leases(
                session,
                ticker_rows=ticker_rows,
                final_bundles=final_bundles,
                discovered_map=discovered_map,
                runtime_settings=runtime_settings,
            )

            scan_run.status = "completed"
            scan_run.stage = "completed"
            scan_run.processed_tickers = len(stage1_bundles)
            scan_run.finished_at = utcnow()
            session.commit()

            pruned = prune_old_snapshots(session, keep_per_symbol=5)
            if pruned:
                session.commit()
            logger.info(
                "Scan #%s (%s) completed: analyzed=%d deep=%d cached_fundamentals=%d/%d radar=%s pruned_snapshots=%d",
                scan_run_id,
                mode,
                len(stage1_bundles),
                len(deep_enriched_bundles),
                cached_fundamentals_count,
                len(deep_list),
                radar_stats,
                pruned,
            )

            # 5. Dispatch optional notifiers
            _dispatch_notifications(runtime_settings, deep_enriched_bundles, priority_syms, market_regime)

        paper_cfg = runtime_settings.get("paper_trading", {})
        if paper_cfg.get("enabled", False) and paper_cfg.get("auto_execute_after_scan", True):
            try:
                from app.core.paper_virtual import run_paper_allocation_cycle

                res = run_paper_allocation_cycle(force=False)
                logger.info("Post-scan paper allocation: %s", res)
            except Exception as exc:
                logger.warning("Post-scan paper allocation error: %s", exc)

        _update_scan_status(
            running=False,
            stage="completed",
            finished_at=datetime.now(timezone.utc).isoformat(),
            message=f"Scan complete ({len(stage1_bundles)} analyzed, {len(deep_enriched_bundles)} deep enriched).",
        )
        return scan_run_id
    except Exception as exc:
        logger.exception("Scan failed: %s", exc)
        if scan_run_id is not None:
            try:
                with session_scope() as session:
                    sr = session.get(ScanRun, scan_run_id)
                    if sr:
                        sr.status = "failed"
                        sr.error_message = str(exc)
                        sr.finished_at = utcnow()
            except Exception:
                pass
        _update_scan_status(
            running=False,
            stage="failed",
            error=str(exc),
            finished_at=datetime.now(timezone.utc).isoformat(),
            message=f"Scan failed: {exc}",
        )
        return scan_run_id


def _dispatch_notifications(
    runtime_settings: dict[str, Any],
    bundles: dict[str, dict[str, Any]],
    priority_syms: set[str],
    regime: MarketRegime,
) -> None:
    min_score = float(runtime_settings.get("notifiers", {}).get("min_alert_score", 78))
    alerts: list[dict[str, Any]] = []
    for sym, b in bundles.items():
        sig_codes = {s["code"] for s in b.get("signals", [])}
        is_channel_alert = bool(sig_codes & {"LOWER_CHANNEL_TOUCH", "CHANNEL_BREAKOUT_UP"}) and sym in priority_syms
        if b["composite_score"] >= min_score or is_channel_alert:
            alerts.append(
                {
                    "symbol": sym,
                    "last_price": b["last_price"],
                    "change_pct": b["change_pct"],
                    "score": b["composite_score"],
                    "bias": b["bias"],
                    "signals": [s["label"] for s in b.get("signals", [])],
                }
            )
    alerts.sort(key=lambda x: x["score"], reverse=True)
    if not alerts:
        return
    for notifier in registry.get_notifiers():
        if notifier.is_configured(runtime_settings):
            try:
                notifier.notify_scan_summary(runtime_settings, alerts, regime)
            except Exception as exc:
                logger.warning("Notifier %s error: %s", notifier.plugin_id, exc)


def enrich_single_ticker(symbol: str, force_refresh: bool = True) -> dict[str, Any] | None:
    """On-demand deep analysis for any US ticker (also tracks newly added symbols)."""
    sym = symbol.strip().upper().replace(".", "-")
    if not sym:
        return None
    registry.discover_all()

    with session_scope() as session:
        runtime_settings = load_runtime_settings(session)
        daily_period, intraday_period, _ = get_active_history_periods(runtime_settings)
        ibkr_cfg = _resolve_ibkr_cfg(runtime_settings)
        bench_frames = _load_benchmarks_from_db(session)
        market_regime: MarketRegime | None = None
        if "SPY" not in bench_frames or "QQQ" not in bench_frames or len(bench_frames.get("SPY", [])) < 260:
            bench_frames, market_regime = _fetch_and_store_benchmarks(
                session, daily_period=daily_period, ibkr_cfg=ibkr_cfg
            )
        else:
            market_regime = compute_market_regime(bench_frames)

        existing_1d_count = get_candle_coverage(session, [sym], "1d").get(sym, (0, 0))[0]
        if force_refresh or existing_1d_count < 30:
            fetched_map, _ = fetch_daily_incremental(
                session,
                [sym],
                full_period=daily_period,
                min_bars_for_delta=220,
                chunk_size=1,
                ensure_ticker_fn=lambda s: _ensure_ticker_row(session, s, is_custom=True),
                ibkr_cfg=ibkr_cfg,
                prefer_ibkr=bool(ibkr_cfg),
                ibkr_fallback=True,
                priority=Priority.HIGH,
            )
            df_1d = fetched_map.get(sym)
        else:
            df_1d = load_candles_df(session, sym, "1d")

        if df_1d is None or df_1d.empty:
            return None

        existing_1h_count = get_candle_coverage(session, [sym], "1h").get(sym, (0, 0))[0]
        if force_refresh or existing_1h_count == 0:
            df_1h, _ = fetch_intraday_1h_incremental(
                session,
                sym,
                full_period=intraday_period,
                min_bars_for_delta=80,
                ensure_ticker_fn=lambda s: _ensure_ticker_row(session, s, is_custom=True),
                ibkr_cfg=ibkr_cfg,
                prefer_ibkr=bool(ibkr_cfg),
                priority=Priority.HIGH,
            )
        else:
            df_1h = load_candles_df(session, sym, "1h")

        existing_tk = session.get(Ticker, sym)
        cached_fe = (
            _load_cached_fundamentals_events(session, existing_tk, max_age_hours=12.0)
            if (not force_refresh and existing_tk is not None)
            else None
        )
        if cached_fe is not None:
            fundamentals, events = cached_fe
            meta = {"name": existing_tk.name, "sector": existing_tk.sector, "industry": existing_tk.industry}
        else:
            fundamentals, events, meta = fetch_fundamentals_and_events(sym, priority=Priority.HIGH)

        tk = _ensure_ticker_row(
            session,
            sym,
            name=meta.get("name", sym),
            sector=meta.get("sector", ""),
            industry=meta.get("industry", ""),
            source_tags=["on_demand"],
            is_custom=True,
        )
        if cached_fe is None:
            if meta.get("name"):
                tk.name = meta["name"]
            if meta.get("sector"):
                tk.sector = meta["sector"]
                tk.sector_etf = map_sector_to_etf(meta["sector"])
                tk.primary_benchmark = get_primary_benchmark(sym, sector=meta["sector"])
            if meta.get("industry"):
                tk.industry = meta["industry"]
            if any(v is not None for v in fundamentals.values()):
                tk.last_enriched_at = utcnow()

            finnhub_key = str(runtime_settings.get("data_sources", {}).get("finnhub_api_key", "")).strip()
            if finnhub_key:
                fh = fetch_finnhub_enrichment(sym, finnhub_key)
                if fh.get("news"):
                    events["news"] = fh["news"]
                if fh.get("analyst_breakdown"):
                    events["analyst_breakdown"] = fh["analyst_breakdown"]

        insider_map = (
            fetch_openinsider_map()
            if runtime_settings.get("enabled_plugins", {}).get("openinsider_feed", True)
            else {}
        )
        insiders = list(insider_map.get(sym, []))
        if runtime_settings.get("enabled_plugins", {}).get("sec_edgar_form4", False) and not insiders:
            sec_ua = str(runtime_settings.get("data_sources", {}).get("sec_user_agent", "StockScreener/1.0"))
            insiders = fetch_sec_form4_filings(sym, sec_ua)

        spot = float(df_1d["close"].iloc[-1])
        chain_df, opt_source = _fetch_options_with_ibkr_fallback(
            sym, spot, events.get("days_to_earnings"), runtime_settings, priority=Priority.HIGH
        )

        bundle = _run_analysis_bundle(
            symbol=sym,
            daily_df=df_1d,
            intraday_df=df_1h if not df_1h.empty else None,
            ticker_meta=tk,
            bench_frames=bench_frames,
            fundamentals=fundamentals,
            events=events,
            insiders=insiders,
            market_regime=market_regime,
            runtime_settings=runtime_settings,
            option_chain_df=chain_df,
            option_source=opt_source,
            include_deep=True,
        )
        latest_run = session.query(ScanRun).order_by(ScanRun.id.desc()).first()
        if latest_run is None and market_regime is not None:
            latest_run = ScanRun(
                mode="on_demand",
                status="completed",
                stage="completed",
                total_tickers=1,
                processed_tickers=1,
                market_regime_json=market_regime.model_dump_json(),
                finished_at=utcnow(),
            )
            session.add(latest_run)
            session.flush()
        snap = _save_snapshot(session, latest_run.id if latest_run else None, sym, bundle, is_deep=True)
        session.commit()
        return {"snapshot_id": snap.id, "symbol": sym}
