from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from app.core.black_scholes import bs_greeks, compute_historical_volatility, implied_volatility
from app.core.contracts import (
    OptionAnalysisContext,
    OptionLeg,
    OptionStrategyPlugin,
    OptionStrategyProposal,
    RiskFlag,
)


class SwingOptionsStrategyPlugin(OptionStrategyPlugin):
    plugin_id = "swing_options"
    display_name = "Swing Options Strategy Proposer"
    description = "Compares ATM IV vs HV20/HV60, checks liquidity & earnings collisions, and builds directional or neutral option spreads."

    def propose(self, ctx: OptionAnalysisContext) -> dict[str, Any]:
        hv20 = compute_historical_volatility(ctx.daily_df["close"], 20)
        hv60 = compute_historical_volatility(ctx.daily_df["close"], 60)

        risk_flags: list[RiskFlag] = []
        chain = ctx.chain_df
        if chain is None or chain.empty:
            return {
                "iv_metrics": {
                    "hv20": round(hv20 * 100, 1),
                    "hv60": round(hv60 * 100, 1),
                    "atm_iv": None,
                    "iv_hv_ratio": None,
                    "iv_regime": "normal",
                    "liquidity_ok": False,
                    "data_source": ctx.data_source,
                },
                "risk_flags": [],
                "proposals": [],
            }

        work = chain.copy()
        spot = float(ctx.spot_price)
        dte = int(work["dte"].iloc[0])
        expiry = str(work["expiry"].iloc[0])
        T = max(dte, 1) / 365.0

        # Fill missing IV and Delta analytically via Black-Scholes
        ivs: list[float] = []
        deltas: list[float] = []
        for row in work.itertuples(index=False):
            opt_type = str(row.option_type)
            strike = float(row.strike)
            mid = float(row.mid_price)
            raw_iv = getattr(row, "iv", None)
            iv_val = float(raw_iv) if raw_iv is not None and pd.notna(raw_iv) and 0.01 < float(raw_iv) < 4.0 else None
            if iv_val is None and mid > 0:
                iv_val = implied_volatility(mid, spot, strike, T, option_type=opt_type)  # type: ignore[arg-type]
            if iv_val is None:
                iv_val = max(hv20, 0.20)
            ivs.append(round(iv_val, 4))

            raw_delta = getattr(row, "delta", None)
            if raw_delta is not None and pd.notna(raw_delta) and math.isfinite(float(raw_delta)):
                deltas.append(round(float(raw_delta), 4))
            else:
                g = bs_greeks(spot, strike, T, sigma=iv_val, option_type=opt_type)  # type: ignore[arg-type]
                deltas.append(g["delta"])

        work["iv"] = ivs
        work["delta"] = deltas

        # ATM IV estimate
        work["dist_atm"] = (work["strike"] - spot).abs()
        atm_rows = work.nsmallest(4, "dist_atm")
        atm_iv = float(atm_rows["iv"].median()) if not atm_rows.empty else hv20
        iv_hv_ratio = atm_iv / max(hv20, 0.05)

        if iv_hv_ratio >= 1.15:
            iv_regime = "high"
        elif iv_hv_ratio <= 0.85:
            iv_regime = "low"
        else:
            iv_regime = "normal"

        # Liquidity check near ATM
        near_money = work[(work["strike"] >= spot * 0.88) & (work["strike"] <= spot * 1.12)]
        median_oi = float(near_money["oi"].median()) if not near_money.empty else 0.0
        spreads = []
        for row in near_money.itertuples(index=False):
            if row.mid_price > 0.05 and row.ask > 0:
                spreads.append((row.ask - row.bid) / row.mid_price)
        median_spread = float(np.median(spreads)) if spreads else 0.25
        liquidity_ok = median_oi >= 50 and median_spread <= 0.25

        if not liquidity_ok:
            risk_flags.append(
                RiskFlag(
                    code="ILLIQUID_OPTIONS",
                    label="Low Option Liquidity",
                    severity="warning",
                    detail=f"Median near-money OI={median_oi:.0f}, spread={median_spread * 100:.0f}%.",
                )
            )

        earnings_warning = None
        if ctx.days_to_earnings is not None and 0 <= ctx.days_to_earnings <= dte:
            earnings_warning = f"Expiration ({expiry}, {dte} DTE) crosses earnings in {ctx.days_to_earnings} days."

        calls = work[(work["option_type"] == "CALL") & (work["mid_price"] > 0.05)].sort_values("strike")
        puts = work[(work["option_type"] == "PUT") & (work["mid_price"] > 0.05)].sort_values("strike")

        proposals: list[OptionStrategyProposal] = []
        if ctx.bias == "bullish":
            if iv_regime == "high":
                p1 = self._build_bull_put_spread(puts, spot, ctx.nearest_support or ctx.channel_lower, expiry, dte, iv_regime, ctx.data_source, earnings_warning)
                if p1:
                    proposals.append(p1)
                p2 = self._build_cash_secured_put(puts, spot, ctx.nearest_support or ctx.channel_lower, expiry, dte, iv_regime, ctx.data_source, earnings_warning)
                if p2:
                    proposals.append(p2)
            else:
                p1 = self._build_bull_call_spread(calls, spot, ctx.nearest_resistance or ctx.channel_upper, expiry, dte, iv_regime, ctx.data_source, earnings_warning)
                if p1:
                    proposals.append(p1)
                p2 = self._build_long_option(calls, "CALL", spot, expiry, dte, iv_regime, ctx.data_source, earnings_warning)
                if p2:
                    proposals.append(p2)
        elif ctx.bias == "bearish":
            if iv_regime == "high":
                p1 = self._build_bear_call_spread(calls, spot, ctx.nearest_resistance or ctx.channel_upper, expiry, dte, iv_regime, ctx.data_source, earnings_warning)
                if p1:
                    proposals.append(p1)
            else:
                p1 = self._build_bear_put_spread(puts, spot, ctx.nearest_support or ctx.channel_lower, expiry, dte, iv_regime, ctx.data_source, earnings_warning)
                if p1:
                    proposals.append(p1)
                p2 = self._build_long_option(puts, "PUT", spot, expiry, dte, iv_regime, ctx.data_source, earnings_warning)
                if p2:
                    proposals.append(p2)
        else:
            # Neutral -> Iron Condor in high/normal IV or Debit Spread alternative
            ic = self._build_iron_condor(calls, puts, spot, ctx.channel_lower or ctx.nearest_support, ctx.channel_upper or ctx.nearest_resistance, expiry, dte, iv_regime, ctx.data_source, earnings_warning)
            if ic:
                proposals.append(ic)
            bcs = self._build_bull_call_spread(calls, spot, ctx.nearest_resistance, expiry, dte, iv_regime, ctx.data_source, earnings_warning)
            if bcs:
                proposals.append(bcs)

        return {
            "iv_metrics": {
                "hv20": round(hv20 * 100, 1),
                "hv60": round(hv60 * 100, 1),
                "atm_iv": round(atm_iv * 100, 1),
                "iv_hv_ratio": round(iv_hv_ratio, 2),
                "iv_regime": iv_regime,
                "liquidity_ok": liquidity_ok,
                "median_oi": int(median_oi),
                "data_source": ctx.data_source,
            },
            "risk_flags": risk_flags,
            "proposals": proposals,
        }

    def _to_leg(self, row: pd.Series, action: str) -> OptionLeg:
        return OptionLeg(
            action=action,  # type: ignore[arg-type]
            option_type=str(row["option_type"]),  # type: ignore[arg-type]
            strike=float(row["strike"]),
            expiry=str(row["expiry"]),
            dte=int(row["dte"]),
            mid_price=round(float(row["mid_price"]), 2),
            delta=round(float(row["delta"]), 3) if pd.notna(row["delta"]) else None,
            iv=round(float(row["iv"]) * 100, 1) if pd.notna(row["iv"]) else None,
            oi=int(row["oi"]),
            bid=round(float(row["bid"]), 2),
            ask=round(float(row["ask"]), 2),
        )

    def _build_bull_put_spread(
        self, puts: pd.DataFrame, spot: float, support: float | None, expiry: str, dte: int, iv_regime: str, data_source: str, warn: str | None
    ) -> OptionStrategyProposal | None:
        otm = puts[puts["strike"] < spot * 0.99]
        if len(otm) < 2:
            return None
        target_short = min(support, spot * 0.97) if support and support < spot else spot * 0.95
        short_row = otm.iloc[(otm["strike"] - target_short).abs().argsort()[:1]].iloc[0]
        lower_puts = otm[otm["strike"] < float(short_row["strike"])]
        if lower_puts.empty:
            return None
        target_long = float(short_row["strike"]) - max(2.5, spot * 0.04)
        long_row = lower_puts.iloc[(lower_puts["strike"] - target_long).abs().argsort()[:1]].iloc[0]

        width = float(short_row["strike"]) - float(long_row["strike"])
        credit = float(short_row["mid_price"]) - float(long_row["mid_price"])
        if width <= 0 or credit <= 0.05 or credit >= width:
            return None

        short_delta = abs(float(short_row["delta"] or 0.25))
        return OptionStrategyProposal(
            strategy_name="Bull Put Credit Spread",
            bias="bullish",
            rationale="Sell OTM put spread below support to harvest elevated implied volatility.",
            expiry=expiry,
            dte=dte,
            legs=[self._to_leg(short_row, "SELL"), self._to_leg(long_row, "BUY")],
            net_credit_debit=round(credit, 2),
            max_profit=round(credit * 100, 2),
            max_loss=round((width - credit) * 100, 2),
            breakeven=[round(float(short_row["strike"]) - credit, 2)],
            pop_estimate=round((1.0 - short_delta) * 100, 1),
            iv_regime=iv_regime,  # type: ignore[arg-type]
            data_source=data_source,
            earnings_warning=warn,
        )

    def _build_cash_secured_put(
        self, puts: pd.DataFrame, spot: float, support: float | None, expiry: str, dte: int, iv_regime: str, data_source: str, warn: str | None
    ) -> OptionStrategyProposal | None:
        otm = puts[puts["strike"] < spot * 0.98]
        if otm.empty:
            return None
        target = min(support, spot * 0.95) if support and support < spot else spot * 0.93
        short_row = otm.iloc[(otm["strike"] - target).abs().argsort()[:1]].iloc[0]
        credit = float(short_row["mid_price"])
        if credit <= 0.1:
            return None
        strike = float(short_row["strike"])
        short_delta = abs(float(short_row["delta"] or 0.25))
        return OptionStrategyProposal(
            strategy_name="Cash-Secured Put",
            bias="bullish",
            rationale="Collect premium to acquire shares at discount near technical support.",
            expiry=expiry,
            dte=dte,
            legs=[self._to_leg(short_row, "SELL")],
            net_credit_debit=round(credit, 2),
            max_profit=round(credit * 100, 2),
            max_loss=round((strike - credit) * 100, 2),
            breakeven=[round(strike - credit, 2)],
            pop_estimate=round((1.0 - short_delta) * 100, 1),
            iv_regime=iv_regime,  # type: ignore[arg-type]
            data_source=data_source,
            earnings_warning=warn,
        )

    def _build_bull_call_spread(
        self, calls: pd.DataFrame, spot: float, resistance: float | None, expiry: str, dte: int, iv_regime: str, data_source: str, warn: str | None
    ) -> OptionStrategyProposal | None:
        if len(calls) < 2:
            return None
        long_row = calls.iloc[(calls["strike"] - spot).abs().argsort()[:1]].iloc[0]
        higher_calls = calls[calls["strike"] > float(long_row["strike"])]
        if higher_calls.empty:
            return None
        target_short = max(float(long_row["strike"]) + max(2.5, spot * 0.04), resistance or spot * 1.05)
        short_row = higher_calls.iloc[(higher_calls["strike"] - target_short).abs().argsort()[:1]].iloc[0]

        width = float(short_row["strike"]) - float(long_row["strike"])
        debit = float(long_row["mid_price"]) - float(short_row["mid_price"])
        if width <= 0 or debit <= 0.05 or debit >= width:
            return None

        long_delta = abs(float(long_row["delta"] or 0.5))
        return OptionStrategyProposal(
            strategy_name="Bull Call Debit Spread",
            bias="bullish",
            rationale="Defined-risk directional call spread capping cost & vega exposure.",
            expiry=expiry,
            dte=dte,
            legs=[self._to_leg(long_row, "BUY"), self._to_leg(short_row, "SELL")],
            net_credit_debit=round(-debit, 2),
            max_profit=round((width - debit) * 100, 2),
            max_loss=round(debit * 100, 2),
            breakeven=[round(float(long_row["strike"]) + debit, 2)],
            pop_estimate=round(long_delta * 100 * 0.85, 1),
            iv_regime=iv_regime,  # type: ignore[arg-type]
            data_source=data_source,
            earnings_warning=warn,
        )

    def _build_bear_call_spread(
        self, calls: pd.DataFrame, spot: float, resistance: float | None, expiry: str, dte: int, iv_regime: str, data_source: str, warn: str | None
    ) -> OptionStrategyProposal | None:
        otm = calls[calls["strike"] > spot * 1.01]
        if len(otm) < 2:
            return None
        target_short = max(resistance, spot * 1.03) if resistance and resistance > spot else spot * 1.05
        short_row = otm.iloc[(otm["strike"] - target_short).abs().argsort()[:1]].iloc[0]
        higher_calls = otm[otm["strike"] > float(short_row["strike"])]
        if higher_calls.empty:
            return None
        target_long = float(short_row["strike"]) + max(2.5, spot * 0.04)
        long_row = higher_calls.iloc[(higher_calls["strike"] - target_long).abs().argsort()[:1]].iloc[0]

        width = float(long_row["strike"]) - float(short_row["strike"])
        credit = float(short_row["mid_price"]) - float(long_row["mid_price"])
        if width <= 0 or credit <= 0.05 or credit >= width:
            return None

        short_delta = abs(float(short_row["delta"] or 0.25))
        return OptionStrategyProposal(
            strategy_name="Bear Call Credit Spread",
            bias="bearish",
            rationale="Sell OTM call spread above resistance during elevated implied volatility.",
            expiry=expiry,
            dte=dte,
            legs=[self._to_leg(short_row, "SELL"), self._to_leg(long_row, "BUY")],
            net_credit_debit=round(credit, 2),
            max_profit=round(credit * 100, 2),
            max_loss=round((width - credit) * 100, 2),
            breakeven=[round(float(short_row["strike"]) + credit, 2)],
            pop_estimate=round((1.0 - short_delta) * 100, 1),
            iv_regime=iv_regime,  # type: ignore[arg-type]
            data_source=data_source,
            earnings_warning=warn,
        )

    def _build_bear_put_spread(
        self, puts: pd.DataFrame, spot: float, support: float | None, expiry: str, dte: int, iv_regime: str, data_source: str, warn: str | None
    ) -> OptionStrategyProposal | None:
        if len(puts) < 2:
            return None
        long_row = puts.iloc[(puts["strike"] - spot).abs().argsort()[:1]].iloc[0]
        lower_puts = puts[puts["strike"] < float(long_row["strike"])]
        if lower_puts.empty:
            return None
        target_short = min(float(long_row["strike"]) - max(2.5, spot * 0.04), support or spot * 0.95)
        short_row = lower_puts.iloc[(lower_puts["strike"] - target_short).abs().argsort()[:1]].iloc[0]

        width = float(long_row["strike"]) - float(short_row["strike"])
        debit = float(long_row["mid_price"]) - float(short_row["mid_price"])
        if width <= 0 or debit <= 0.05 or debit >= width:
            return None

        long_delta = abs(float(long_row["delta"] or 0.5))
        return OptionStrategyProposal(
            strategy_name="Bear Put Debit Spread",
            bias="bearish",
            rationale="Defined-risk downside swing spread with capped premium outlay.",
            expiry=expiry,
            dte=dte,
            legs=[self._to_leg(long_row, "BUY"), self._to_leg(short_row, "SELL")],
            net_credit_debit=round(-debit, 2),
            max_profit=round((width - debit) * 100, 2),
            max_loss=round(debit * 100, 2),
            breakeven=[round(float(long_row["strike"]) - debit, 2)],
            pop_estimate=round(long_delta * 100 * 0.85, 1),
            iv_regime=iv_regime,  # type: ignore[arg-type]
            data_source=data_source,
            earnings_warning=warn,
        )

    def _build_long_option(
        self, df: pd.DataFrame, opt_type: str, spot: float, expiry: str, dte: int, iv_regime: str, data_source: str, warn: str | None
    ) -> OptionStrategyProposal | None:
        if df.empty:
            return None
        target_delta = 0.65 if opt_type == "CALL" else -0.65
        row = df.iloc[(df["delta"].fillna(target_delta) - target_delta).abs().argsort()[:1]].iloc[0]
        price = float(row["mid_price"])
        if price <= 0.05:
            return None
        strike = float(row["strike"])
        be = strike + price if opt_type == "CALL" else strike - price
        return OptionStrategyProposal(
            strategy_name=f"Long {opt_type.title()} (~0.65Δ)",
            bias="bullish" if opt_type == "CALL" else "bearish",
            rationale="High-delta stock replacement taking advantage of normal/low IV.",
            expiry=expiry,
            dte=dte,
            legs=[self._to_leg(row, "BUY")],
            net_credit_debit=round(-price, 2),
            max_profit=None,
            max_loss=round(price * 100, 2),
            breakeven=[round(be, 2)],
            pop_estimate=round(abs(float(row["delta"] or 0.6)) * 100 * 0.8, 1),
            iv_regime=iv_regime,  # type: ignore[arg-type]
            data_source=data_source,
            earnings_warning=warn,
        )

    def _build_iron_condor(
        self,
        calls: pd.DataFrame,
        puts: pd.DataFrame,
        spot: float,
        low_anchor: float | None,
        high_anchor: float | None,
        expiry: str,
        dte: int,
        iv_regime: str,
        data_source: str,
        warn: str | None,
    ) -> OptionStrategyProposal | None:
        otm_puts = puts[puts["strike"] < spot * 0.98]
        otm_calls = calls[calls["strike"] > spot * 1.02]
        if len(otm_puts) < 2 or len(otm_calls) < 2:
            return None

        p_target = min(low_anchor, spot * 0.95) if low_anchor and low_anchor < spot else spot * 0.94
        c_target = max(high_anchor, spot * 1.05) if high_anchor and high_anchor > spot else spot * 1.06

        short_p = otm_puts.iloc[(otm_puts["strike"] - p_target).abs().argsort()[:1]].iloc[0]
        lower_puts = otm_puts[otm_puts["strike"] < float(short_p["strike"])]
        if lower_puts.empty:
            return None
        long_p = lower_puts.iloc[(lower_puts["strike"] - (float(short_p["strike"]) - max(2.5, spot * 0.04))).abs().argsort()[:1]].iloc[0]

        short_c = otm_calls.iloc[(otm_calls["strike"] - c_target).abs().argsort()[:1]].iloc[0]
        higher_calls = otm_calls[otm_calls["strike"] > float(short_c["strike"])]
        if higher_calls.empty:
            return None
        long_c = higher_calls.iloc[(higher_calls["strike"] - (float(short_c["strike"]) + max(2.5, spot * 0.04))).abs().argsort()[:1]].iloc[0]

        put_w = float(short_p["strike"]) - float(long_p["strike"])
        call_w = float(long_c["strike"]) - float(short_c["strike"])
        credit = (float(short_p["mid_price"]) - float(long_p["mid_price"])) + (float(short_c["mid_price"]) - float(long_c["mid_price"]))
        max_w = max(put_w, call_w)
        if credit <= 0.1 or credit >= max_w:
            return None

        pop = max(35.0, min(85.0, (1.0 - abs(float(short_p["delta"] or 0.2)) - abs(float(short_c["delta"] or 0.2))) * 100))
        return OptionStrategyProposal(
            strategy_name="Iron Condor (Range-Bound)",
            bias="neutral",
            rationale="Collect credit on both sides outside the horizontal/channel boundaries.",
            expiry=expiry,
            dte=dte,
            legs=[
                self._to_leg(short_p, "SELL"),
                self._to_leg(long_p, "BUY"),
                self._to_leg(short_c, "SELL"),
                self._to_leg(long_c, "BUY"),
            ],
            net_credit_debit=round(credit, 2),
            max_profit=round(credit * 100, 2),
            max_loss=round((max_w - credit) * 100, 2),
            breakeven=[round(float(short_p["strike"]) - credit, 2), round(float(short_c["strike"]) + credit, 2)],
            pop_estimate=round(pop, 1),
            iv_regime=iv_regime,  # type: ignore[arg-type]
            data_source=data_source,
            earnings_warning=warn,
        )
