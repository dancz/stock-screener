from __future__ import annotations

import json
import pytest

from app.mcp_server import handle_rpc_request, validate_readonly_sql


def test_mcp_server_tools_and_readonly_guard():
    list_resp = handle_rpc_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert list_resp is not None
    tool_names = {t["name"] for t in list_resp["result"]["tools"]}
    assert {
        "query_screener_db",
        "list_screener_plugins",
        "get_latest_screener_top",
        "get_ticker_snapshot",
    }.issubset(tool_names)

    plugins_resp = handle_rpc_request(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "list_screener_plugins", "arguments": {}},
        }
    )
    assert plugins_resp is not None
    assert plugins_resp["result"]["isError"] is False
    parsed = json.loads(plugins_resp["result"]["content"][0]["text"])
    assert any(a["id"] == "trend_channels" for a in parsed["analyzers"])

    # Verify SQL guard rejects mutations/multi-statements
    assert validate_readonly_sql("SELECT symbol FROM tickers LIMIT 5") == "SELECT symbol FROM tickers LIMIT 5"
    for bad in [
        "DROP TABLE tickers",
        "DELETE FROM tickers",
        "INSERT INTO tickers(symbol) VALUES ('X')",
        "SELECT 1; DROP TABLE tickers",
        "PRAGMA writable_schema=ON",
    ]:
        with pytest.raises(ValueError):
            validate_readonly_sql(bad)
