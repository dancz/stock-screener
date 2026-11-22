from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from app.core.contracts import BenchmarkSnapshot, MarketRegime

BENCHMARKS: dict[str, str] = {
    "SPY": "S&P 500 ETF",
    "QQQ": "Nasdaq 100 ETF",
    "IWM": "Russell 2000 ETF",
    "^VIX": "CBOE Volatility Index",
}

SECTORS: dict[str, str] = {
    "XLK": "Technology",
    "XLF": "Financials",
    "XLE": "Energy",
    "XLV": "Health Care",
    "XLI": "Industrials",
    "XLY": "Consumer Disc.",
    "XLP": "Consumer Staples",
    "XLU": "Utilities",
    "XLB": "Materials",
    "XLRE": "Real Estate",
    "XLC": "Comm. Services",
}


def _snapshot_from_df(symbol: str, name: str, df: pd.DataFrame | None) -> BenchmarkSnapshot | None:
    if df is None or len(df) < 5:
        return None
    closes = df["close"].astype(float).dropna()
    if len(closes) < 5:
        return None
    price = float(closes.iloc[-1])
    prev_1d = float(closes.iloc[-2])
    idx_5d = -6 if len(closes) >= 6 else 0
    prev_5d = float(closes.iloc[idx_5d])
    ema20 = float(closes.ewm(span=20, adjust=False).mean().iloc[-1])
    ema50 = float(closes.ewm(span=50, adjust=False).mean().iloc[-1])

    chg_1d = ((price / prev_1d) - 1.0) * 100.0 if prev_1d > 0 else 0.0
    chg_5d = ((price / prev_5d) - 1.0) * 100.0 if prev_5d > 0 else 0.0

    return BenchmarkSnapshot(
        symbol=symbol,
        name=name,
        price=round(price, 2),
        change_1d_pct=round(chg_1d, 2),
        change_5d_pct=round(chg_5d, 2),
        above_ema20=price > ema20,
        above_ema50=price > ema50,
    )


def compute_market_regime(index_frames: dict[str, pd.DataFrame]) -> MarketRegime:
    bench_snaps: list[BenchmarkSnapshot] = []
    for sym, label in BENCHMARKS.items():
        snap = _snapshot_from_df(sym, label, index_frames.get(sym))
        if snap is not None:
            bench_snaps.append(snap)

    sector_snaps: list[BenchmarkSnapshot] = []
    for sym, label in SECTORS.items():
        snap = _snapshot_from_df(sym, label, index_frames.get(sym))
        if snap is not None:
            sector_snaps.append(snap)
    sector_snaps.sort(key=lambda s: s.change_5d_pct, reverse=True)

    spy = next((b for b in bench_snaps if b.symbol == "SPY"), None)
    qqq = next((b for b in bench_snaps if b.symbol == "QQQ"), None)
    vix = next((b for b in bench_snaps if b.symbol == "^VIX"), None)

    bullish_points = 0
    if spy:
        bullish_points += int(spy.above_ema20) + int(spy.above_ema50)
    if qqq:
        bullish_points += int(qqq.above_ema20) + int(qqq.above_ema50)
    vix_price = vix.price if vix else 18.0

    if bullish_points >= 3 and vix_price < 22.0:
        regime = "RISK_ON"
        summary = f"Risk-On: SPY/QQQ trending above key EMAs, VIX at {vix_price:.1f}"
    elif bullish_points <= 1 or vix_price >= 26.0:
        regime = "RISK_OFF"
        summary = f"Risk-Off: Broad index weakness or elevated VIX ({vix_price:.1f}) — tighten sizing"
    else:
        regime = "NEUTRAL"
        summary = f"Neutral / Mixed Tape: Selective setups favored, VIX at {vix_price:.1f}"

    return MarketRegime(
        regime=regime,
        summary=summary,
        benchmarks=bench_snaps,
        sectors=sector_snaps,
        updated_at=datetime.now(timezone.utc).isoformat(),
    )
