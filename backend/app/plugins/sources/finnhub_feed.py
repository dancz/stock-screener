from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.core.contracts import SourcePlugin

logger = logging.getLogger(__name__)


def fetch_finnhub_enrichment(symbol: str, api_key: str) -> dict[str, Any]:
    """Fetch recent news and analyst recommendation trends if Finnhub API key is configured."""
    if not api_key.strip():
        return {}

    now = datetime.now(timezone.utc).date()
    from_date = (now - timedelta(days=14)).isoformat()
    to_date = now.isoformat()
    out: dict[str, Any] = {}

    try:
        with httpx.Client(timeout=6.0) as client:
            news_resp = client.get(
                "https://finnhub.io/api/v1/company-news",
                params={"symbol": symbol.upper(), "from": from_date, "to": to_date, "token": api_key.strip()},
            )
            if news_resp.status_code == 200:
                articles = news_resp.json()[:5]
                out["news"] = [
                    {
                        "headline": a.get("headline", ""),
                        "source": a.get("source", ""),
                        "url": a.get("url", ""),
                        "datetime": a.get("datetime"),
                    }
                    for a in articles
                    if a.get("headline")
                ]

            rec_resp = client.get(
                "https://finnhub.io/api/v1/stock/recommendation",
                params={"symbol": symbol.upper(), "token": api_key.strip()},
            )
            if rec_resp.status_code == 200:
                recs = rec_resp.json()
                if recs:
                    latest = recs[0]
                    out["analyst_breakdown"] = {
                        "strong_buy": latest.get("strongBuy", 0),
                        "buy": latest.get("buy", 0),
                        "hold": latest.get("hold", 0),
                        "sell": latest.get("sell", 0),
                        "strong_sell": latest.get("strongSell", 0),
                        "period": latest.get("period", ""),
                    }
    except Exception as exc:
        logger.debug("Finnhub enrichment skipped for %s: %s", symbol, exc)

    return out


class FinnhubFeedPlugin(SourcePlugin):
    plugin_id = "finnhub_feed"
    display_name = "Finnhub News & Analyst Trends"
    description = "Optional free-tier API enrichment for recent company news and analyst consensus breakdowns."
