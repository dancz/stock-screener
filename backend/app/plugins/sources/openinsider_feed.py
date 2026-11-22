from __future__ import annotations

import logging
from typing import Any

import httpx
from bs4 import BeautifulSoup

from app.core.contracts import DiscoveredTicker, SourcePlugin

logger = logging.getLogger(__name__)

OPENINSIDER_URLS: list[tuple[str, str]] = [
    ("http://openinsider.com/latest-cluster-buys", "cluster_buy"),
    ("http://openinsider.com/latest-ceo-cfo-purchases-25k", "ceo_cfo_buy"),
]


def fetch_openinsider_map() -> dict[str, list[dict[str, Any]]]:
    """Fetch recent cluster buys and CEO/CFO buys from OpenInsider, mapped by ticker."""
    trades_by_symbol: dict[str, list[dict[str, Any]]] = {}
    headers = {"User-Agent": "Mozilla/5.0 (compatible; StockScreener/1.0)"}

    try:
        with httpx.Client(timeout=10.0, headers=headers, follow_redirects=True) as client:
            for url, tag in OPENINSIDER_URLS:
                try:
                    resp = client.get(url)
                    if resp.status_code != 200:
                        continue
                    soup = BeautifulSoup(resp.text, "lxml")
                    table = soup.find("table", {"class": "tinytable"})
                    if table is None:
                        continue
                    tbody = table.find("tbody") or table
                    for tr in tbody.find_all("tr")[:80]:
                        tds = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
                        if len(tds) < 12:
                            continue
                        filing_date = tds[1]
                        trade_date = tds[2]
                        sym = tds[3].strip().upper().replace(".", "-")
                        if not sym or len(sym) > 6:
                            continue
                        insider_name = tds[5] if tag != "cluster_buy" else tds[5]
                        title = tds[6]
                        trade_type = tds[7]
                        price = tds[8]
                        qty = tds[9]
                        value_raw = tds[12] if len(tds) > 12 else tds[-1]

                        value_usd = _parse_dollar(value_raw)
                        record = {
                            "source": "OpenInsider",
                            "tag": tag,
                            "filing_date": filing_date,
                            "trade_date": trade_date,
                            "insider_name": insider_name,
                            "title": title,
                            "trade_type": trade_type,
                            "price": price,
                            "qty": qty,
                            "value_raw": value_raw,
                            "value_usd": value_usd,
                        }
                        trades_by_symbol.setdefault(sym, []).append(record)
                except Exception as exc:
                    logger.debug("OpenInsider error for %s: %s", url, exc)
    except Exception as exc:
        logger.warning("OpenInsider feed degraded gracefully: %s", exc)

    return trades_by_symbol


def _parse_dollar(raw: str) -> float:
    cleaned = raw.replace("$", "").replace(",", "").replace("+", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


class OpenInsiderPlugin(SourcePlugin):
    plugin_id = "openinsider_feed"
    display_name = "OpenInsider Cluster & C-Suite Buys"
    description = "Fetches recent US insider cluster buys and CEO/CFO purchases from OpenInsider (no API key)."

    def discover(self, settings: dict[str, Any]) -> list[DiscoveredTicker]:
        if not settings.get("enabled_plugins", {}).get(self.plugin_id, True):
            return []
        trades_map = fetch_openinsider_map()
        return [
            DiscoveredTicker(symbol=sym, name=sym, source_tag="openinsider")
            for sym in list(trades_map.keys())[:60]
        ]
