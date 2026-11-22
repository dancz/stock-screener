from __future__ import annotations

import logging
from typing import Any

import httpx

from app.core.contracts import DiscoveredTicker, SourcePlugin

logger = logging.getLogger(__name__)

YAHOO_SCREENER_URLS: list[tuple[str, str]] = [
    ("https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved?formatted=false&scrIds=most_actives&count=25", "yahoo_most_active"),
    ("https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved?formatted=false&scrIds=day_gainers&count=25", "yahoo_gainers"),
    ("https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved?formatted=false&scrIds=day_losers&count=20", "yahoo_losers"),
    ("https://query1.finance.yahoo.com/v1/finance/trending/US?count=20", "yahoo_trending"),
]


class YahooMoversPlugin(SourcePlugin):
    plugin_id = "yahoo_movers"
    display_name = "Yahoo Finance Movers & Trending"
    description = "Pulls US most active, day gainers/losers, and trending tickers from Yahoo Finance."

    def discover(self, settings: dict[str, Any]) -> list[DiscoveredTicker]:
        if not settings.get("enabled_plugins", {}).get(self.plugin_id, True):
            return []

        found: dict[str, DiscoveredTicker] = {}
        headers = {"User-Agent": "Mozilla/5.0"}

        try:
            with httpx.Client(timeout=8.0, headers=headers, follow_redirects=True) as client:
                for url, tag in YAHOO_SCREENER_URLS:
                    try:
                        resp = client.get(url)
                        if resp.status_code != 200:
                            continue
                        data = resp.json()
                        if "trending" in url:
                            quotes = (
                                data.get("finance", {})
                                .get("result", [{}])[0]
                                .get("quotes", [])
                            )
                            for q in quotes:
                                sym = str(q.get("symbol", "")).upper().replace(".", "-")
                                if sym and "^" not in sym and "=" not in sym and len(sym) <= 6:
                                    found.setdefault(sym, DiscoveredTicker(symbol=sym, name=sym, source_tag=tag))
                        else:
                            quotes = (
                                data.get("finance", {})
                                .get("result", [{}])[0]
                                .get("quotes", [])
                            )
                            for q in quotes:
                                sym = str(q.get("symbol", "")).upper().replace(".", "-")
                                if sym and "^" not in sym and "=" not in sym and len(sym) <= 6:
                                    name = q.get("shortName") or q.get("longName") or sym
                                    found.setdefault(sym, DiscoveredTicker(symbol=sym, name=name, source_tag=tag))
                    except Exception as exc:
                        logger.debug("Yahoo movers error (%s): %s", tag, exc)
        except Exception as exc:
            logger.warning("Yahoo movers plugin degraded gracefully: %s", exc)

        return list(found.values())
