from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.paper_risk import ensure_active_paper_run
from app.core.registry import registry
from app.core.scheduler import configure_scheduler
from app.core.telegram_bot import detect_telegram_chats
from app.db.database import get_db
from app.db.models import load_runtime_settings, save_runtime_settings
from app.plugins.sources.market_data_ibkr import test_connection

router = APIRouter(prefix="/settings", tags=["settings"])


class IbkrTestBody(BaseModel):
    host: str = "127.0.0.1"
    port: int = 4002
    client_id: int = 17


class NotifierTestBody(BaseModel):
    plugin_id: str


class TelegramDetectBody(BaseModel):
    bot_token: str = ""


@router.get("")
def get_settings(db: Session = Depends(get_db)) -> dict[str, Any]:
    runtime = load_runtime_settings(db)
    return {
        "settings": runtime,
        "plugins": registry.describe_all(runtime.get("enabled_plugins", {})),
    }


@router.put("")
def update_settings(payload: dict[str, Any], db: Session = Depends(get_db)) -> dict[str, Any]:
    updated = save_runtime_settings(db, payload)
    if "paper_trading" in payload:
        ensure_active_paper_run(db, updated)
        db.commit()
    if "schedule" in payload or "history" in payload or "notifiers" in payload:
        configure_scheduler(updated)
    return {
        "settings": updated,
        "plugins": registry.describe_all(updated.get("enabled_plugins", {})),
    }


@router.post("/test-ibkr")
def test_ibkr_gateway(body: IbkrTestBody) -> dict[str, Any]:
    ok, message = test_connection(body.host, body.port, body.client_id)
    return {"connected": ok, "message": message}


@router.post("/test-notifier")
def test_notifier(body: NotifierTestBody, db: Session = Depends(get_db)) -> dict[str, Any]:
    runtime = load_runtime_settings(db)
    registry.discover_all()
    plugin = registry.notifiers.get(body.plugin_id)
    if plugin is None:
        return {"ok": False, "message": f"Unknown notifier plugin: {body.plugin_id}"}
    ok, msg = plugin.send_test(runtime)
    return {"ok": ok, "message": msg}


@router.post("/telegram/detect-chat")
def detect_telegram_chat_id(body: TelegramDetectBody, db: Session = Depends(get_db)) -> dict[str, Any]:
    token = body.bot_token.strip()
    if not token:
        runtime = load_runtime_settings(db)
        token = str(runtime.get("notifiers", {}).get("telegram_bot_token", "")).strip()
    return detect_telegram_chats(token)
