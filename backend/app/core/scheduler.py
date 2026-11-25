from __future__ import annotations

import logging
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.core.history_backfill import run_history_backfill_batch
from app.core.pipeline import run_scan
from app.db.database import session_scope
from app.db.models import load_runtime_settings

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def _parse_hhmm(raw: str, default_h: int, default_m: int) -> tuple[int, int]:
    try:
        parts = raw.strip().split(":")
        return int(parts[0]), int(parts[1])
    except Exception:
        return default_h, default_m


def _scheduled_full_scan() -> None:
    logger.info("Running scheduled full daily stock scan...")
    run_scan(mode="full")


def _scheduled_intraday_scan() -> None:
    logger.info("Running scheduled intraday quick stock scan...")
    run_scan(mode="quick")


def _scheduled_telegram_poll() -> None:
    try:
        from app.core.telegram_bot import poll_telegram_commands_once

        poll_telegram_commands_once()
    except Exception as exc:
        logger.debug("Scheduled Telegram poll error: %s", exc)


def _scheduled_paper_tick() -> None:
    try:
        from app.core.paper_ibkr import step_ibkr_paper_lifecycle
        from app.core.paper_virtual import step_virtual_lifecycle
        from app.db.models import PaperTrade

        with session_scope() as session:
            runtime = load_runtime_settings(session)
            paper_cfg = runtime.get("paper_trading", {})
            has_active = (
                session.query(PaperTrade)
                .filter(PaperTrade.status.in_(("PENDING_ENTRY", "OPEN", "PARTIAL_TP1")))
                .count()
                > 0
            )
            mode = str(paper_cfg.get("mode", "virtual") or "virtual")
            if paper_cfg.get("enabled", False) or has_active:
                if mode == "ibkr_paper":
                    step_ibkr_paper_lifecycle(session, runtime_settings=runtime)
                else:
                    step_virtual_lifecycle(session, runtime_settings=runtime)
    except Exception as exc:
        logger.warning("Scheduled paper lifecycle tick error: %s", exc)


def configure_scheduler(runtime_settings: dict[str, Any] | None = None) -> BackgroundScheduler:
    global _scheduler
    if runtime_settings is None:
        with session_scope() as session:
            runtime_settings = load_runtime_settings(session)

    sched_cfg = runtime_settings.get("schedule", {})
    notif_cfg = runtime_settings.get("notifiers", {})
    tz = str(sched_cfg.get("timezone", "Europe/Prague"))

    if _scheduler is None:
        _scheduler = BackgroundScheduler(timezone=tz)
        _scheduler.start()
    else:
        _scheduler.remove_all_jobs()

    if notif_cfg.get("telegram_enabled", False) and notif_cfg.get("telegram_commands_enabled", True):
        _scheduler.add_job(
            _scheduled_telegram_poll,
            IntervalTrigger(seconds=20),
            id="telegram_bot_job",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )

    if not sched_cfg.get("enabled", True):
        logger.info("Background scan scheduler is disabled.")
        return _scheduler

    post_h, post_m = _parse_hhmm(str(sched_cfg.get("post_market_time", "22:30")), 22, 30)
    pre_h, pre_m = _parse_hhmm(str(sched_cfg.get("pre_market_time", "14:45")), 14, 45)

    _scheduler.add_job(
        _scheduled_full_scan,
        CronTrigger(day_of_week="mon-fri", hour=post_h, minute=post_m, timezone=tz),
        id="post_market_scan",
        replace_existing=True,
    )
    _scheduler.add_job(
        _scheduled_full_scan,
        CronTrigger(day_of_week="mon-fri", hour=pre_h, minute=pre_m, timezone=tz),
        id="pre_market_scan",
        replace_existing=True,
    )
    if sched_cfg.get("intraday_enabled", True):
        raw_times = [t.strip() for t in str(sched_cfg.get("intraday_times", "16:15,19:15")).split(",") if t.strip()]
        for idx, hhmm in enumerate(raw_times[:4]):
            ih, im = _parse_hhmm(hhmm, 16, 15)
            _scheduler.add_job(
                _scheduled_intraday_scan,
                CronTrigger(day_of_week="mon-fri", hour=ih, minute=im, timezone=tz),
                id=f"intraday_scan_{idx}",
                replace_existing=True,
            )

    _scheduler.add_job(
        _scheduled_paper_tick,
        CronTrigger(day_of_week="mon-fri", hour="14-22", minute="*/5", timezone=tz),
        id="paper_lifecycle_job",
        replace_existing=True,
    )

    hist_cfg = runtime_settings.get("history", {})
    if hist_cfg.get("profile") == "production" or hist_cfg.get("backfill_enabled", False):
        interval_min = max(5, int(hist_cfg.get("backfill_interval_minutes", 15)))
        _scheduler.add_job(
            run_history_backfill_batch,
            IntervalTrigger(minutes=interval_min),
            id="history_backfill_job",
            replace_existing=True,
        )
        logger.info("Enabled background history backfill every %d min (profile=%s).", interval_min, hist_cfg.get("profile"))

    logger.info("Scheduled daily Mon-Fri scans at %02d:%02d and %02d:%02d (%s).", pre_h, pre_m, post_h, post_m, tz)
    return _scheduler


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
