from __future__ import annotations

import asyncio
import logging
import math
import socket
from datetime import datetime, timezone
from typing import Any, Literal

import pandas as pd

from app.core.contracts import SourcePlugin

logger = logging.getLogger(__name__)


def is_gateway_reachable(host: str, port: int, timeout: float = 0.25) -> bool:
    """Fast TCP probe so offline IBKR Gateway adds <0.25s overhead."""
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
) -> pd.DataFrame | None:
    """Read-only fetch of IBKR option chain + model/market Greeks. Returns None on fallback."""
    if not is_gateway_reachable(host, port, timeout=0.25):
        return None

    async def _fetch() -> pd.DataFrame | None:
        from ib_async import IB, Option, Stock

        ib = IB()
        try:
            await ib.connectAsync(host, int(port), clientId=int(client_id) + 1, timeout=3.0, readonly=True)
            ib.reqMarketDataType(3)  # Delayed data allowed if real-time subscription is absent
            stock = Stock(symbol, "SMART", "USD")
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
                    opt_contracts.append(Option(symbol, chosen_raw, float(strike), right, "SMART"))

            qualified_opts = await ib.qualifyContractsAsync(*opt_contracts)
            if not qualified_opts:
                return None
            tickers = await ib.reqTickersAsync(*qualified_opts)

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
            logger.debug("IBKR option fetch fallback for %s: %s", symbol, exc)
            return None
        finally:
            if ib.isConnected():
                ib.disconnect()

    try:
        return _run_async(_fetch())
    except Exception:
        return None


def fetch_live_quote_ibkr(
    symbol: str,
    host: str,
    port: int,
    client_id: int,
) -> dict[str, Any] | None:
    """Read-only IBKR Gateway snapshot quote (supports delayed data when live subscription absent)."""
    if not is_gateway_reachable(host, port, timeout=0.25):
        return None

    async def _fetch() -> dict[str, Any] | None:
        from ib_async import IB, Stock

        ib = IB()
        try:
            await ib.connectAsync(host, int(port), clientId=int(client_id) + 2, timeout=3.0, readonly=True)
            ib.reqMarketDataType(3)
            contract = Stock(symbol.upper(), "SMART", "USD")
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
                "symbol": symbol.upper(),
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
            logger.debug("IBKR live quote fallback for %s: %s", symbol, exc)
            return None
        finally:
            if ib.isConnected():
                ib.disconnect()

    try:
        return _run_async(_fetch())
    except Exception:
        return None


def fetch_historical_bars_ibkr(
    symbol: str,
    timeframe: Literal["1d", "1h", "1w"],
    duration: str,
    host: str,
    port: int,
    client_id: int,
) -> pd.DataFrame | None:
    """Read-only IBKR Gateway historical bar download with transparent fallback on failure."""
    if not is_gateway_reachable(host, port, timeout=0.25):
        return None

    bar_size_map = {"1d": "1 day", "1h": "1 hour", "1w": "1 week"}
    bar_size = bar_size_map[timeframe]

    async def _fetch() -> pd.DataFrame | None:
        from ib_async import IB, Stock

        ib = IB()
        try:
            await ib.connectAsync(host, int(port), clientId=int(client_id) + 3, timeout=3.0, readonly=True)
            contract = Stock(symbol.upper(), "SMART", "USD")
            qualified = await ib.qualifyContractsAsync(contract)
            if not qualified:
                return None
            bars = await ib.reqHistoricalDataAsync(
                qualified[0],
                endDateTime="",
                durationStr=duration,
                barSizeSetting=bar_size,
                whatToShow="TRADES",
                useRTH=True,
                formatDate=2,
                timeout=12.0,
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
            logger.debug("IBKR historical bars fallback for %s (%s): %s", symbol, timeframe, exc)
            return None
        finally:
            if ib.isConnected():
                ib.disconnect()

    try:
        return _run_async(_fetch())
    except Exception:
        return None


class IbkrMarketDataPlugin(SourcePlugin):
    plugin_id = "market_data_ibkr"
    display_name = "IBKR Gateway (Read-Only Options & Quotes)"
    description = "Optional read-only connection to Interactive Brokers Gateway (localhost or LAN) for real option Greeks & IV."
