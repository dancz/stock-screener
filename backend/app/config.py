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
        "description": "Balanced swing setup across Price Action, Smart Money Zones, Channels, R:R, RS, Volume, Fundamentals, Events & Insiders.",
        "weights": {
            "price_action_sr": 0.16,
            "smart_money_zones": 0.16,
            "rr_index_trend": 0.14,
            "trend_channels": 0.16,
            "correlation_rs": 0.12,
            "volume_volatility": 0.10,
            "fundamental_health": 0.08,
            "events_earnings": 0.04,
            "insider_activity": 0.04,
        },
    },
    "short_term_high_rr": {
        "name": "Short-Term High R:R (Long/Short)",
        "description": "Targets asymmetric Reward/Risk setups (Long or Short) near S/R & SMC zones aligned with the benchmark index trend.",
        "weights": {
            "rr_index_trend": 0.30,
            "smart_money_zones": 0.24,
            "price_action_sr": 0.16,
            "trend_channels": 0.14,
            "correlation_rs": 0.10,
            "volume_volatility": 0.06,
        },
    },
    "medium_term_invest": {
        "name": "Medium-Term Investing",
        "description": "Focuses on financial health, relative strength leadership, multi-month channels, SMC discount zones, and insider conviction.",
        "weights": {
            "fundamental_health": 0.34,
            "correlation_rs": 0.18,
            "trend_channels": 0.16,
            "smart_money_zones": 0.14,
            "insider_activity": 0.10,
            "events_earnings": 0.08,
        },
    },
    "channel_bounce": {
        "name": "Channel Bounce",
        "description": "Prioritizes stocks touching trend channel rails near S/R & SMC zones with Price Action confirmation.",
        "weights": {
            "trend_channels": 0.32,
            "price_action_sr": 0.24,
            "smart_money_zones": 0.16,
            "rr_index_trend": 0.10,
            "volume_volatility": 0.08,
            "correlation_rs": 0.06,
            "fundamental_health": 0.04,
        },
    },
    "breakout_momentum": {
        "name": "Breakout & Momentum",
        "description": "Focuses on TTM Squeeze releases, RVOL spikes, S/R or BOS breakouts, and Relative Strength leaders.",
        "weights": {
            "volume_volatility": 0.24,
            "correlation_rs": 0.22,
            "price_action_sr": 0.18,
            "smart_money_zones": 0.16,
            "trend_channels": 0.12,
            "rr_index_trend": 0.05,
            "events_earnings": 0.03,
        },
    },
    "options_income": {
        "name": "Options Income (High IV)",
        "description": "Highlights stable S/R & channel plays with healthy fundamentals and clean earnings runway for spreads.",
        "weights": {
            "price_action_sr": 0.20,
            "trend_channels": 0.20,
            "smart_money_zones": 0.16,
            "fundamental_health": 0.20,
            "events_earnings": 0.14,
            "volume_volatility": 0.06,
            "correlation_rs": 0.04,
        },
    },
    "insider_quality": {
        "name": "Insider & Quality",
        "description": "Combines C-suite / cluster insider buys with strong financial health and technical support.",
        "weights": {
            "insider_activity": 0.30,
            "fundamental_health": 0.26,
            "price_action_sr": 0.14,
            "smart_money_zones": 0.14,
            "trend_channels": 0.10,
            "correlation_rs": 0.06,
        },
    },
}


def get_default_runtime_settings() -> dict[str, Any]:
    return copy.deepcopy(
        {
            "presets": DEFAULT_PRESETS,
            "enabled_plugins": {
                "price_action_sr": True,
                "smart_money_zones": True,
                "rr_index_trend": True,
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
            "history": {
                "profile": "dev",  # "dev" | "production"
                "dev_daily_period": "2y",
                "dev_intraday_period": "120d",
                "dev_weekly_period": "5y",
                "prod_daily_period": "5y",
                "prod_intraday_period": "365d",
                "prod_weekly_period": "10y",
                "backfill_enabled": False,
                "backfill_interval_minutes": 15,
                "backfill_batch_size": 5,
                "prefer_ibkr_history": True,
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
                "intraday_enabled": True,
                "intraday_times": "16:15,19:15",
                "radar_enabled": True,
                "radar_ttl_days": 14,
                "radar_probation_days": 7,
                "radar_max_tickers": 120,
                "radar_min_score": 66.0,
            },
            "notifiers": {
                "telegram_enabled": False,
                "telegram_bot_token": "",
                "telegram_chat_id": "",
                "telegram_notify_scan": True,
                "telegram_notify_paper_orders": True,
                "telegram_notify_paper_lifecycle": True,
                "telegram_notify_kill_switch": True,
                "telegram_commands_enabled": True,
                "discord_enabled": False,
                "discord_webhook_url": "",
                "min_alert_score": 78,
            },
            "paper_trading": {
                "enabled": False,
                "mode": "virtual",
                "auto_execute_after_scan": True,
                "min_conviction": 68.0,
                "min_rr": 1.8,
                "min_tp1_rr": 1.0,
                "allow_short": False,
                "risk_per_trade_pct": 1.0,
                "max_position_pct_equity": 15.0,
                "max_open_positions": 6,
                "max_positions_per_sector": 2,
                "max_daily_new_orders": 5,
                "min_dollar_volume": 20000000.0,
                "max_spread_pct": 0.4,
                "min_days_to_earnings": 7,
                "move_to_be_at_r": 1.0,
                "trailing_stop_enabled": True,
                "trailing_activation_r": 1.5,
                "trailing_atr_mult": 2.0,
                "time_stop_days": 15,
                "daily_loss_limit_pct": -2.0,
                "max_drawdown_pct": -6.0,
            },
        }
    )


def get_active_history_periods(runtime_settings: dict[str, Any]) -> tuple[str, str, str]:
    """Return (daily_period, intraday_period, weekly_period) for active history profile."""
    hist = runtime_settings.get("history", {})
    profile = str(hist.get("profile", "dev")).lower()
    if profile == "production":
        return (
            str(hist.get("prod_daily_period", "5y")),
            str(hist.get("prod_intraday_period", "365d")),
            str(hist.get("prod_weekly_period", "10y")),
        )
    return (
        str(hist.get("dev_daily_period", "2y")),
        str(hist.get("dev_intraday_period", "120d")),
        str(hist.get("dev_weekly_period", "5y")),
    )
