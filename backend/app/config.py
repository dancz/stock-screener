from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict

APP_DIR = Path(__file__).resolve().parent
BACKEND_DIR = APP_DIR.parent
PROJECT_ROOT = BACKEND_DIR.parent
DEFAULT_DB_PATH = BACKEND_DIR / "data" / "screener.db"
FRONTEND_DIST_DIR = PROJECT_ROOT / "frontend" / "dist"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SCREENER_", extra="ignore")

    db_path: str = str(DEFAULT_DB_PATH)
    ibkr_host: str = "127.0.0.1"
    ibkr_port: int = 4002
    ibkr_client_id: int = 17
    finnhub_api_key: str = ""
    sec_user_agent: str = "StockScreener/1.0 (local@localhost)"
    host: str = "0.0.0.0"
    port: int = 8000


settings = Settings()


DEFAULT_PRESETS: dict[str, dict[str, Any]] = {
    "all_around_swing": {
        "name": "All-Around Swing",
        "description": "Balanced swing setup across Price Action, Channels, RS, Volume, Fundamentals, Events & Insiders.",
        "weights": {
            "price_action_sr": 0.22,
            "trend_channels": 0.22,
            "correlation_rs": 0.16,
            "volume_volatility": 0.16,
            "fundamental_health": 0.12,
            "events_earnings": 0.06,
            "insider_activity": 0.06,
        },
    },
    "channel_bounce": {
        "name": "Channel Bounce",
        "description": "Prioritizes stocks touching trend channel rails near S/R zones with Price Action confirmation.",
        "weights": {
            "trend_channels": 0.35,
            "price_action_sr": 0.28,
            "volume_volatility": 0.14,
            "correlation_rs": 0.10,
            "fundamental_health": 0.08,
            "events_earnings": 0.03,
            "insider_activity": 0.02,
        },
    },
    "breakout_momentum": {
        "name": "Breakout & Momentum",
        "description": "Focuses on TTM Squeeze releases, RVOL spikes, S/R or channel breakouts, and Relative Strength leaders.",
        "weights": {
            "volume_volatility": 0.28,
            "correlation_rs": 0.24,
            "price_action_sr": 0.22,
            "trend_channels": 0.16,
            "events_earnings": 0.05,
            "fundamental_health": 0.03,
            "insider_activity": 0.02,
        },
    },
    "options_income": {
        "name": "Options Income (High IV)",
        "description": "Highlights stable S/R & channel plays with healthy fundamentals and clean earnings runway for spreads.",
        "weights": {
            "price_action_sr": 0.24,
            "trend_channels": 0.24,
            "fundamental_health": 0.22,
            "events_earnings": 0.14,
            "volume_volatility": 0.10,
            "correlation_rs": 0.04,
            "insider_activity": 0.02,
        },
    },
    "insider_quality": {
        "name": "Insider & Quality",
        "description": "Combines C-suite / cluster insider buys with strong financial health and technical support.",
        "weights": {
            "insider_activity": 0.32,
            "fundamental_health": 0.28,
            "price_action_sr": 0.16,
            "trend_channels": 0.12,
            "correlation_rs": 0.06,
            "volume_volatility": 0.04,
            "events_earnings": 0.02,
        },
    },
}


def get_default_runtime_settings() -> dict[str, Any]:
    return copy.deepcopy(
        {
            "presets": DEFAULT_PRESETS,
            "enabled_plugins": {
                "price_action_sr": True,
                "trend_channels": True,
                "correlation_rs": True,
                "volume_volatility": True,
                "fundamental_health": True,
                "events_earnings": True,
                "insider_activity": True,
                "swing_options": True,
                "finviz_movers": True,
                "yahoo_movers": True,
                "openinsider_feed": True,
                "sec_edgar_form4": False,
            },
            "account": {
                "equity": 25000.0,
                "risk_pct": 1.0,
            },
            "ibkr": {
                "enabled": True,
                "host": settings.ibkr_host,
                "port": settings.ibkr_port,
                "client_id": settings.ibkr_client_id,
            },
            "data_sources": {
                "finnhub_api_key": settings.finnhub_api_key,
                "sec_user_agent": settings.sec_user_agent,
            },
            "schedule": {
                "enabled": True,
                "timezone": "Europe/Prague",
                "post_market_time": "22:30",
                "pre_market_time": "14:45",
            },
            "notifiers": {
                "telegram_enabled": False,
                "telegram_bot_token": "",
                "telegram_chat_id": "",
                "discord_enabled": False,
                "discord_webhook_url": "",
                "min_alert_score": 78,
            },
        }
    )
