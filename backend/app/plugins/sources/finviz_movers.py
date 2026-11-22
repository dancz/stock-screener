from __future__ import annotations

import logging
import time
from typing import Any

import httpx
from bs4 import BeautifulSoup

from app.core.contracts import DiscoveredTicker, SourcePlugin

logger = logging.getLogger(__name__)

FINVIZ_SIGNALS: list[tuple[str, str]] = [
    ("ta_unusualvolume", "finviz_unusual_vol"),
    ("ta_channelup", "finviz_channel_up"),
    ("ta_new52whigh", "finviz_52w_high"),
    ("ta_oversold", "finviz_oversold"),
]


class FinvizMoversPlugin(SourcePlugin):
    plugin_id = "finviz_movers"
    display_name = "Finviz Swing Movers"
    description = "Discovers daily US swing candidates from Finviz (unusual volume, channel up, 52w highs, oversold)."

    def discover(self, settings: dict[str, Any]) -> list[DiscoveredTicker]:
        if not settings.get("enabled_plugins", {}).get(self.plugin_id, True):
            return []

        results: dict[str, DiscoveredTicker] = {}
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        }

        try:
            with httpx.Client(timeout=10.0, headers=headers, follow_redirects=True) as client:
                for idx, (sig, tag) in enumerate(FINVIZ_SIGNALS):
                    if idx > 0:
                        time.sleep(0.8)
                    url = f"https://finviz.com/screener.ashx?v=111&s={sig}&f=sh_avgvol_o500,sh_price_o5,geo_usa"
                    try:
                        resp = client.get(url)
                        if resp.status_code != 200:
                            logger.debug("Finviz %s returned %s", sig, resp.status_code)
                            continue
                        soup = BeautifulSoup(resp.text, "lxml")
                        for link in soup.select("a.tab-link"):
                            href = link.get("href", "")
                            if "quote.ashx?t=" in href:
                                sym = link.get_text(strip=True).upper().replace(".", "-")
                                if sym and sym.replace("-", "").isalpha() and len(sym) <= 6:
                                    if sym not in results:
                                        results[sym] = DiscoveredTicker(symbol=sym, name=sym, source_tag=tag)
                    except Exception as exc:
                        logger.debug("Finviz fetch error for %s: %s", sig, exc)
        except Exception as exc:
            logger.warning("Finviz movers plugin degraded gracefully: %s", exc)

        return list(results.values())[:100]
