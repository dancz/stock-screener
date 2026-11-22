from __future__ import annotations

from app.core.contracts import (
    AnalysisContext,
    AnalyzerPlugin,
    AnalyzerResult,
    MetricItem,
    Signal,
)


class InsiderActivityAnalyzer(AnalyzerPlugin):
    plugin_id = "insider_activity"
    display_name = "Insider Activity"
    description = "Scores recent OpenInsider cluster buys, CEO/CFO open-market purchases, and SEC Form 4 activity."
    category = "sentiment"
    default_weight = 0.06
    requires_deep_data = False

    def analyze(self, ctx: AnalysisContext) -> AnalyzerResult:
        trades = ctx.insiders or []
        if not trades:
            return AnalyzerResult(
                plugin_id=self.plugin_id,
                display_name=self.display_name,
                category=self.category,
                score=50.0,
                bias="neutral",
                confidence=0.4,
                summary="No recent open-market cluster/C-suite insider buys detected.",
                metrics={
                    "recent_buys_count": MetricItem(label="Recent Insider Buys", value=0, formatted="0"),
                    "net_buy_usd": MetricItem(label="Net Insider Buy ($)", value=0.0, formatted="$0"),
                },
            )

        cluster_hits = sum(1 for t in trades if t.get("tag") == "cluster_buy")
        c_suite_hits = sum(
            1
            for t in trades
            if t.get("tag") == "ceo_cfo_buy"
            or any(role in str(t.get("title", "")).upper() for role in ("CEO", "CFO", "PRES", "COB"))
        )
        total_buy_usd = sum(float(t.get("value_usd") or 0.0) for t in trades if float(t.get("value_usd") or 0.0) > 0)

        signals: list[Signal] = []
        score = 55.0

        if cluster_hits > 0 or len(trades) >= 2:
            score += 24.0
            signals.append(
                Signal(
                    code="INSIDER_CLUSTER_BUY",
                    label=f"Insider Cluster Buy ({len(trades)} filings)",
                    direction="bullish",
                    severity="high",
                    score_impact=22.0,
                    detail=f"Multiple insiders buying open-market shares (${total_buy_usd:,.0f}).",
                )
            )

        if c_suite_hits > 0:
            score += 14.0
            signals.append(
                Signal(
                    code="CEO_CFO_BUY",
                    label="CEO / CFO Open-Market Buy",
                    direction="bullish",
                    severity="high",
                    score_impact=14.0,
                    detail="Executive C-suite purchase recorded.",
                )
            )

        if total_buy_usd >= 250_000:
            score += 8.0

        score = min(98.0, score)
        summary = f"{len(trades)} insider buy record(s) totaling ${total_buy_usd:,.0f}" if total_buy_usd > 0 else f"{len(trades)} recent insider Form 4 filing(s)"

        metrics = {
            "recent_buys_count": MetricItem(
                label="Recent Insider Buys",
                value=len(trades),
                formatted=str(len(trades)),
                status="good" if len(trades) > 0 else "neutral",
            ),
            "net_buy_usd": MetricItem(
                label="Net Insider Buy ($)",
                value=round(total_buy_usd, 0),
                formatted=f"${total_buy_usd:,.0f}",
                status="good" if total_buy_usd >= 50_000 else "neutral",
            ),
        }

        return AnalyzerResult(
            plugin_id=self.plugin_id,
            display_name=self.display_name,
            category=self.category,
            score=round(score, 1),
            bias="bullish" if (cluster_hits or c_suite_hits or total_buy_usd > 0) else "neutral",
            confidence=0.85,
            summary=summary,
            signals=signals,
            metrics=metrics,
        )
