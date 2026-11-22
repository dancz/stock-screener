from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.db.database import configure_database, session_scope
from app.db.models import AnalysisSnapshot, ScanRun, Ticker, init_db
from app.main import app


def test_api_screener_watchlists_notes_and_settings(tmp_path: Path):
    db_file = tmp_path / "test_screener.db"
    configure_database(db_file)
    init_db()

    with session_scope() as session:
        run = ScanRun(mode="quick", status="completed", stage="completed", total_tickers=2, processed_tickers=2)
        session.add(run)
        session.flush()

        for sym, score, bias in [("AAPL", 82.5, "bullish"), ("MSFT", 74.0, "bullish")]:
            if session.get(Ticker, sym) is None:
                session.add(Ticker(symbol=sym, name=f"{sym} Inc.", sector="Information Technology"))
                session.flush()
            session.add(
                AnalysisSnapshot(
                    scan_run_id=run.id,
                    symbol=sym,
                    last_price=200.0,
                    change_pct=1.5,
                    composite_score=score,
                    bias=bias,
                    preset_scores_json=json.dumps({"all_around_swing": score, "channel_bounce": score + 2}),
                    signals_json=json.dumps(
                        [{"code": "LOWER_CHANNEL_TOUCH", "label": "Lower Channel Touch", "direction": "bullish", "severity": "high", "score_impact": 25.0, "detail": "Touch"}]
                        if sym == "AAPL"
                        else []
                    ),
                    risk_flags_json="[]",
                    overlays_json="[]",
                    trade_plan_json=json.dumps({"direction": "LONG", "setup_name": "Bounce", "rationale": "Test", "entry_low": 198, "entry_high": 201, "stop_loss": 194, "tp1": 208, "tp2": 215, "risk_reward": 2.4, "atr": 3.0, "risk_per_share": 5.5}),
                    option_proposals_json=json.dumps({"iv_metrics": {}, "proposals": []}),
                    fundamentals_json="{}",
                    events_insiders_json=json.dumps({"days_to_earnings": 25}),
                    is_deep_enriched=True,
                )
            )

    with TestClient(app) as client:
        # Health & Screener filtering
        assert client.get("/api/health").status_code == 200
        scr = client.get("/api/screener?signal=LOWER_CHANNEL_TOUCH").json()
        assert scr["total"] == 1
        assert scr["items"][0]["symbol"] == "AAPL"

        # Stock detail
        det = client.get("/api/stocks/AAPL?enrich=false").json()
        assert det["symbol"] == "AAPL"
        assert det["composite_score"] == 82.5

        # Custom Watchlist CRUD & multi-list membership
        wl_resp = client.post("/api/watchlists", json={"name": "Channel Bounces", "color": "#38bdf8"})
        assert wl_resp.status_code == 201
        wl_id = wl_resp.json()["id"]

        add_resp = client.post(f"/api/watchlists/{wl_id}/items/AAPL")
        assert add_resp.status_code == 200
        assert "AAPL" in add_resp.json()["symbols"]

        # Filter screener by watchlist
        wl_scr = client.get(f"/api/screener?watchlist_id={wl_id}").json()
        assert wl_scr["total"] == 1
        assert wl_scr["items"][0]["symbol"] == "AAPL"

        # Notes & tags persistence
        note_resp = client.put("/api/stocks/AAPL/notes", json={"note_text": "Strong bounce", "tags": ["swing", "channel"]})
        assert note_resp.status_code == 200
        det_after = client.get("/api/stocks/AAPL?enrich=false").json()
        assert det_after["note_text"] == "Strong bounce"
        assert det_after["tags"] == ["swing", "channel"]

        # Settings & plugins
        set_resp = client.get("/api/settings").json()
        assert len(set_resp["plugins"]) >= 10
        upd = client.put("/api/settings", json={"account": {"equity": 50000, "risk_pct": 1.5}}).json()
        assert upd["settings"]["account"]["equity"] == 50000
