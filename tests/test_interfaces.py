import asyncio
import json
import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def api(svc, monkeypatch):
    monkeypatch.setenv("AN_API_TOKEN", "secret")
    from action_network import api as api_mod
    api_mod = importlib.reload(api_mod)
    with TestClient(api_mod.app) as c:
        yield c


AUTH = {"Authorization": "Bearer secret"}


def test_rest_auth_and_odds(api):
    assert api.get("/health").status_code == 200
    assert api.get("/openapi.json").status_code == 200
    assert api.get("/v1/odds", params={"league": "nfl"}).status_code == 401

    r = api.get("/v1/odds", params={"league": "nfl", "team": "chiefs", "books": "DraftKings,FanDuel",
                                    "include_books": "true"}, headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["games"][0]["consensus"]["books_counted"] == 2
    assert {b["book"] for b in body["games"][0]["books"]} == {"DraftKings", "FanDuel"}
    assert api.get("/v1/leagues", headers={"X-API-Key": "secret"}).status_code == 200


def test_rest_errors_and_other_routes(api):
    assert api.get("/v1/odds", params={"league": "xfl"}, headers=AUTH).status_code == 400
    assert api.get("/v1/odds", params={"league": "nfl", "team": "packers", "days_ahead": 0}, headers=AUTH).status_code == 404
    assert api.get("/v1/games", params={"league": "nfl", "date": "2026-10-11"}, headers=AUTH).json()["count"] == 2
    assert api.get("/v1/public", params={"league": "nfl", "game_id": 256913, "date": "today"}, headers=AUTH).status_code == 200
    assert api.get("/v1/games/256913/movement", headers=AUTH).json()["tracked"] is True
    assert api.get("/v1/futures", params={"league": "nfl", "market": "mvp"}, headers=AUTH).json()["outcomes"][0]["outcome"] == "Patrick Mahomes"
    ops = {op["operationId"] for p in api.get("/openapi.json").json()["paths"].values() for op in p.values()}
    assert {"get_odds", "get_games", "get_public_betting", "get_line_movement", "get_futures", "list_leagues"} <= ops


def test_mcp_over_http(api):
    headers = {**AUTH, "Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    assert api.post("/mcp", json={}, headers={"Accept": "application/json"}).status_code == 401

    init = api.post("/mcp", headers=headers, json={
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                   "clientInfo": {"name": "test", "version": "0"}},
    })
    assert init.status_code == 200, init.text
    assert init.json()["result"]["serverInfo"]["name"] == "action-network-odds"
    sid = init.headers.get("mcp-session-id")
    h2 = {**headers, **({"mcp-session-id": sid} if sid else {}),
          "mcp-protocol-version": init.json()["result"]["protocolVersion"]}

    tools = api.post("/mcp", headers=h2, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = {t["name"] for t in tools.json()["result"]["tools"]}
    assert {"get_odds", "get_games", "get_public_betting", "get_line_movement", "list_futures", "get_futures", "list_leagues"} <= names

    call = api.post("/mcp", headers=h2, json={"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                              "params": {"name": "get_odds", "arguments": {"league": "nfl", "team": "KC"}}})
    result = call.json()["result"]
    assert not result.get("isError"), result
    payload = result.get("structuredContent") or json.loads(result["content"][0]["text"])
    assert payload["games"][0]["best"]["moneyline"]["home"]["book"] == "DraftKings"


def test_mcp_tool_errors_are_reported(svc):
    from mcp.server.mcpserver.exceptions import ToolError
    from action_network.mcp_server import mcp
    with pytest.raises(ToolError, match="should be 'nfl'"):
        asyncio.run(mcp.call_tool("get_odds", {"league": "xfl"}))
    with pytest.raises(ToolError, match="No nfl game found for team 'packers'"):
        asyncio.run(mcp.call_tool("get_odds", {"league": "nfl", "team": "packers", "days_ahead": 0}))


def test_cli(svc, capsys):
    from action_network.__main__ import main
    assert main(["odds", "nfl", "--team", "raiders", "--markets", "spread"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert set(out["games"][0]["best"]) == {"spread"}
    assert main(["odds", "xfl"]) == 1
