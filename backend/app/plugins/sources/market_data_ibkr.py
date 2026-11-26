from __future__ import annotations

import asyncio
import logging
import math
import os
import socket
import time
from datetime import datetime, timezone
from typing import Any, Literal

import pandas as pd

from app.core.contracts import SourcePlugin
from app.core.request_queue import Priority, ibkr_worker

logger = logging.getLogger(__name__)


def _make_ibkr_contract(symbol: str) -> Any:
    from ib_async import Index, Stock

    sym = symbol.strip().upper().replace(".", "-")
    if sym in ("^VIX", "VIX"):
        return Index("VIX", "CBOE", "USD")
    return Stock(sym.replace("-", " "), "SMART", "USD")


def period_to_ibkr_duration(period: str, fallback: str = "1 Y") -> str:
    p = period.strip().lower()
    if p.endswith("y") and p[:-1].isdigit():
        return f"{p[:-1]} Y"
    if p.endswith("mo") and p[:-2].isdigit():
        return f"{p[:-2]} M"
    if p.endswith("d") and p[:-1].isdigit():
        days = int(p[:-1])
        return f"{max(1, round(days / 365))} Y" if days >= 365 else f"{days} D"
    return fallback


def is_gateway_reachable(host: str, port: int, timeout: float = 0.25) -> bool:
    """Fast TCP probe so offline IBKR Gateway adds <0.25s overhead."""
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def _run_async(coro: Any) -> Any:
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)
    finally:
        try:
            loop.close()
        finally:
            asyncio.set_event_loop(None)


def test_connection(host: str, port: int, client_id: int) -> tuple[bool, str]:
    """Read-only IBKR Gateway connectivity verification."""
    if not is_gateway_reachable(host, port, timeout=0.6):
        return False, f"IBKR Gateway TCP port {host}:{port} is not reachable."

    async def _probe() -> tuple[bool, str]:
        from ib_async import IB

        ib = IB()
        try:
            await ib.connectAsync(host, int(port), clientId=int(client_id), timeout=3.0, readonly=True)
            accounts = ib.managedAccounts()
            mode_hint = "Paper" if int(port) in (4002, 7497) else "Live/Custom"
            acct_str = ", ".join(accounts) if accounts else "connected"
            return True, f"Connected ({mode_hint}, accounts: {acct_str})"
        except Exception as exc:
            return False, f"IBKR handshake failed: {exc}"
        finally:
            if ib.isConnected():
                ib.disconnect()

    try:
        return _run_async(_probe())
    except Exception as exc:
        return False, f"IBKR check error: {exc}"


def fetch_option_chain_with_greeks(
    symbol: str,
    spot_price: float,
    host: str,
    port: int,
    client_id: int,
    target_dte_min: int = 20,
    target_dte_max: int = 60,
    priority: int = Priority.NORMAL,
) -> pd.DataFrame | None:
    """Read-only fetch of IBKR option chain + model/market Greeks via shared session."""
    if not is_gateway_reachable(host, port, timeout=0.25):
        return None
    sym = symbol.strip().upper().replace(".", "-")

    async def _fetch(ib: Any) -> pd.DataFrame | None:
        from ib_async import Option

        try:
            stock = _make_ibkr_contract(sym)
            qualified = await ib.qualifyContractsAsync(stock)
            if not qualified:
                return None
            contract = qualified[0]
            chains = await ib.reqSecDefOptParamsAsync(contract.symbol, "", contract.secType, contract.conId)
            if not chains:
                return None
            smart_chain = next((c for c in chains if c.exchange == "SMART"), chains[0])
            today = datetime.now(timezone.utc).date()

            exp_candidates: list[tuple[str, int]] = []
            for exp_raw in smart_chain.expirations:
                try:
                    d = datetime.strptime(exp_raw, "%Y%m%d").date()
                    dte = (d - today).days
                    if dte >= 14:
                        exp_candidates.append((exp_raw, dte))
                except ValueError:
                    continue
            if not exp_candidates:
                return None

            in_win = [c for c in exp_candidates if target_dte_min <= c[1] <= target_dte_max]
            chosen_raw, chosen_dte = min(in_win or exp_candidates, key=lambda c: abs(c[1] - 35))
            chosen_iso = f"{chosen_raw[:4]}-{chosen_raw[4:6]}-{chosen_raw[6:8]}"

            # Pick strikes within +-15% of spot (max 14 strikes per side for fast lookup)
            strikes = sorted(
                k for k in smart_chain.strikes if 0.85 * spot_price <= k <= 1.15 * spot_price
            )
            if len(strikes) > 14:
                step = max(1, len(strikes) // 14)
                strikes = strikes[::step]

            opt_contracts = []
            for strike in strikes:
                for right in ("C", "P"):
                    opt_contracts.append(Option(contract.symbol, chosen_raw, float(strike), right, "SMART"))

            qualified_opts = await ib.qualifyContractsAsync(*opt_contracts)
            if not qualified_opts:
                return None
            try:
                tickers = await asyncio.wait_for(ib.reqTickersAsync(*qualified_opts), timeout=12.0)
            except asyncio.TimeoutError:
                tickers = [t for t in (ib.ticker(c) for c in qualified_opts) if t is not None]

            rows: list[dict[str, Any]] = []
            for tk in tickers:
                c = tk.contract
                greeks = tk.modelGreeks or tk.lastGreeks
                bid = float(tk.bid) if tk.bid and math.isfinite(tk.bid) and tk.bid > 0 else 0.0
                ask = float(tk.ask) if tk.ask and math.isfinite(tk.ask) and tk.ask > 0 else 0.0
                last_p = float(tk.last) if tk.last and math.isfinite(tk.last) and tk.last > 0 else 0.0
                mid = (bid + ask) / 2.0 if (bid > 0 and ask > 0) else (
                    float(greeks.optPrice) if greeks and greeks.optPrice and math.isfinite(greeks.optPrice) else last_p
                )
                if mid <= 0:
                    continue
                rows.append(
                    {
                        "option_type": "CALL" if c.right == "C" else "PUT",
                        "strike": float(c.strike),
                        "expiry": chosen_iso,
                        "dte": chosen_dte,
                        "bid": bid,
                        "ask": ask,
                        "mid_price": round(mid, 4),
                        "iv": round(float(greeks.impliedVol), 4) if greeks and greeks.impliedVol and math.isfinite(greeks.impliedVol) else None,
                        "oi": int(tk.callOpenInterest or tk.putOpenInterest or 0),
                        "volume": int(tk.volume or 0) if tk.volume and math.isfinite(tk.volume) else 0,
                        "delta": round(float(greeks.delta), 4) if greeks and greeks.delta is not None and math.isfinite(greeks.delta) else None,
                    }
                )
            if not rows:
                return None
            return pd.DataFrame(rows)
        except Exception as exc:
            logger.debug("IBKR option fetch fallback for %s: %s", sym, exc)
            return None

    return ibkr_worker.run(
        _fetch,
        host=host,
        port=port,
        client_id=client_id,
        priority=priority,
        is_hmds=False,
        dedup_key=f"ibkr:opt:{sym}",
        timeout=20.0,
        max_wait=5.0,
    )


def fetch_live_quote_ibkr(
    symbol: str,
    host: str,
    port: int,
    client_id: int,
    priority: int = Priority.HIGH,
) -> dict[str, Any] | None:
    """Read-only IBKR Gateway snapshot quote via shared session."""
    if not is_gateway_reachable(host, port, timeout=0.25):
        return None
    sym = symbol.strip().upper().replace(".", "-")

    async def _fetch(ib: Any) -> dict[str, Any] | None:
        try:
            contract = _make_ibkr_contract(sym)
            qualified = await ib.qualifyContractsAsync(contract)
            if not qualified:
                return None
            tickers = await ib.reqTickersAsync(qualified[0])
            if not tickers:
                return None
            tk = tickers[0]
            bid = float(tk.bid) if tk.bid and math.isfinite(tk.bid) and tk.bid > 0 else None
            ask = float(tk.ask) if tk.ask and math.isfinite(tk.ask) and tk.ask > 0 else None
            prev_close = float(tk.close) if tk.close and math.isfinite(tk.close) and tk.close > 0 else None
            mp = tk.marketPrice()
            price = (
                float(mp)
                if mp and math.isfinite(mp) and mp > 0
                else (
                    float(tk.last)
                    if tk.last and math.isfinite(tk.last) and tk.last > 0
                    else prev_close
                )
            )
            if price is None or price <= 0:
                return None
            chg_pct = ((price / prev_close) - 1.0) * 100.0 if prev_close and prev_close > 0 else 0.0
            return {
                "symbol": sym,
                "price": round(price, 2),
                "previous_close": round(prev_close, 2) if prev_close else None,
                "change_pct": round(chg_pct, 2),
                "day_high": float(tk.high) if tk.high and math.isfinite(tk.high) and tk.high > 0 else None,
                "day_low": float(tk.low) if tk.low and math.isfinite(tk.low) and tk.low > 0 else None,
                "bid": round(bid, 2) if bid else None,
                "ask": round(ask, 2) if ask else None,
                "source": "IBKR Gateway",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as exc:
            logger.debug("IBKR live quote fallback for %s: %s", sym, exc)
            return None

    return ibkr_worker.run(
        _fetch,
        host=host,
        port=port,
        client_id=client_id,
        priority=priority,
        is_hmds=False,
        dedup_key=f"ibkr:quote:{sym}",
        timeout=10.0,
        max_wait=3.0,
    )


def fetch_live_quotes_batch_ibkr(
    symbols: list[str],
    host: str,
    port: int,
    client_id: int,
    priority: int = Priority.HIGH,
) -> dict[str, dict[str, Any]]:
    """Read-only batch IBKR Gateway quote lookup via shared session."""
    unique = [s.strip().upper().replace(".", "-") for s in dict.fromkeys(symbols) if s.strip()][:40]
    if not unique or not is_gateway_reachable(host, port, timeout=0.25):
        return {}

    ibkr_to_db = {sym.replace("-", " "): sym for sym in unique}

    async def _fetch_batch(ib: Any) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        try:
            contracts = [_make_ibkr_contract(sym) for sym in unique]
            qualified = await asyncio.wait_for(ib.qualifyContractsAsync(*contracts), timeout=4.0)
            valid_contracts = [c for c in (qualified or []) if c is not None and getattr(c, "conId", 0)]
            if not valid_contracts:
                return out
            try:
                tickers = await asyncio.wait_for(ib.reqTickersAsync(*valid_contracts), timeout=8.5)
            except asyncio.TimeoutError:
                # Preserve only partial quotes belonging to this request on the shared IB session
                tickers = [t for t in (ib.ticker(c) for c in valid_contracts) if t is not None]
            now_iso = datetime.now(timezone.utc).isoformat()
            for tk in tickers or []:
                c = getattr(tk, "contract", None)
                raw_sym = str(getattr(c, "symbol", "") or "").upper()
                sym = ibkr_to_db.get(raw_sym, raw_sym)
                if not sym:
                    continue
                bid = float(tk.bid) if tk.bid and math.isfinite(tk.bid) and tk.bid > 0 else None
                ask = float(tk.ask) if tk.ask and math.isfinite(tk.ask) and tk.ask > 0 else None
                prev_close = float(tk.close) if tk.close and math.isfinite(tk.close) and tk.close > 0 else None
                mp = tk.marketPrice()
                price = (
                    float(mp)
                    if mp and math.isfinite(mp) and mp > 0
                    else (
                        float(tk.last)
                        if tk.last and math.isfinite(tk.last) and tk.last > 0
                        else prev_close
                    )
                )
                if price is None or price <= 0:
                    continue
                chg_pct = ((price / prev_close) - 1.0) * 100.0 if prev_close and prev_close > 0 else 0.0
                out[sym] = {
                    "symbol": sym,
                    "price": round(price, 2),
                    "previous_close": round(prev_close, 2) if prev_close else None,
                    "change_pct": round(chg_pct, 2),
                    "day_high": float(tk.high) if tk.high and math.isfinite(tk.high) and tk.high > 0 else None,
                    "day_low": float(tk.low) if tk.low and math.isfinite(tk.low) and tk.low > 0 else None,
                    "bid": round(bid, 2) if bid else None,
                    "ask": round(ask, 2) if ask else None,
                    "source": "IBKR Gateway",
                    "timestamp": now_iso,
                }
            return out
        except Exception as exc:
            logger.debug("IBKR batch quote fallback (%d symbols): %s", len(unique), exc)
            return out

    res = ibkr_worker.run(
        _fetch_batch,
        host=host,
        port=port,
        client_id=client_id,
        priority=priority,
        is_hmds=False,
        dedup_key=None,
        timeout=16.0,
        max_wait=3.0,
    )
    return res if isinstance(res, dict) else {}


def fetch_historical_bars_ibkr(
    symbol: str,
    timeframe: Literal["1d", "1h", "1w"],
    duration: str,
    host: str,
    port: int,
    client_id: int,
    priority: int = Priority.NORMAL,
    max_wait: float = 6.0,
) -> pd.DataFrame | None:
    """Read-only IBKR Gateway historical bar download paced through shared HMDS limiter."""
    if not is_gateway_reachable(host, port, timeout=0.25):
        return None
    sym = symbol.strip().upper().replace(".", "-")
    bar_size_map = {"1d": "1 day", "1h": "1 hour", "1w": "1 week"}
    bar_size = bar_size_map[timeframe]
    req_timeout = 6.0 if duration.strip().upper().endswith(("D", "M")) else 12.0

    async def _fetch(ib: Any) -> pd.DataFrame | None:
        try:
            contract = _make_ibkr_contract(sym)
            qualified = await ib.qualifyContractsAsync(contract)
            if not qualified:
                return None
            t0 = time.monotonic()
            bars = await ib.reqHistoricalDataAsync(
                qualified[0],
                endDateTime="",
                durationStr=duration,
                barSizeSetting=bar_size,
                whatToShow="TRADES",
                useRTH=True,
                formatDate=2,
                timeout=req_timeout,
            )
            if not bars and sym in ("^VIX", "VIX") and (time.monotonic() - t0) < 3.5:
                bars = await ib.reqHistoricalDataAsync(
                    qualified[0],
                    endDateTime="",
                    durationStr=duration,
                    barSizeSetting=bar_size,
                    whatToShow="MIDPOINT",
                    useRTH=True,
                    formatDate=2,
                    timeout=req_timeout,
                )
            if not bars:
                return None
            rows = []
            for b in bars:
                dt = b.date
                if isinstance(dt, datetime):
                    ts = int((dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp())
                else:
                    ts = int(datetime.combine(dt, datetime.min.time(), tzinfo=timezone.utc).timestamp())
                rows.append(
                    {
                        "ts": ts,
                        "open": float(b.open),
                        "high": float(b.high),
                        "low": float(b.low),
                        "close": float(b.close),
                        "volume": float(b.volume or 0.0),
                    }
                )
            df = pd.DataFrame(rows)
            return df.sort_values("ts").drop_duplicates(subset=["ts"]).reset_index(drop=True)
        except Exception as exc:
            logger.debug("IBKR historical bars fallback for %s (%s): %s", sym, timeframe, exc)
            return None

    return ibkr_worker.run(
        _fetch,
        host=host,
        port=port,
        client_id=client_id,
        priority=priority,
        is_hmds=True,
        dedup_key=f"ibkr:hist:{sym}:{timeframe}:{duration}",
        timeout=req_timeout + 6.0,
        max_wait=max_wait,
    )


class IbkrMarketDataPlugin(SourcePlugin):
    plugin_id = "market_data_ibkr"
    display_name = "IBKR Gateway (Read-Only Options & Quotes)"
    description = "Optional read-only connection to Interactive Brokers Gateway (localhost or LAN) for real option Greeks & IV."
