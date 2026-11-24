from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable

import pandas as pd
import yfinance as yf

from app.core.contracts import SourcePlugin
from app.core.request_queue import Priority, yahoo_queue

logger = logging.getLogger(__name__)


def _reset_yahoo_crumb() -> None:
    """Clear yfinance cached crumb/cookie so the next call obtains a fresh session."""
    try:
        from yfinance.data import YfData

        yfd = YfData()
        lock = getattr(yfd, "_cookie_lock", None)
        if lock is not None:
            with lock:
                if hasattr(yfd, "_crumb"):
                    yfd._crumb = None
                if hasattr(yfd, "_cookie"):
                    yfd._cookie = None
        else:
            if hasattr(yfd, "_crumb"):
                yfd._crumb = None
            if hasattr(yfd, "_cookie"):
                yfd._cookie = None
    except Exception:
        pass


def _is_retryable_yahoo_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(tok in msg for tok in ("401", "crumb", "unauthorized", "429", "too many", "rate limit", "timed out"))


def _run_yahoo_queued(
    op_name: str,
    fn: Callable[[], Any],
    *,
    priority: int = Priority.NORMAL,
    dedup_key: str | None = None,
    timeout: float = 90.0,
) -> Any:
    def _exec() -> Any:
        try:
            return fn()
        except Exception as exc:
            if _is_retryable_yahoo_error(exc):
                logger.debug("Yahoo %s transient error (%s); resetting crumb and retrying.", op_name, exc)
                _reset_yahoo_crumb()
                time.sleep(1.0)
                return fn()
            raise

    return yahoo_queue.call(_exec, priority=priority, dedup_key=dedup_key, timeout=timeout)


def normalize_ohlcv_df(df: pd.DataFrame | None) -> pd.DataFrame:
    """Convert yfinance OHLCV DataFrame to normalized columns [ts, open, high, low, close, volume]."""
    if df is None or df.empty:
        return pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume"])

    work = df.copy()
    if isinstance(work.columns, pd.MultiIndex):
        lvl0 = set(map(str, work.columns.get_level_values(0)))
        lvl1 = set(map(str, work.columns.get_level_values(1)))
        if "Close" in lvl1 or "Open" in lvl1:
            work.columns = [c[1] for c in work.columns]
        elif "Close" in lvl0 or "Open" in lvl0:
            work.columns = [c[0] for c in work.columns]
        else:
            work.columns = [c[-1] for c in work.columns]

    rename_map = {
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Close": "close",
        "Volume": "volume",
    }
    work = work.rename(columns=rename_map)
    for col in ("open", "high", "low", "close"):
        if col not in work.columns:
            return pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume"])
    if "volume" not in work.columns:
        work["volume"] = 0.0

    idx = pd.to_datetime(work.index, utc=True)
    # Handle both pandas 2.x (ns) and pandas 3.x (s/us/ns) DatetimeIndex resolutions cleanly
    work["ts"] = [int(ts.timestamp()) for ts in idx]
    work = work[["ts", "open", "high", "low", "close", "volume"]].copy()
    work = work.dropna(subset=["open", "high", "low", "close"])
    work["volume"] = work["volume"].fillna(0.0).astype(float)
    for col in ("open", "high", "low", "close"):
        work[col] = work[col].astype(float)
    work = work.sort_values("ts").drop_duplicates(subset=["ts"]).reset_index(drop=True)
    return work


def _download_daily_chunk(chunk: list[str], period: str) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    if not chunk:
        return out
    raw = yf.download(
        tickers=chunk if len(chunk) > 1 else chunk[0],
        period=period,
        interval="1d",
        group_by="ticker",
        auto_adjust=True,
        threads=False,
        progress=False,
    )
    if raw is None or raw.empty:
        return out

    if len(chunk) == 1:
        sym = chunk[0]
        if isinstance(raw.columns, pd.MultiIndex) and sym in set(raw.columns.get_level_values(0)):
            norm = normalize_ohlcv_df(raw[sym].dropna(how="all"))
        else:
            norm = normalize_ohlcv_df(raw)
        if not norm.empty:
            out[sym] = norm
    elif isinstance(raw.columns, pd.MultiIndex):
        top_levels = set(raw.columns.get_level_values(0))
        for sym in chunk:
            if sym in top_levels:
                sub = raw[sym].dropna(how="all")
                norm = normalize_ohlcv_df(sub)
                if not norm.empty:
                    out[sym] = norm
    else:
        norm = normalize_ohlcv_df(raw)
        if not norm.empty:
            out[chunk[0]] = norm
    return out


def fetch_daily_batch(
    symbols: list[str],
    period: str = "1y",
    chunk_size: int = 15,
    priority: int = Priority.NORMAL,
) -> dict[str, pd.DataFrame]:
    """Batch-download daily OHLCV bars via paced Yahoo queue without multi-thread crumb races."""
    out: dict[str, pd.DataFrame] = {}
    unique = [s.strip().upper() for s in dict.fromkeys(symbols) if s.strip()]
    if not unique:
        return out

    eff_chunk = min(max(1, int(chunk_size)), 15)
    for i in range(0, len(unique), eff_chunk):
        chunk = unique[i : i + eff_chunk]
        chunk_res: dict[str, pd.DataFrame] = {}
        try:
            chunk_res = yahoo_queue.call(
                lambda ch=chunk: _download_daily_chunk(ch, period),
                priority=priority,
                dedup_key=f"yf:1d:{period}:{','.join(chunk)}",
                timeout=90.0,
            )
            out.update(chunk_res)
        except Exception as exc:
            logger.warning("yfinance batch download failed for chunk %d..%d: %s", i, i + len(chunk), exc)

        # Retry missing symbols once with fresh crumb when batch suffered >50% loss (likely crumb/rate limit)
        missing = [s for s in chunk if s not in chunk_res]
        if missing and (not chunk_res or (len(chunk) > 1 and len(missing) > len(chunk) * 0.5)):
            try:
                def _retry_missing(syms: list[str] = missing) -> dict[str, pd.DataFrame]:
                    _reset_yahoo_crumb()
                    time.sleep(1.2)
                    return _download_daily_chunk(syms, period)

                retry_res = yahoo_queue.call(
                    _retry_missing,
                    priority=priority,
                    timeout=90.0,
                )
                out.update(retry_res)
            except Exception:
                # Single-ticker queued fallback only if retry chunk raised
                for sym in missing:
                    try:
                        hist = yahoo_queue.call(
                            lambda s=sym: normalize_ohlcv_df(
                                yf.Ticker(s).history(period=period, interval="1d", auto_adjust=True)
                            ),
                            priority=priority,
                            dedup_key=f"yf:1d-single:{period}:{sym}",
                            timeout=60.0,
                        )
                        if hist is not None and not hist.empty:
                            out[sym] = hist
                    except Exception:
                        continue

    return out


def fetch_intraday_1h(
    symbol: str,
    period: str = "120d",
    priority: int = Priority.NORMAL,
) -> pd.DataFrame:
    sym = symbol.strip().upper()
    try:
        return _run_yahoo_queued(
            f"1h:{sym}",
            lambda: normalize_ohlcv_df(yf.Ticker(sym).history(period=period, interval="1h", auto_adjust=True)),
            priority=priority,
            dedup_key=f"yf:1h:{period}:{sym}",
            timeout=60.0,
        )
    except Exception as exc:
        logger.debug("Yahoo 1h fetch failed for %s: %s", sym, exc)
        return pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume"])


def fetch_weekly_bars(
    symbol: str,
    period: str = "10y",
    priority: int = Priority.NORMAL,
) -> pd.DataFrame:
    sym = symbol.strip().upper()
    try:
        return _run_yahoo_queued(
            f"1w:{sym}",
            lambda: normalize_ohlcv_df(yf.Ticker(sym).history(period=period, interval="1wk", auto_adjust=True)),
            priority=priority,
            dedup_key=f"yf:1w:{period}:{sym}",
            timeout=60.0,
        )
    except Exception as exc:
        logger.debug("Yahoo 1w fetch failed for %s: %s", sym, exc)
        return pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume"])


def resample_daily_to_weekly(daily_df: pd.DataFrame) -> pd.DataFrame:
    if daily_df is None or daily_df.empty:
        return pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume"])
    work = daily_df.copy()
    work["dt"] = pd.to_datetime(work["ts"], unit="s", utc=True)
    work = work.set_index("dt").sort_index()
    agg = work.resample("W-MON", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).dropna(subset=["open", "high", "low", "close"])
    agg["ts"] = [int(t.timestamp()) for t in agg.index]
    return agg[["ts", "open", "high", "low", "close", "volume"]].reset_index(drop=True)


def _fetch_live_quote_sync(sym: str) -> dict[str, Any] | None:
    tk = yf.Ticker(sym)
    fi = getattr(tk, "fast_info", None)
    price = _safe_float(getattr(fi, "last_price", None)) if fi is not None else None
    prev_close = _safe_float(getattr(fi, "previous_close", None)) if fi is not None else None
    day_high = _safe_float(getattr(fi, "day_high", None)) if fi is not None else None
    day_low = _safe_float(getattr(fi, "day_low", None)) if fi is not None else None

    if price is None:
        hist = tk.history(period="5d", interval="5m", auto_adjust=True)
        norm = normalize_ohlcv_df(hist)
        if not norm.empty:
            price = float(norm["close"].iloc[-1])
            if day_high is None:
                day_high = float(norm["high"].iloc[-1])
            if day_low is None:
                day_low = float(norm["low"].iloc[-1])

    if price is None:
        hist_1d = tk.history(period="5d", interval="1d", auto_adjust=True)
        norm_1d = normalize_ohlcv_df(hist_1d)
        if not norm_1d.empty:
            price = float(norm_1d["close"].iloc[-1])
            if prev_close is None and len(norm_1d) >= 2:
                prev_close = float(norm_1d["close"].iloc[-2])

    if price is None or price <= 0:
        return None

    chg_pct = ((price / prev_close) - 1.0) * 100.0 if prev_close and prev_close > 0 else 0.0
    return {
        "symbol": sym,
        "price": round(price, 2),
        "previous_close": round(prev_close, 2) if prev_close else None,
        "change_pct": round(chg_pct, 2),
        "day_high": round(day_high, 2) if day_high else None,
        "day_low": round(day_low, 2) if day_low else None,
        "bid": None,
        "ask": None,
        "source": "Yahoo Live",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def fetch_live_quote(symbol: str, priority: int = Priority.HIGH) -> dict[str, Any] | None:
    """Fetch near-real-time quote via Yahoo Finance fast_info with 5m bar fallback."""
    sym = symbol.strip().upper().replace(".", "-")
    try:
        return _run_yahoo_queued(
            f"quote:{sym}",
            lambda: _fetch_live_quote_sync(sym),
            priority=priority,
            dedup_key=f"yf:quote:{sym}",
            timeout=30.0,
        )
    except Exception as exc:
        logger.debug("Yahoo live quote failed for %s: %s", sym, exc)
        return None


def _fetch_fundamentals_and_events_sync(symbol: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, str]]:
    """Return (fundamentals_dict, events_dict, meta_dict)."""
    fundamentals: dict[str, Any] = {}
    events: dict[str, Any] = {}
    meta: dict[str, str] = {"name": symbol, "sector": "", "industry": ""}

    tk = yf.Ticker(symbol)
    info = tk.info or {}

    meta["name"] = info.get("shortName") or info.get("longName") or symbol
    meta["sector"] = info.get("sector") or ""
    meta["industry"] = info.get("industry") or ""

    market_cap = _safe_float(info.get("marketCap"))
    fcf = _safe_float(info.get("freeCashflow"))
    fcf_yield = round((fcf / market_cap) * 100.0, 2) if fcf is not None and market_cap and market_cap > 0 else None

    fundamentals = {
        "market_cap": market_cap,
        "pe_trailing": _safe_float(info.get("trailingPE")),
        "pe_forward": _safe_float(info.get("forwardPE")),
        "peg_ratio": _safe_float(info.get("pegRatio") or info.get("trailingPegRatio")),
        "price_to_sales": _safe_float(info.get("priceToSalesTrailing12Months")),
        "profit_margin": _pct_float(info.get("profitMargins")),
        "operating_margin": _pct_float(info.get("operatingMargins")),
        "roe": _pct_float(info.get("returnOnEquity")),
        "revenue_growth": _pct_float(info.get("revenueGrowth")),
        "earnings_growth": _pct_float(info.get("earningsGrowth")),
        "debt_to_equity": _safe_float(info.get("debtToEquity")),
        "current_ratio": _safe_float(info.get("currentRatio")),
        "free_cash_flow": fcf,
        "fcf_yield_pct": fcf_yield,
        "dividend_yield_pct": _pct_float(info.get("dividendYield"), already_pct_possible=True),
        "short_pct_float": _pct_float(info.get("shortPercentOfFloat")),
        "target_mean_price": _safe_float(info.get("targetMeanPrice")),
        "recommendation": info.get("recommendationKey"),
    }

    next_earnings_iso: str | None = None
    days_to_earnings: int | None = None
    now_dt = datetime.now(timezone.utc)

    try:
        cal = tk.calendar
        if isinstance(cal, dict):
            ed_list = cal.get("Earnings Date") or []
            parsed_dates: list[tuple[datetime, int]] = []
            for ed in ed_list:
                ed_dt = _coerce_datetime(ed)
                if ed_dt is not None:
                    parsed_dates.append((ed_dt, (ed_dt.date() - now_dt.date()).days))
            if parsed_dates:
                future_dates = [p for p in parsed_dates if p[1] >= 0]
                chosen_dt, chosen_days = (
                    min(future_dates, key=lambda p: p[1])
                    if future_dates
                    else max(parsed_dates, key=lambda p: p[1])
                )
                next_earnings_iso = chosen_dt.date().isoformat()
                days_to_earnings = chosen_days
    except Exception:
        pass

    if next_earnings_iso is None or (days_to_earnings is not None and days_to_earnings < 0):
        for ts_key in ("earningsTimestampStart", "earningsTimestamp"):
            ts_earn = info.get(ts_key)
            if not ts_earn:
                continue
            try:
                ed_dt = datetime.fromtimestamp(float(ts_earn), tz=timezone.utc)
                cand_days = (ed_dt.date() - now_dt.date()).days
                if next_earnings_iso is None or cand_days >= 0:
                    next_earnings_iso = ed_dt.date().isoformat()
                    days_to_earnings = cand_days
                    if cand_days >= 0:
                        break
            except Exception:
                pass

    ex_div_iso: str | None = None
    ex_div_ts = info.get("exDividendDate")
    if ex_div_ts:
        try:
            ex_div_iso = datetime.fromtimestamp(float(ex_div_ts), tz=timezone.utc).date().isoformat()
        except Exception:
            pass

    events = {
        "next_earnings_date": next_earnings_iso,
        "days_to_earnings": days_to_earnings,
        "ex_dividend_date": ex_div_iso,
        "analyst_target": _safe_float(info.get("targetMeanPrice")),
        "analyst_count": info.get("numberOfAnalystOpinions"),
        "recommendation": info.get("recommendationKey"),
        "news": [],
    }
    return fundamentals, events, meta


def fetch_fundamentals_and_events(
    symbol: str,
    priority: int = Priority.NORMAL,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, str]]:
    """Return (fundamentals_dict, events_dict, meta_dict) via paced Yahoo queue."""
    sym = symbol.strip().upper()
    try:
        return _run_yahoo_queued(
            f"fundamentals:{sym}",
            lambda: _fetch_fundamentals_and_events_sync(sym),
            priority=priority,
            dedup_key=f"yf:fund:{sym}",
            timeout=60.0,
        )
    except Exception as exc:
        logger.debug("Yahoo fundamentals/events failed for %s: %s", sym, exc)
        return {}, {}, {"name": sym, "sector": "", "industry": ""}


def _fetch_option_chain_sync(
    symbol: str,
    target_dte_min: int = 20,
    target_dte_max: int = 60,
    days_to_earnings: int | None = None,
) -> pd.DataFrame | None:
    tk = yf.Ticker(symbol)
    expiries = list(tk.options or [])
    if not expiries:
        return None

    today = datetime.now(timezone.utc).date()
    candidates: list[tuple[str, int]] = []
    for exp_str in expiries:
        try:
            exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
            dte = (exp_date - today).days
            if dte >= 10:
                candidates.append((exp_str, dte))
        except ValueError:
            continue

    if not candidates:
        return None

    in_window = [c for c in candidates if target_dte_min <= c[1] <= target_dte_max]
    pool = in_window or [c for c in candidates if c[1] >= 14] or candidates

    # Prefer expirations not crossing upcoming near-term earnings when possible
    if days_to_earnings is not None and 0 <= days_to_earnings <= 40:
        non_crossing = [c for c in pool if c[1] < days_to_earnings]
        if non_crossing:
            pool = non_crossing

    chosen_exp, chosen_dte = min(pool, key=lambda c: abs(c[1] - 35))
    chain = tk.option_chain(chosen_exp)

    frames: list[pd.DataFrame] = []
    for opt_type, df in [("CALL", chain.calls), ("PUT", chain.puts)]:
        if df is None or df.empty:
            continue
        sub = df.copy()
        sub["option_type"] = opt_type
        sub["expiry"] = chosen_exp
        sub["dte"] = chosen_dte
        sub["bid"] = pd.to_numeric(sub.get("bid"), errors="coerce").fillna(0.0)
        sub["ask"] = pd.to_numeric(sub.get("ask"), errors="coerce").fillna(0.0)
        sub["lastPrice"] = pd.to_numeric(sub.get("lastPrice"), errors="coerce").fillna(0.0)
        mid = (sub["bid"] + sub["ask"]) / 2.0
        sub["mid_price"] = mid.where((sub["bid"] > 0) & (sub["ask"] > 0), sub["lastPrice"])
        sub["iv"] = pd.to_numeric(sub.get("impliedVolatility"), errors="coerce")
        sub["oi"] = pd.to_numeric(sub.get("openInterest"), errors="coerce").fillna(0).astype(int)
        sub["volume"] = pd.to_numeric(sub.get("volume"), errors="coerce").fillna(0).astype(int)
        sub["delta"] = None
        frames.append(sub[["option_type", "strike", "expiry", "dte", "bid", "ask", "mid_price", "iv", "oi", "volume", "delta"]])

    if not frames:
        return None
    return pd.concat(frames, ignore_index=True)


def fetch_option_chain(
    symbol: str,
    target_dte_min: int = 20,
    target_dte_max: int = 60,
    days_to_earnings: int | None = None,
    priority: int = Priority.NORMAL,
) -> pd.DataFrame | None:
    """Fetch nearest swing-friendly option chain expiration via paced Yahoo queue."""
    sym = symbol.strip().upper()
    try:
        return _run_yahoo_queued(
            f"opt:{sym}",
            lambda: _fetch_option_chain_sync(sym, target_dte_min, target_dte_max, days_to_earnings),
            priority=priority,
            dedup_key=f"yf:opt:{sym}",
            timeout=60.0,
        )
    except Exception as exc:
        logger.debug("Yahoo option chain failed for %s: %s", sym, exc)
        return None


def _safe_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        f = float(val)
        if pd.isna(f) or f in (float("inf"), float("-inf")):
            return None
        return round(f, 4)
    except Exception:
        return None


def _pct_float(val: Any, already_pct_possible: bool = False) -> float | None:
    f = _safe_float(val)
    if f is None:
        return None
    if already_pct_possible and abs(f) > 1.0:
        return round(f, 2)
    return round(f * 100.0, 2)


def _coerce_datetime(val: Any) -> datetime | None:
    if isinstance(val, datetime):
        return val if val.tzinfo else val.replace(tzinfo=timezone.utc)
    try:
        return datetime.combine(val, datetime.min.time(), tzinfo=timezone.utc)
    except Exception:
        return None


class YahooMarketDataPlugin(SourcePlugin):
    plugin_id = "market_data_yahoo"
    display_name = "Yahoo Finance Market & Options Data"
    description = "Primary zero-key provider for batch daily OHLCV, 1H bars, fundamentals, earnings dates, and option chains."
