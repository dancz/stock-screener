from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Literal

import pandas as pd
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
from app.core.scoring import compute_scores_and_summary
from app.core.trade_planner import build_trade_plan
from app.db.database import session_scope
from app.db.models import (
    AnalysisSnapshot,
    Candle,
    ScanRun,
    Ticker,
    WatchlistItem,
    load_runtime_settings,
    utcnow,
)
from app.plugins.sources.finnhub_feed import fetch_finnhub_enrichment
from app.plugins.sources.market_data_ibkr import fetch_option_chain_with_greeks
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
        return dict(_scan_status)


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


def _fetch_and_store_benchmarks(session: Session, daily_period: str = "2y") -> tuple[dict[str, pd.DataFrame], MarketRegime]:
    ref_symbols = list(BENCHMARKS.keys()) + list(SECTORS.keys())
    frames = fetch_daily_batch(ref_symbols, period=daily_period, chunk_size=20)
    for sym, df in frames.items():
        _ensure_ticker_row(session, sym, name=BENCHMARKS.get(sym) or SECTORS.get(sym) or sym)
        upsert_candles(session, sym, "1d", df)
    regime = compute_market_regime(frames)
    return frames, regime


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
) -> tuple[pd.DataFrame | None, str]:
    ibkr_cfg = runtime_settings.get("ibkr", {})
    if ibkr_cfg.get("enabled", False):
        chain_df = fetch_option_chain_with_greeks(
            symbol=symbol,
            spot_price=spot_price,
            host=str(ibkr_cfg.get("host", "127.0.0.1")),
            port=int(ibkr_cfg.get("port", 4002)),
            client_id=int(ibkr_cfg.get("client_id", 17)),
        )
        if chain_df is not None and not chain_df.empty:
            return chain_df, "IBKR Gateway"

    yahoo_chain = fetch_option_chain(symbol, days_to_earnings=days_to_earnings)
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

            # Collect watchlist + custom symbols
            watchlist_syms = {row[0] for row in session.query(WatchlistItem.symbol).all()}
            custom_syms = {row[0] for row in session.query(Ticker.symbol).filter(Ticker.is_custom.is_(True)).all()}
            priority_syms = watchlist_syms | custom_syms

            daily_period, intraday_period, _ = get_active_history_periods(runtime_settings)

            # 1. Benchmarks & Market Regime
            _update_scan_status(stage="benchmarks", message="Fetching market regime (SPY/QQQ/IWM/VIX & Sectors)...")
            bench_frames, market_regime = _fetch_and_store_benchmarks(session, daily_period=daily_period)
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
                for sym in priority_syms:
                    add_disc(sym, tag="watchlist")
            else:
                for src in registry.get_sources(runtime_settings.get("enabled_plugins")):
                    for item in src.discover(runtime_settings):
                        add_disc(item.symbol, item.name, item.sector, item.industry, item.source_tag)
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

            # 3. Stage 1: Batch Daily OHLCV + Fast Technical Pass
            daily_frames = fetch_daily_batch(symbols, period=daily_period, chunk_size=60)
            _update_scan_status(stage="stage1_analyze", message=f"Running Stage 1 technical analysis on {len(daily_frames)} tickers...")

            stage1_bundles: dict[str, dict[str, Any]] = {}
            for idx, (sym, df) in enumerate(daily_frames.items(), start=1):
                if len(df) < 15:
                    continue
                upsert_candles(session, sym, "1d", df)
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

            # 4. Stage 2: Deep Enrichment for Top 90 + all Watchlists/Custom
            ranked_stage1 = sorted(
                stage1_bundles.items(),
                key=lambda kv: kv[1]["composite_score"],
                reverse=True,
            )
            deep_cap = 90 if mode == "full" else len(ranked_stage1)
            deep_symbols = set(sym for sym, _ in ranked_stage1[:deep_cap]) | (priority_syms & set(stage1_bundles.keys()))
            deep_list = [sym for sym, _ in ranked_stage1 if sym in deep_symbols]

            _update_scan_status(
                stage="stage2_deep",
                processed=0,
                total=len(deep_list),
                message=f"Deep enriching Top {len(deep_list)} candidates & watchlists (1H, Fundamentals, Earnings, Options)...",
            )

            deep_enriched_bundles: dict[str, dict[str, Any]] = {}
            finnhub_key = str(runtime_settings.get("data_sources", {}).get("finnhub_api_key", "")).strip()
            sec_ua = str(runtime_settings.get("data_sources", {}).get("sec_user_agent", "StockScreener/1.0"))
            use_sec = runtime_settings.get("enabled_plugins", {}).get("sec_edgar_form4", False)

            for idx, sym in enumerate(deep_list, start=1):
                try:
                    df_1d = daily_frames[sym]
                    df_1h = fetch_intraday_1h(sym, period=intraday_period)
                    if not df_1h.empty:
                        upsert_candles(session, sym, "1h", df_1h)

                    fundamentals, events, meta = fetch_fundamentals_and_events(sym)
                    tk = ticker_rows[sym]
                    if meta.get("name") and tk.name == sym:
                        tk.name = meta["name"]
                    if meta.get("sector") and not tk.sector:
                        tk.sector = meta["sector"]
                        tk.sector_etf = map_sector_to_etf(meta["sector"])
                        tk.primary_benchmark = get_primary_benchmark(sym, sector=meta["sector"])
                    if meta.get("industry") and not tk.industry:
                        tk.industry = meta["industry"]
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

            scan_run.status = "completed"
            scan_run.stage = "completed"
            scan_run.processed_tickers = len(stage1_bundles)
            scan_run.finished_at = utcnow()
            session.commit()

            # 5. Dispatch optional notifiers
            _dispatch_notifications(runtime_settings, deep_enriched_bundles, priority_syms, market_regime)

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
        bench_frames = _load_benchmarks_from_db(session)
        market_regime: MarketRegime | None = None
        if "SPY" not in bench_frames or "QQQ" not in bench_frames or len(bench_frames.get("SPY", [])) < 260:
            bench_frames, market_regime = _fetch_and_store_benchmarks(session, daily_period=daily_period)
        else:
            market_regime = compute_market_regime(bench_frames)

        df_1d = load_candles_df(session, sym, "1d")
        if force_refresh or len(df_1d) < 30:
            fetched = fetch_daily_batch([sym], period=daily_period, chunk_size=1)
            if sym in fetched and not fetched[sym].empty:
                df_1d = fetched[sym]
                _ensure_ticker_row(session, sym, is_custom=True)
                upsert_candles(session, sym, "1d", df_1d)

        if df_1d is None or df_1d.empty:
            return None

        df_1h = load_candles_df(session, sym, "1h")
        if force_refresh or df_1h.empty:
            df_1h = fetch_intraday_1h(sym, period=intraday_period)
            if not df_1h.empty:
                _ensure_ticker_row(session, sym, is_custom=True)
                upsert_candles(session, sym, "1h", df_1h)

        fundamentals, events, meta = fetch_fundamentals_and_events(sym)
        tk = _ensure_ticker_row(
            session,
            sym,
            name=meta.get("name", sym),
            sector=meta.get("sector", ""),
            industry=meta.get("industry", ""),
            source_tags=["on_demand"],
            is_custom=True,
        )
        if meta.get("name"):
            tk.name = meta["name"]
        if meta.get("sector"):
            tk.sector = meta["sector"]
            tk.sector_etf = map_sector_to_etf(meta["sector"])
            tk.primary_benchmark = get_primary_benchmark(sym, sector=meta["sector"])
        if meta.get("industry"):
            tk.industry = meta["industry"]
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
