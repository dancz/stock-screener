from __future__ import annotations

from app.core.contracts import (
    AnalysisContext,
    AnalyzerPlugin,
    AnalyzerResult,
    MetricItem,
    RiskFlag,
    Signal,
)


class EventsEarningsAnalyzer(AnalyzerPlugin):
    plugin_id = "events_earnings"
    display_name = "Events, Earnings & Catalysts"
    description = "Tracks upcoming earnings dates (binary gap risk), ex-dividend dates, and optional Finnhub analyst consensus & headlines."
    category = "catalyst"
    default_weight = 0.06
    requires_deep_data = True

    def analyze(self, ctx: AnalysisContext) -> AnalyzerResult:
        ev = ctx.events or {}
        if not ev:
            return AnalyzerResult(
                plugin_id=self.plugin_id,
                display_name=self.display_name,
                category=self.category,
                score=55.0,
                bias="neutral",
                confidence=0.2,
                summary="Pending earnings & catalyst enrichment.",
            )

        days_to_earn = ev.get("days_to_earnings")
        next_earn = ev.get("next_earnings_date")
        ex_div = ev.get("ex_dividend_date")
        rec = (ev.get("recommendation") or "").lower()
        target = ev.get("analyst_target")
        last_close = float(ctx.daily_df["close"].iloc[-1]) if ctx.daily_df is not None and not ctx.daily_df.empty else 0.0

        score = 60.0
        bias = "neutral"
        signals: list[Signal] = []
        risk_flags: list[RiskFlag] = []

        if days_to_earn is not None:
            if 0 <= days_to_earn <= 7:
                score -= 22.0
                risk_flags.append(
                    RiskFlag(
                        code="EARNINGS_WITHIN_7D",
                        label=f"Earnings in {days_to_earn}d ({next_earn})",
                        severity="high",
                        detail="Binary overnight gap risk — avoid unhedged swing size or adjust option expiration.",
                    )
                )
            elif 8 <= days_to_earn <= 14:
                score -= 8.0
                risk_flags.append(
                    RiskFlag(
                        code="EARNINGS_WITHIN_14D",
                        label=f"Earnings in {days_to_earn}d",
                        severity="warning",
                        detail="Upcoming earnings within 2 weeks; expect rising IV.",
                    )
                )
            elif days_to_earn > 21:
                score += 12.0
                signals.append(
                    Signal(
                        code="CLEAN_EARNINGS_RUNWAY",
                        label=f"Clean Runway ({days_to_earn}d to Earnings)",
                        direction="neutral",
                        severity="low",
                        score_impact=8.0,
                    )
                )

        if target and last_close > 0:
            upside_pct = ((float(target) / last_close) - 1.0) * 100.0
            if upside_pct >= 15.0 and rec in ("buy", "strong_buy"):
                score += 10.0
                bias = "bullish"
                signals.append(
                    Signal(
                        code="ANALYST_UPSIDE",
                        label=f"Analyst Target +{upside_pct:.0f}% ({rec.replace('_', ' ').title()})",
                        direction="bullish",
                        severity="low",
                        score_impact=8.0,
                    )
                )

        score = min(95.0, max(15.0, score))
        if days_to_earn is not None and days_to_earn > 0:
            summary = f"Earnings in {days_to_earn}d ({next_earn})"
        elif days_to_earn == 0:
            summary = f"Earnings Today ({next_earn})"
        elif days_to_earn is not None and days_to_earn < 0:
            summary = f"Last earnings reported {abs(days_to_earn)}d ago ({next_earn})"
        else:
            summary = "No imminent earnings date detected"

        if next_earn and days_to_earn is not None:
            if days_to_earn < 0:
                earn_label = "Last Earnings"
                earn_formatted = f"{next_earn} (Reported {abs(days_to_earn)}d ago)"
            elif days_to_earn == 0:
                earn_label = "Next Earnings"
                earn_formatted = f"{next_earn} (Today)"
            else:
                earn_label = "Next Earnings"
                earn_formatted = f"{next_earn} ({days_to_earn}d)"
        else:
            earn_label = "Next Earnings"
            earn_formatted = next_earn or "N/A"

        metrics = {
            "next_earnings_date": MetricItem(
                label=earn_label,
                value=next_earn,
                formatted=earn_formatted,
                status="bad" if (days_to_earn is not None and 0 <= days_to_earn <= 7) else "neutral",
            ),
            "ex_dividend_date": MetricItem(
                label="Ex-Dividend",
                value=ex_div,
                formatted=ex_div or "N/A",
            ),
            "analyst_target": MetricItem(
                label="Analyst Target",
                value=target,
                formatted=f"${target:.2f}" if target else "N/A",
            ),
        }

        return AnalyzerResult(
            plugin_id=self.plugin_id,
            display_name=self.display_name,
            category=self.category,
            score=round(score, 1),
            bias=bias,
            confidence=0.8,
            summary=summary,
            signals=signals,
            risk_flags=risk_flags,
            metrics=metrics,
        )
