from __future__ import annotations

import threading
from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.history_backfill import get_backfill_status, run_history_backfill_batch
from app.core.pipeline import get_scan_status, run_scan

router = APIRouter(prefix="/scan", tags=["scan"])


class ScanRunRequest(BaseModel):
    mode: Literal["full", "quick", "watchlists"] = "quick"


@router.post("/run")
def trigger_scan(body: ScanRunRequest) -> dict[str, Any]:
    current = get_scan_status()
    if current.get("running"):
        return {"started": False, "status": current}

    t = threading.Thread(target=run_scan, args=(body.mode,), daemon=True)
    t.start()
    return {"started": True, "status": get_scan_status()}


@router.get("/status")
def scan_status() -> dict[str, Any]:
    return get_scan_status()


@router.get("/backfill/status")
def backfill_status() -> dict[str, Any]:
    return get_backfill_status()


@router.post("/backfill")
def trigger_backfill() -> dict[str, Any]:
    current = get_backfill_status()
    if current.get("running"):
        return {"started": False, "status": current}

    t = threading.Thread(target=run_history_backfill_batch, kwargs={"force": True}, daemon=True)
    t.start()
    return {"started": True, "status": get_backfill_status()}
