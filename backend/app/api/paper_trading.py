from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.paper_analytics import (
    build_equity_curve,
    build_paper_overview,
    build_signal_attribution,
    export_trades_csv,
    serialize_paper_event,
    serialize_paper_trade,
)
from app.core.paper_ibkr import probe_ibkr_paper_account, step_ibkr_paper_lifecycle
from app.core.paper_risk import ensure_active_paper_run
from app.core.paper_virtual import (
    close_paper_trade_manually,
    reset_paper_run,
    run_paper_allocation_cycle,
    set_paper_kill_switch,
    step_virtual_lifecycle,
)
from app.db.database import get_db
from app.db.models import PaperAccountRun, PaperOrderEvent, PaperTrade, load_runtime_settings

router = APIRouter(prefix="/paper", tags=["paper_trading"])


class ResetRunBody(BaseModel):
    name: str | None = None
    initial_capital: float | None = None


class KillSwitchBody(BaseModel):
    halt: bool = True
    reason: str = ""
    close_all: bool = False


@router.get("/overview")
def get_paper_overview(
    run_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return build_paper_overview(db, run_id=run_id)


@router.get("/equity-curve")
def get_paper_equity_curve(
    run_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    run = db.get(PaperAccountRun, run_id) if run_id is not None else ensure_active_paper_run(db)
    resolved_id = run.id if run is not None else None
    return {
        "run_id": resolved_id,
        "points": build_equity_curve(db, run_id=resolved_id),
    }


@router.get("/trades")
def list_paper_trades(
    run_id: int | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    target_id = run_id if run_id is not None else ensure_active_paper_run(db).id
    q = db.query(PaperTrade).filter(PaperTrade.run_id == target_id)
    if status:
        wanted = [s.strip().upper() for s in status.split(",") if s.strip()]
        if wanted:
            q = q.filter(PaperTrade.status.in_(wanted))
    rows = q.order_by(PaperTrade.id.desc()).limit(limit).all()
    return {
        "run_id": target_id,
        "trades": [serialize_paper_trade(t) for t in rows],
    }


@router.get("/events")
def list_paper_events(
    run_id: int | None = Query(default=None),
    limit: int = Query(default=80, ge=1, le=500),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    target_id = run_id if run_id is not None else ensure_active_paper_run(db).id
    rows = (
        db.query(PaperOrderEvent)
        .filter(PaperOrderEvent.run_id == target_id)
        .order_by(PaperOrderEvent.id.desc())
        .limit(limit)
        .all()
    )
    return {
        "run_id": target_id,
        "events": [serialize_paper_event(ev) for ev in rows],
    }


@router.get("/attribution")
def get_paper_attribution(
    run_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return build_signal_attribution(db, run_id=run_id)


@router.get("/runs")
def list_paper_runs(db: Session = Depends(get_db)) -> dict[str, Any]:
    runs = db.query(PaperAccountRun).order_by(PaperAccountRun.id.desc()).all()
    return {
        "runs": [
            {
                "id": r.id,
                "name": r.name,
                "mode": r.mode,
                "initial_capital": round(float(r.initial_capital), 2),
                "current_equity": round(float(r.current_equity), 2),
                "is_active": bool(r.is_active),
                "kill_switch_triggered": bool(r.kill_switch_triggered),
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "ended_at": r.ended_at.isoformat() if r.ended_at else None,
            }
            for r in runs
        ]
    }


@router.post("/runs/reset")
def reset_experiment_run(body: ResetRunBody, db: Session = Depends(get_db)) -> dict[str, Any]:
    new_run = reset_paper_run(db, name=body.name, initial_capital=body.initial_capital)
    return build_paper_overview(db, run_id=new_run.id)


@router.post("/evaluate-now")
def evaluate_now() -> dict[str, Any]:
    return run_paper_allocation_cycle(force=True)


@router.post("/step")
def step_lifecycle_now(db: Session = Depends(get_db)) -> dict[str, Any]:
    runtime = load_runtime_settings(db)
    run = ensure_active_paper_run(db, runtime)
    if run.mode == "ibkr_paper":
        res = step_ibkr_paper_lifecycle(db, run=run, runtime_settings=runtime)
    else:
        res = step_virtual_lifecycle(db, run=run, runtime_settings=runtime)
    return {"step": res, "overview": build_paper_overview(db, run_id=res["run_id"])}


@router.get("/ibkr-status")
def get_ibkr_paper_status(db: Session = Depends(get_db)) -> dict[str, Any]:
    runtime = load_runtime_settings(db)
    ibkr_cfg = runtime.get("ibkr", {})
    if not ibkr_cfg.get("enabled", False):
        return {
            "connected": False,
            "paper_verified": False,
            "account": "",
            "open_orders_count": 0,
            "positions_count": 0,
            "net_liquidation": None,
            "currency": "USD",
            "message": "IBKR Gateway is disabled in Settings.",
        }
    return probe_ibkr_paper_account(
        str(ibkr_cfg.get("host", "127.0.0.1")),
        int(ibkr_cfg.get("port", 4002)),
        int(ibkr_cfg.get("client_id", 17)),
    )


@router.post("/reconcile")
def reconcile_ibkr_now(db: Session = Depends(get_db)) -> dict[str, Any]:
    runtime = load_runtime_settings(db)
    run = ensure_active_paper_run(db, runtime)
    if run.mode != "ibkr_paper":
        return {
            "reconcile": {"reconciled": False, "reason": "Active run is in Virtual mode; switch mode to IBKR Paper first."},
            "overview": build_paper_overview(db, run_id=run.id),
        }
    res = step_ibkr_paper_lifecycle(db, run=run, runtime_settings=runtime)
    return {"reconcile": res, "overview": build_paper_overview(db, run_id=run.id)}


@router.post("/trades/{trade_id}/close")
def close_trade(trade_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    trade = close_paper_trade_manually(db, trade_id)
    if trade is None:
        raise HTTPException(status_code=404, detail=f"Paper trade {trade_id} not found.")
    return {
        "trade": serialize_paper_trade(trade),
        "overview": build_paper_overview(db, run_id=trade.run_id),
    }


@router.post("/kill-switch")
def toggle_kill_switch(body: KillSwitchBody, db: Session = Depends(get_db)) -> dict[str, Any]:
    run = set_paper_kill_switch(db, halt=body.halt, reason=body.reason, close_all=body.close_all)
    return build_paper_overview(db, run_id=run.id)


@router.get("/trades/export.csv")
def export_paper_trades_csv(
    run_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
) -> Response:
    run = db.get(PaperAccountRun, run_id) if run_id is not None else ensure_active_paper_run(db)
    resolved_id = run.id if run is not None else 1
    csv_text = export_trades_csv(db, run_id=resolved_id)
    return Response(
        content=csv_text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="paper_trades_run_{resolved_id}.csv"'},
    )
