from __future__ import annotations

from app.core.contracts import (
    AnalysisContext,
    AnalyzerPlugin,
    AnalyzerResult,
    MetricItem,
    RiskFlag,
    Signal,
)


class FundamentalHealthAnalyzer(AnalyzerPlugin):
    plugin_id = "fundamental_health"
    display_name = "Company Financial Health"
    description = "Grades valuation, profitability margins, ROE, revenue/EPS growth, balance sheet liquidity, and Free Cash Flow yield."
    category = "fundamental"
    default_weight = 0.12
    requires_deep_data = True

    def analyze(self, ctx: AnalysisContext) -> AnalyzerResult:
        f = ctx.fundamentals or {}
        if not f or (f.get("market_cap") is None and f.get("pe_trailing") is None and f.get("profit_margin") is None):
            return AnalyzerResult(
                plugin_id=self.plugin_id,
                display_name=self.display_name,
                category=self.category,
                score=55.0,
                bias="neutral",
                confidence=0.2,
                summary="Pending deep fundamental enrichment.",
            )

        points = 50.0
        risk_flags: list[RiskFlag] = []
        signals: list[Signal] = []

        op_margin = f.get("operating_margin")
        prof_margin = f.get("profit_margin")
        roe = f.get("roe")
        rev_growth = f.get("revenue_growth")
        eps_growth = f.get("earnings_growth")
        pe_fwd = f.get("pe_forward")
        peg = f.get("peg_ratio")
        de = f.get("debt_to_equity")
        curr_ratio = f.get("current_ratio")
        fcf_yield = f.get("fcf_yield_pct")
        fcf = f.get("free_cash_flow")
        mcap = f.get("market_cap")

        # Profitability
        if op_margin is not None:
            if op_margin >= 20.0:
                points += 10.0
            elif op_margin >= 10.0:
                points += 5.0
            elif op_margin < 0.0:
                points -= 10.0

        if roe is not None:
            if roe >= 18.0:
                points += 8.0
            elif roe < 0.0:
                points -= 6.0

        # Growth
        if rev_growth is not None:
            if rev_growth >= 15.0:
                points += 10.0
                signals.append(
                    Signal(
                        code="STRONG_REV_GROWTH",
                        label=f"Strong Revenue Growth (+{rev_growth:.1f}%)",
                        direction="bullish",
                        severity="medium",
                        score_impact=8.0,
                    )
                )
            elif rev_growth < -5.0:
                points -= 8.0

        if eps_growth is not None and eps_growth >= 20.0:
            points += 6.0

        # Valuation
        if pe_fwd is not None:
            if 8.0 <= pe_fwd <= 25.0:
                points += 6.0
            elif pe_fwd > 65.0 or pe_fwd < 0:
                points -= 6.0
                risk_flags.append(
                    RiskFlag(
                        code="EXTREME_VALUATION",
                        label=f"High Valuation (Fwd P/E {pe_fwd:.1f})",
                        severity="info",
                        detail="Elevated forward earnings multiple increases drawdown sensitivity.",
                    )
                )

        if peg is not None and 0.3 <= peg <= 1.8:
            points += 5.0

        # Balance Sheet & Cash Flow
        if de is not None:
            if de < 80.0:
                points += 5.0
            elif de > 220.0:
                points -= 10.0
                risk_flags.append(
                    RiskFlag(
                        code="HIGH_DEBT",
                        label=f"High Debt/Equity ({de:.0f}%)",
                        severity="warning",
                        detail="Elevated balance sheet leverage.",
                    )
                )

        if curr_ratio is not None and curr_ratio >= 1.4:
            points += 4.0

        if fcf is not None:
            if fcf < 0:
                points -= 10.0
                risk_flags.append(
                    RiskFlag(
                        code="NEGATIVE_FCF",
                        label="Negative Free Cash Flow",
                        severity="warning",
                        detail="Company is currently cash-flow negative.",
                    )
                )
            elif fcf_yield is not None and fcf_yield >= 3.0:
                points += 8.0
                signals.append(
                    Signal(
                        code="STRONG_FCF",
                        label=f"Strong FCF Yield ({fcf_yield:.1f}%)",
                        direction="bullish",
                        severity="medium",
                        score_impact=7.0,
                    )
                )

        if mcap is not None and mcap < 1_000_000_000:
            risk_flags.append(
                RiskFlag(
                    code="SMALL_CAP_RISK",
                    label="Small-Cap (<$1B)",
                    severity="info",
                    detail="Higher gap and liquidity risk than large-cap peers.",
                )
            )

        short_pct = f.get("short_pct_float")
        if short_pct is not None and short_pct >= 15.0:
            risk_flags.append(
                RiskFlag(
                    code="HIGH_SHORT_INTEREST",
                    label=f"High Short Interest ({short_pct:.1f}%)",
                    severity="warning",
                    detail="Elevated short float increases short-squeeze volatility.",
                )
            )

        score = min(98.0, max(10.0, points))
        grade = _score_to_grade(score)
        bias = "bullish" if score >= 68 else ("bearish" if score <= 36 else "neutral")

        metrics = {
            "health_grade": MetricItem(
                label="Health Grade",
                value=grade,
                formatted=grade,
                status="good" if grade.startswith(("A", "B")) else ("bad" if grade in ("D", "F") else "neutral"),
            ),
            "pe_forward": MetricItem(
                label="Forward P/E",
                value=pe_fwd,
                formatted=f"{pe_fwd:.1f}" if pe_fwd is not None else "N/A",
            ),
            "operating_margin": MetricItem(
                label="Op. Margin",
                value=op_margin,
                formatted=f"{op_margin:.1f}%" if op_margin is not None else "N/A",
                status="good" if (op_margin or 0) >= 15 else "neutral",
            ),
            "revenue_growth": MetricItem(
                label="Rev Growth YoY",
                value=rev_growth,
                formatted=f"{rev_growth:+.1f}%" if rev_growth is not None else "N/A",
                status="good" if (rev_growth or 0) >= 10 else ("bad" if (rev_growth or 0) < 0 else "neutral"),
            ),
            "debt_to_equity": MetricItem(
                label="Debt / Equity",
                value=de,
                formatted=f"{de:.0f}%" if de is not None else "N/A",
                status="bad" if (de or 0) > 200 else "neutral",
            ),
            "fcf_yield": MetricItem(
                label="FCF Yield",
                value=fcf_yield,
                formatted=f"{fcf_yield:.1f}%" if fcf_yield is not None else "N/A",
                status="good" if (fcf_yield or 0) >= 3 else ("bad" if (fcf or 0) < 0 else "neutral"),
            ),
        }

        return AnalyzerResult(
            plugin_id=self.plugin_id,
            display_name=self.display_name,
            category=self.category,
            score=round(score, 1),
            bias=bias,
            confidence=0.85,
            summary=f"Health Grade {grade} | Fwd P/E {pe_fwd:.1f}" if pe_fwd else f"Financial Health Grade {grade}",
            signals=signals,
            risk_flags=risk_flags,
            metrics=metrics,
        )


def _score_to_grade(score: float) -> str:
    if score >= 88:
        return "A+"
    if score >= 80:
        return "A"
    if score >= 72:
        return "B+"
    if score >= 64:
        return "B"
    if score >= 52:
        return "C"
    if score >= 38:
        return "D"
    return "F"
