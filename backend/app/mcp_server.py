from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

# Ensure backend/ is importable regardless of current working directory
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

DEFAULT_DB_PATH = BACKEND_DIR / "data" / "screener.db"

SERVER_NAME = "stock-screener-dev"
SERVER_VERSION = "0.1.0"
PROTOCOL_VERSION = "2024-11-05"

FORBIDDEN_SQL_PATTERN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|REPLACE|TRUNCATE|ATTACH|DETACH|VACUUM|REINDEX|PRAGMA)\b",
    re.IGNORECASE,
)


def _get_db_path() -> Path:
    return Path(os.environ.get("SCREENER_DB_PATH", str(DEFAULT_DB_PATH)))


def _open_readonly_conn(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise FileNotFoundError(f"Database file not found at {db_path}. Run a scan or start the backend first.")
    uri = f"file:{db_path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn


def validate_readonly_sql(sql: str) -> str:
    cleaned = sql.strip().rstrip(";").strip()
    if not cleaned:
        raise ValueError("SQL query cannot be empty.")
    if ";" in cleaned:
        raise ValueError("Multiple SQL statements are not allowed.")
    upper = cleaned.lstrip("(").upper()
    if not (upper.startswith("SELECT") or upper.startswith("WITH")):
        raise ValueError("Only read-only SELECT or WITH ... SELECT queries are permitted.")
    if FORBIDDEN_SQL_PATTERN.search(cleaned):
        raise ValueError("Query contains forbidden write/DDL keywords.")
    return cleaned


def tool_query_screener_db(sql: str) -> dict[str, Any]:
    cleaned = validate_readonly_sql(sql)
    conn = _open_readonly_conn(_get_db_path())
    try:
        cur = conn.execute(cleaned)
        rows = cur.fetchmany(200)
        columns = [d[0] for d in cur.description] if cur.description else []
        data = [dict(zip(columns, row)) for row in rows]
        return {"columns": columns, "row_count": len(data), "rows": data}
    finally:
        conn.close()


def tool_list_screener_plugins() -> dict[str, Any]:
    try:
        from app.core.registry import PluginRegistry

        reg = PluginRegistry()
        reg.discover_all(force=True)
        return {
            "sources": [{"id": k, "name": v.display_name, "description": v.description} for k, v in reg.sources.items()],
            "analyzers": [
                {
                    "id": k,
                    "name": v.display_name,
                    "category": v.category,
                    "default_weight": v.default_weight,
                    "requires_deep_data": v.requires_deep_data,
                    "description": v.description,
                }
                for k, v in reg.analyzers.items()
            ],
            "options": [{"id": k, "name": v.display_name, "description": v.description} for k, v in reg.options.items()],
            "notifiers": [{"id": k, "name": v.display_name, "description": v.description} for k, v in reg.notifiers.items()],
        }
    except ImportError:
        # Running under global Python without venv deps — delegate to backend/.venv if available
        import subprocess

        for venv_py in (
            BACKEND_DIR / ".venv" / "Scripts" / "python.exe",
            BACKEND_DIR / ".venv" / "bin" / "python",
        ):
            if venv_py.exists():
                out = subprocess.check_output(
                    [
                        str(venv_py),
                        "-c",
                        "import json; from app.mcp_server import tool_list_screener_plugins; print(json.dumps(tool_list_screener_plugins()))",
                    ],
                    cwd=str(BACKEND_DIR),
                    text=True,
                )
                return json.loads(out)
        raise


def tool_get_latest_screener_top(limit: int = 20, preset: str = "all_around_swing") -> dict[str, Any]:
    limit = max(1, min(int(limit), 100))
    conn = _open_readonly_conn(_get_db_path())
    try:
        rows = conn.execute(
            """
            SELECT s.symbol, t.name, t.sector, s.last_price, s.change_pct,
                   s.composite_score, s.bias, s.preset_scores_json, s.signals_json,
                   s.risk_flags_json, s.analyzed_at, s.is_deep_enriched
            FROM analysis_snapshots s
            JOIN tickers t ON t.symbol = s.symbol
            INNER JOIN (
                SELECT symbol, MAX(id) AS max_id
                FROM analysis_snapshots
                GROUP BY symbol
            ) latest ON latest.max_id = s.id
            ORDER BY s.composite_score DESC
            """
        ).fetchall()

        results = []
        for row in rows:
            preset_scores = json.loads(row["preset_scores_json"] or "{}")
            score = float(preset_scores.get(preset, row["composite_score"]))
            signals = json.loads(row["signals_json"] or "[]")
            risk_flags = json.loads(row["risk_flags_json"] or "[]")
            results.append(
                {
                    "symbol": row["symbol"],
                    "name": row["name"],
                    "sector": row["sector"],
                    "last_price": row["last_price"],
                    "change_pct": row["change_pct"],
                    "score": round(score, 2),
                    "bias": row["bias"],
                    "top_signals": [s.get("label") for s in signals[:3]],
                    "risk_flags": [r.get("label") for r in risk_flags],
                    "is_deep_enriched": bool(row["is_deep_enriched"]),
                    "analyzed_at": row["analyzed_at"],
                }
            )

        results.sort(key=lambda item: item["score"], reverse=True)
        return {"preset": preset, "count": len(results[:limit]), "items": results[:limit]}
    finally:
        conn.close()


def tool_get_ticker_snapshot(symbol: str) -> dict[str, Any]:
    sym = symbol.strip().upper()
    if not sym:
        raise ValueError("symbol must be non-empty.")
    conn = _open_readonly_conn(_get_db_path())
    try:
        row = conn.execute(
            """
            SELECT s.*, t.name, t.sector, t.industry, t.primary_benchmark, t.sector_etf
            FROM analysis_snapshots s
            JOIN tickers t ON t.symbol = s.symbol
            WHERE s.symbol = ?
            ORDER BY s.id DESC
            LIMIT 1
            """,
            (sym,),
        ).fetchone()
        if row is None:
            return {"found": False, "symbol": sym}
        return {
            "found": True,
            "symbol": row["symbol"],
            "name": row["name"],
            "sector": row["sector"],
            "industry": row["industry"],
            "primary_benchmark": row["primary_benchmark"],
            "sector_etf": row["sector_etf"],
            "analyzed_at": row["analyzed_at"],
            "last_price": row["last_price"],
            "change_pct": row["change_pct"],
            "composite_score": row["composite_score"],
            "bias": row["bias"],
            "preset_scores": json.loads(row["preset_scores_json"] or "{}"),
            "signals": json.loads(row["signals_json"] or "[]"),
            "risk_flags": json.loads(row["risk_flags_json"] or "[]"),
            "trade_plan": json.loads(row["trade_plan_json"] or "{}"),
            "option_proposals": json.loads(row["option_proposals_json"] or "[]"),
            "fundamentals": json.loads(row["fundamentals_json"] or "{}"),
            "events_insiders": json.loads(row["events_insiders_json"] or "{}"),
            "plugin_results": json.loads(row["plugin_results_json"] or "{}"),
        }
    finally:
        conn.close()


TOOLS = [
    {
        "name": "query_screener_db",
        "description": "Execute a read-only SELECT SQL query against the stock screener SQLite database (max 200 rows).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "sql": {"type": "string", "description": "Read-only SELECT or WITH ... SELECT SQL statement."}
            },
            "required": ["sql"],
        },
    },
    {
        "name": "list_screener_plugins",
        "description": "List all auto-discovered source, analyzer, options, and notifier plugins.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_latest_screener_top",
        "description": "Return the top ranked stocks from the latest stock screener snapshots for a scoring preset.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 20, "minimum": 1, "maximum": 100},
                "preset": {"type": "string", "default": "all_around_swing"},
            },
        },
    },
    {
        "name": "get_ticker_snapshot",
        "description": "Return the full latest analysis snapshot (scores, signals, trade plan, options, fundamentals) for a ticker.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "US equity ticker symbol, e.g., AAPL or NVDA."}
            },
            "required": ["symbol"],
        },
    },
]


def handle_rpc_request(msg: dict[str, Any]) -> dict[str, Any] | None:
    req_id = msg.get("id")
    method = msg.get("method", "")
    params = msg.get("params") or {}

    if method.startswith("notifications/"):
        return None

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        }

    if method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS}}

    if method == "tools/call":
        tool_name = params.get("name")
        args = params.get("arguments") or {}
        try:
            if tool_name == "query_screener_db":
                payload = tool_query_screener_db(str(args.get("sql", "")))
            elif tool_name == "list_screener_plugins":
                payload = tool_list_screener_plugins()
            elif tool_name == "get_latest_screener_top":
                payload = tool_get_latest_screener_top(
                    limit=int(args.get("limit", 20)),
                    preset=str(args.get("preset", "all_around_swing")),
                )
            elif tool_name == "get_ticker_snapshot":
                payload = tool_get_ticker_snapshot(str(args.get("symbol", "")))
            else:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"},
                }

            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(payload, indent=2)}],
                    "isError": False,
                },
            }
        except Exception as exc:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": f"Error: {exc}"}],
                    "isError": True,
                },
            }

    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def main() -> None:
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            err = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": f"Parse error: {exc}"}}
            sys.stdout.write(json.dumps(err) + "\n")
            sys.stdout.flush()
            continue

        response = handle_rpc_request(request)
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
