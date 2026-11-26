from __future__ import annotations

import csv
import io
import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx
from bs4 import BeautifulSoup

from app.config import BACKEND_DIR, settings
from app.core.contracts import DiscoveredTicker, SourcePlugin

logger = logging.getLogger(__name__)

CACHE_PATH = BACKEND_DIR / "data" / "universe_cache.json"
CACHE_TTL_SECONDS = 7 * 24 * 3600
GITHUB_SP500_CSV_URL = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"
WIKIPEDIA_HEADERS = {
    "User-Agent": "SwingPulseResearch/1.0 (https://akcie.dantm.info; contact@dantm.info) python-httpx/0.27",
    "Accept": "text/html,application/xhtml+xml,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
}


def _get_cache_path() -> Path:
    try:
        return Path(settings.db_path).resolve().parent / "cache" / "universe_cache.json"
    except Exception:
        return CACHE_PATH

SECTOR_ETF_MAP: dict[str, str] = {
    "Information Technology": "XLK",
    "Technology": "XLK",
    "Financials": "XLF",
    "Financial Services": "XLF",
    "Energy": "XLE",
    "Health Care": "XLV",
    "Healthcare": "XLV",
    "Industrials": "XLI",
    "Consumer Discretionary": "XLY",
    "Consumer Cyclical": "XLY",
    "Consumer Staples": "XLP",
    "Consumer Defensive": "XLP",
    "Utilities": "XLU",
    "Materials": "XLB",
    "Basic Materials": "XLB",
    "Real Estate": "XLRE",
    "Communication Services": "XLC",
}

# Fallback high-liquidity S&P 500 + Nasdaq 100 core list (used when Wikipedia is offline)
FALLBACK_UNIVERSE: list[tuple[str, str, str, str]] = [
    ("AAPL", "Apple Inc.", "Information Technology", "QQQ"),
    ("MSFT", "Microsoft Corp.", "Information Technology", "QQQ"),
    ("NVDA", "NVIDIA Corp.", "Information Technology", "QQQ"),
    ("AMZN", "Amazon.com Inc.", "Consumer Discretionary", "QQQ"),
    ("META", "Meta Platforms Inc.", "Communication Services", "QQQ"),
    ("GOOGL", "Alphabet Inc.", "Communication Services", "QQQ"),
    ("AVGO", "Broadcom Inc.", "Information Technology", "QQQ"),
    ("TSLA", "Tesla Inc.", "Consumer Discretionary", "QQQ"),
    ("AMD", "Advanced Micro Devices", "Information Technology", "QQQ"),
    ("NFLX", "Netflix Inc.", "Communication Services", "QQQ"),
    ("COST", "Costco Wholesale", "Consumer Staples", "QQQ"),
    ("ADBE", "Adobe Inc.", "Information Technology", "QQQ"),
    ("CRM", "Salesforce Inc.", "Information Technology", "SPY"),
    ("ORCL", "Oracle Corp.", "Information Technology", "SPY"),
    ("CSCO", "Cisco Systems", "Information Technology", "QQQ"),
    ("INTC", "Intel Corp.", "Information Technology", "QQQ"),
    ("QCOM", "Qualcomm Inc.", "Information Technology", "QQQ"),
    ("TXN", "Texas Instruments", "Information Technology", "QQQ"),
    ("AMAT", "Applied Materials", "Information Technology", "QQQ"),
    ("LRCX", "Lam Research", "Information Technology", "QQQ"),
    ("KLAC", "KLA Corp.", "Information Technology", "QQQ"),
    ("MU", "Micron Technology", "Information Technology", "QQQ"),
    ("MRVL", "Marvell Technology", "Information Technology", "QQQ"),
    ("PANW", "Palo Alto Networks", "Information Technology", "QQQ"),
    ("CRWD", "CrowdStrike Holdings", "Information Technology", "QQQ"),
    ("SNPS", "Synopsys Inc.", "Information Technology", "QQQ"),
    ("CDNS", "Cadence Design Systems", "Information Technology", "QQQ"),
    ("INTU", "Intuit Inc.", "Information Technology", "QQQ"),
    ("NOW", "ServiceNow Inc.", "Information Technology", "SPY"),
    ("PLTR", "Palantir Technologies", "Information Technology", "QQQ"),
    ("ISRG", "Intuitive Surgical", "Health Care", "QQQ"),
    ("VRTX", "Vertex Pharmaceuticals", "Health Care", "QQQ"),
    ("REGN", "Regeneron Pharmaceuticals", "Health Care", "QQQ"),
    ("GILD", "Gilead Sciences", "Health Care", "QQQ"),
    ("AMGN", "Amgen Inc.", "Health Care", "QQQ"),
    ("LLY", "Eli Lilly and Co.", "Health Care", "SPY"),
    ("UNH", "UnitedHealth Group", "Health Care", "SPY"),
    ("JNJ", "Johnson & Johnson", "Health Care", "SPY"),
    ("ABBV", "AbbVie Inc.", "Health Care", "SPY"),
    ("MRK", "Merck & Co.", "Health Care", "SPY"),
    ("TMO", "Thermo Fisher Scientific", "Health Care", "SPY"),
    ("ABT", "Abbott Laboratories", "Health Care", "SPY"),
    ("PFE", "Pfizer Inc.", "Health Care", "SPY"),
    ("JPM", "JPMorgan Chase & Co.", "Financials", "SPY"),
    ("BAC", "Bank of America", "Financials", "SPY"),
    ("WFC", "Wells Fargo & Co.", "Financials", "SPY"),
    ("GS", "Goldman Sachs Group", "Financials", "SPY"),
    ("MS", "Morgan Stanley", "Financials", "SPY"),
    ("BLK", "BlackRock Inc.", "Financials", "SPY"),
    ("SCHW", "Charles Schwab", "Financials", "SPY"),
    ("V", "Visa Inc.", "Financials", "SPY"),
    ("MA", "Mastercard Inc.", "Financials", "SPY"),
    ("PYPL", "PayPal Holdings", "Financials", "QQQ"),
    ("AXP", "American Express", "Financials", "SPY"),
    ("XOM", "Exxon Mobil Corp.", "Energy", "SPY"),
    ("CVX", "Chevron Corp.", "Energy", "SPY"),
    ("COP", "ConocoPhillips", "Energy", "SPY"),
    ("SLB", "SLB (Schlumberger)", "Energy", "SPY"),
    ("EOG", "EOG Resources", "Energy", "SPY"),
    ("OXY", "Occidental Petroleum", "Energy", "SPY"),
    ("CAT", "Caterpillar Inc.", "Industrials", "SPY"),
    ("DE", "Deere & Co.", "Industrials", "SPY"),
    ("BA", "Boeing Co.", "Industrials", "SPY"),
    ("HON", "Honeywell International", "Industrials", "QQQ"),
    ("GE", "GE Aerospace", "Industrials", "SPY"),
    ("RTX", "RTX Corp.", "Industrials", "SPY"),
    ("LMT", "Lockheed Martin", "Industrials", "SPY"),
    ("UNP", "Union Pacific", "Industrials", "SPY"),
    ("UPS", "United Parcel Service", "Industrials", "SPY"),
    ("HD", "Home Depot Inc.", "Consumer Discretionary", "SPY"),
    ("LOW", "Lowe's Companies", "Consumer Discretionary", "SPY"),
    ("MCD", "McDonald's Corp.", "Consumer Discretionary", "SPY"),
    ("SBUX", "Starbucks Corp.", "Consumer Discretionary", "QQQ"),
    ("NKE", "Nike Inc.", "Consumer Discretionary", "SPY"),
    ("BKNG", "Booking Holdings", "Consumer Discretionary", "QQQ"),
    ("ABNB", "Airbnb Inc.", "Consumer Discretionary", "QQQ"),
    ("MAR", "Marriott International", "Consumer Discretionary", "QQQ"),
    ("WMT", "Walmart Inc.", "Consumer Staples", "SPY"),
    ("PG", "Procter & Gamble", "Consumer Staples", "SPY"),
    ("KO", "Coca-Cola Co.", "Consumer Staples", "SPY"),
    ("PEP", "PepsiCo Inc.", "Consumer Staples", "QQQ"),
    ("MDLZ", "Mondelez International", "Consumer Staples", "QQQ"),
    ("PM", "Philip Morris International", "Consumer Staples", "SPY"),
    ("DIS", "Walt Disney Co.", "Communication Services", "SPY"),
    ("TMUS", "T-Mobile US", "Communication Services", "QQQ"),
    ("CMCSA", "Comcast Corp.", "Communication Services", "QQQ"),
    ("VZ", "Verizon Communications", "Communication Services", "SPY"),
    ("T", "AT&T Inc.", "Communication Services", "SPY"),
    ("NEE", "NextEra Energy", "Utilities", "SPY"),
    ("SO", "Southern Co.", "Utilities", "SPY"),
    ("DUK", "Duke Energy", "Utilities", "SPY"),
    ("CEG", "Constellation Energy", "Utilities", "QQQ"),
    ("LIN", "Linde plc", "Materials", "QQQ"),
    ("APD", "Air Products", "Materials", "SPY"),
    ("FCX", "Freeport-McMoRan", "Materials", "SPY"),
    ("NEM", "Newmont Corp.", "Materials", "SPY"),
    ("PLD", "Prologis Inc.", "Real Estate", "SPY"),
    ("AMT", "American Tower", "Real Estate", "SPY"),
    ("EQIX", "Equinix Inc.", "Real Estate", "SPY"),
    ("MELI", "MercadoLibre", "Consumer Discretionary", "QQQ"),
    ("APP", "AppLovin Corp.", "Information Technology", "QQQ"),
    ("ARM", "Arm Holdings", "Information Technology", "QQQ"),
    ("SMCI", "Super Micro Computer", "Information Technology", "SPY"),
    ("COIN", "Coinbase Global", "Financials", "QQQ"),
    ("SOFI", "SoFi Technologies", "Financials", "IWM"),
    ("HOOD", "Robinhood Markets", "Financials", "QQQ"),
    ("RIVN", "Rivian Automotive", "Consumer Discretionary", "IWM"),
]

QUICK_LEADERS: list[str] = [item[0] for item in FALLBACK_UNIVERSE[:35]]


def map_sector_to_etf(sector: str) -> str:
    if not sector:
        return "SPY"
    return SECTOR_ETF_MAP.get(sector.strip(), "SPY")


def get_primary_benchmark(symbol: str, is_ndx: bool = False, sector: str = "") -> str:
    if is_ndx or sector in {"Information Technology", "Technology", "Communication Services"}:
        return "QQQ"
    return "SPY"


def _normalize_yf_symbol(raw: str) -> str:
    return raw.strip().upper().replace(".", "-")


def _http_get_text(url: str, timeout: float = 12.0) -> str:
    try:
        transport = httpx.HTTPTransport(local_address="0.0.0.0")
        with httpx.Client(timeout=timeout, headers=WIKIPEDIA_HEADERS, follow_redirects=True, transport=transport) as client:
            resp = client.get(url)
            resp.raise_for_status()
            return resp.text
    except (TypeError, OSError):
        with httpx.Client(timeout=timeout, headers=WIKIPEDIA_HEADERS, follow_redirects=True) as client:
            resp = client.get(url)
            resp.raise_for_status()
            return resp.text


def _fetch_wikipedia_table(url: str, table_id: str | None = None) -> list[dict[str, str]]:
    html = _http_get_text(url, timeout=12.0)
    soup = BeautifulSoup(html, "lxml")

    table = soup.find("table", {"id": table_id}) if table_id else None
    if table is None:
        tables = soup.find_all("table", {"class": "wikitable"})
        # Pick table with Ticker/Symbol column
        for tbl in tables:
            header_text = " ".join(th.get_text(strip=True).lower() for th in tbl.find_all("th"))
            if "symbol" in header_text or "ticker" in header_text:
                table = tbl
                break
    if table is None:
        return []

    headers_row = [th.get_text(" ", strip=True) for th in table.find_all("tr")[0].find_all(["th", "td"])]
    rows: list[dict[str, str]] = []
    for tr in table.find_all("tr")[1:]:
        cells = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
        if len(cells) < 2:
            continue
        row = {headers_row[i]: cells[i] for i in range(min(len(headers_row), len(cells)))}
        rows.append(row)
    return rows


def _fetch_github_sp500_csv() -> list[dict[str, str]]:
    text_csv = _http_get_text(GITHUB_SP500_CSV_URL, timeout=12.0)
    reader = csv.DictReader(io.StringIO(text_csv))
    rows: list[dict[str, str]] = []
    for row in reader:
        sym = row.get("Symbol") or ""
        if sym:
            rows.append(
                {
                    "Symbol": sym,
                    "Security": row.get("Security") or row.get("Name") or sym,
                    "GICS Sector": row.get("GICS Sector") or row.get("Sector") or "",
                    "GICS Sub-Industry": row.get("GICS Sub-Industry") or row.get("Sub-Industry") or "",
                }
            )
    return rows


class Sp500Nasdaq100UniversePlugin(SourcePlugin):
    plugin_id = "universe_sp500_ndx"
    display_name = "S&P 500 + Nasdaq 100 Universe"
    description = "Maintains the core liquid US equities universe (S&P 500 & Nasdaq 100) with 7-day local caching and offline fallback."

    def discover(self, settings: dict[str, Any]) -> list[DiscoveredTicker]:
        cached = self._load_cache()
        if cached:
            return cached

        discovered: dict[str, DiscoveredTicker] = {}
        sp_rows: list[dict[str, str]] = []
        try:
            sp_rows = _fetch_wikipedia_table("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", "constituents")
        except Exception as exc:
            logger.warning("Wikipedia S&P 500 fetch failed (%s), trying GitHub CSV backup.", exc)

        if not sp_rows:
            try:
                sp_rows = _fetch_github_sp500_csv()
            except Exception as exc:
                logger.warning("GitHub S&P 500 CSV backup failed (%s), will use fallback if needed.", exc)

        for row in sp_rows:
            sym = _normalize_yf_symbol(row.get("Symbol", ""))
            if not sym:
                continue
            discovered[sym] = DiscoveredTicker(
                symbol=sym,
                name=row.get("Security", sym),
                sector=row.get("GICS Sector", ""),
                industry=row.get("GICS Sub-Industry", ""),
                source_tag="sp500",
            )

        try:
            ndx_rows = _fetch_wikipedia_table("https://en.wikipedia.org/wiki/Nasdaq-100", "constituents")
            for row in ndx_rows:
                sym = _normalize_yf_symbol(row.get("Ticker") or row.get("Symbol") or "")
                if not sym:
                    continue
                if sym in discovered:
                    discovered[sym].source_tag = "sp500+ndx100"
                else:
                    discovered[sym] = DiscoveredTicker(
                        symbol=sym,
                        name=row.get("Company") or row.get("Security") or sym,
                        sector=row.get("ICB Industry") or row.get("GICS Sector") or "",
                        industry=row.get("ICB Subsector") or row.get("GICS Sub-Industry") or "",
                        source_tag="ndx100",
                    )
        except Exception as exc:
            logger.warning("Wikipedia Nasdaq 100 fetch failed (%s).", exc)

        if not discovered:
            for sym, name, sector, _ in FALLBACK_UNIVERSE:
                discovered[sym] = DiscoveredTicker(
                    symbol=sym,
                    name=name,
                    sector=sector,
                    industry="",
                    source_tag="fallback_universe",
                )
        else:
            # Ensure core fallback leaders are also included
            for sym, name, sector, _ in FALLBACK_UNIVERSE:
                if sym not in discovered:
                    discovered[sym] = DiscoveredTicker(
                        symbol=sym,
                        name=name,
                        sector=sector,
                        industry="",
                        source_tag="core_leader",
                    )
            self._save_cache(list(discovered.values()))

        return list(discovered.values())

    def _load_cache(self) -> list[DiscoveredTicker] | None:
        cache_path = _get_cache_path()
        if not cache_path.exists():
            return None
        try:
            age = time.time() - cache_path.stat().st_mtime
            if age > CACHE_TTL_SECONDS:
                return None
            raw = json.loads(cache_path.read_text(encoding="utf-8"))
            items = [DiscoveredTicker(**item) for item in raw]
            if len(items) < 200:
                return None
            return items
        except Exception:
            return None

    def _save_cache(self, items: list[DiscoveredTicker]) -> None:
        cache_path = _get_cache_path()
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps([item.model_dump() for item in items], indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning("Failed to write universe cache: %s", exc)
