from __future__ import annotations

import logging
from typing import Any

import httpx

from app.core.contracts import SourcePlugin

logger = logging.getLogger(__name__)

_CIK_CACHE: dict[str, str] = {}


def _load_sec_cik_map(user_agent: str) -> dict[str, str]:
    global _CIK_CACHE
    if _CIK_CACHE:
        return _CIK_CACHE
    try:
        with httpx.Client(timeout=10.0, headers={"User-Agent": user_agent}) as client:
            resp = client.get("https://www.sec.gov/files/company_tickers.json")
            resp.raise_for_status()
            data = resp.json()
            for entry in data.values():
                ticker = str(entry.get("ticker", "")).upper()
                cik = str(entry.get("cik_str", "")).zfill(10)
                if ticker and cik:
                    _CIK_CACHE[ticker] = cik
    except Exception as exc:
        logger.debug("SEC CIK map fetch skipped: %s", exc)
    return _CIK_CACHE


def fetch_sec_form4_filings(symbol: str, user_agent: str) -> list[dict[str, Any]]:
    """Optional per-ticker Form 4 filing metadata from SEC EDGAR."""
    try:
        cik_map = _load_sec_cik_map(user_agent)
        cik = cik_map.get(symbol.upper())
        if not cik:
            return []
        url = f"https://data.sec.gov/submissions/CIK{cik}.json"
        with httpx.Client(timeout=8.0, headers={"User-Agent": user_agent}) as client:
            resp = client.get(url)
            if resp.status_code != 200:
                return []
            recent = resp.json().get("filings", {}).get("recent", {})
            forms = recent.get("form", [])
            dates = recent.get("filingDate", [])
            docs = recent.get("primaryDocDescription", [])
            filings: list[dict[str, Any]] = []
            for form, fdate, desc in zip(forms, dates, docs):
                if form == "4":
                    filings.append(
                        {
                            "source": "SEC EDGAR Form 4",
                            "tag": "sec_form4",
                            "filing_date": fdate,
                            "trade_date": fdate,
                            "insider_name": desc or "Form 4 Insider Filing",
                            "title": "Form 4",
                            "trade_type": "Filing",
                            "value_usd": 0.0,
                            "value_raw": "SEC Filing",
                        }
                    )
                if len(filings) >= 5:
                    break
            return filings
    except Exception as exc:
        logger.debug("SEC Form 4 lookup failed for %s: %s", symbol, exc)
        return []


class SecEdgarForm4Plugin(SourcePlugin):
    plugin_id = "sec_edgar_form4"
    display_name = "SEC EDGAR Form 4 (Per-Ticker)"
    description = "Optional official SEC EDGAR Form 4 insider filing lookup during deep enrichment."
