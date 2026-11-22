from __future__ import annotations

import math
from typing import Literal

import numpy as np
import pandas as pd
from scipy.optimize import brentq
from scipy.stats import norm


def compute_historical_volatility(closes: pd.Series, window: int = 20) -> float:
    if closes is None or len(closes) < window + 2:
        return 0.25
    clean = closes.astype(float).replace([np.inf, -np.inf], np.nan).dropna()
    if len(clean) < window + 2:
        return 0.25
    log_ret = np.log(clean / clean.shift(1)).dropna()
    if len(log_ret) < window:
        return 0.25
    vol = float(log_ret.tail(window).std(ddof=1) * math.sqrt(252.0))
    if not math.isfinite(vol) or vol <= 0:
        return 0.25
    return round(vol, 4)


def bs_price(
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    option_type: Literal["CALL", "PUT"] = "CALL",
) -> float:
    if S <= 0 or K <= 0:
        return 0.0
    if T <= 1e-6 or sigma <= 1e-6:
        if option_type == "CALL":
            return max(0.0, S - K * math.exp(-r * max(T, 0.0)))
        return max(0.0, K * math.exp(-r * max(T, 0.0)) - S)

    sqrt_t = math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t

    if option_type == "CALL":
        return float(S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2))
    return float(K * math.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1))


def bs_greeks(
    S: float,
    K: float,
    T: float,
    r: float = 0.0425,
    sigma: float = 0.30,
    option_type: Literal["CALL", "PUT"] = "CALL",
) -> dict[str, float]:
    """Return analytical Black-Scholes price and Greeks (theta per day, vega per 1% IV)."""
    if S <= 0 or K <= 0 or T <= 1e-6 or sigma <= 1e-6:
        intrinsic = max(0.0, S - K) if option_type == "CALL" else max(0.0, K - S)
        delta = (1.0 if S > K else 0.0) if option_type == "CALL" else (-1.0 if K > S else 0.0)
        return {"price": intrinsic, "delta": delta, "gamma": 0.0, "theta": 0.0, "vega": 0.0}

    sqrt_t = math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    pdf_d1 = float(norm.pdf(d1))
    disc = math.exp(-r * T)

    if option_type == "CALL":
        price = float(S * norm.cdf(d1) - K * disc * norm.cdf(d2))
        delta = float(norm.cdf(d1))
        theta_annual = -(S * pdf_d1 * sigma) / (2.0 * sqrt_t) - r * K * disc * float(norm.cdf(d2))
    else:
        price = float(K * disc * norm.cdf(-d2) - S * norm.cdf(-d1))
        delta = float(norm.cdf(d1) - 1.0)
        theta_annual = -(S * pdf_d1 * sigma) / (2.0 * sqrt_t) + r * K * disc * float(norm.cdf(-d2))

    gamma = pdf_d1 / (S * sigma * sqrt_t)
    vega_1pct = (S * pdf_d1 * sqrt_t) / 100.0
    theta_daily = theta_annual / 365.0

    return {
        "price": round(price, 4),
        "delta": round(delta, 4),
        "gamma": round(gamma, 6),
        "theta": round(theta_daily, 4),
        "vega": round(vega_1pct, 4),
    }


def implied_volatility(
    market_price: float,
    S: float,
    K: float,
    T: float,
    r: float = 0.0425,
    option_type: Literal["CALL", "PUT"] = "CALL",
) -> float | None:
    if market_price <= 0 or S <= 0 or K <= 0 or T <= 1e-6:
        return None
    intrinsic = max(0.0, S - K * math.exp(-r * T)) if option_type == "CALL" else max(0.0, K * math.exp(-r * T) - S)
    if market_price <= intrinsic + 1e-4:
        return None

    def obj(vol: float) -> float:
        return bs_price(S, K, T, r, vol, option_type) - market_price

    try:
        iv = brentq(obj, 0.01, 4.0, xtol=1e-4, maxiter=50)
        return round(float(iv), 4)
    except Exception:
        return None
