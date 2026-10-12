"""CLI for shell-capable agents and humans. Prints JSON.

    python -m action_network leagues
    python -m action_network games nfl --date tomorrow
    python -m action_network odds nfl --team chiefs --books DraftKings,FanDuel
    python -m action_network public nba --team celtics
    python -m action_network movement 256913
    python -m action_network futures nfl            # list markets
    python -m action_network futures nfl --market mvp
    python -m action_network serve --port 8000      # REST + MCP over HTTP
    python -m action_network mcp                    # MCP over stdio
"""
from __future__ import annotations

import argparse
import json
import sys


def _csv(v):
    return [x.strip() for x in v.split(",") if x.strip()] if v else None


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="action_network", description="Live odds from Action Network")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("leagues")
    g = sub.add_parser("games"); g.add_argument("league"); g.add_argument("--date")
    o = sub.add_parser("odds")
    o.add_argument("league"); o.add_argument("--team"); o.add_argument("--date"); o.add_argument("--game-id", type=int)
    o.add_argument("--markets"); o.add_argument("--books"); o.add_argument("--include-books", action="store_true")
    o.add_argument("--days-ahead", type=int)
    pb = sub.add_parser("public"); pb.add_argument("league"); pb.add_argument("--team"); pb.add_argument("--game-id", type=int); pb.add_argument("--date")
    m = sub.add_parser("movement"); m.add_argument("game_id", type=int); m.add_argument("--books")
    f = sub.add_parser("futures"); f.add_argument("league"); f.add_argument("--market"); f.add_argument("--limit", type=int, default=25)
    s = sub.add_parser("serve"); s.add_argument("--host", default="0.0.0.0"); s.add_argument("--port", type=int, default=8000)
    sub.add_parser("mcp")

    a = p.parse_args(argv)

    if a.cmd == "serve":
        import uvicorn
        uvicorn.run("action_network.api:app", host=a.host, port=a.port)
        return 0
    if a.cmd == "mcp":
        from .mcp_server import main as mcp_main
        mcp_main()
        return 0

    from .client import ActionNetworkError
    from .service import NotFoundError, get_service

    svc = get_service()
    try:
        if a.cmd == "leagues":
            out = svc.list_leagues()
        elif a.cmd == "games":
            out = svc.get_games(a.league, a.date)
        elif a.cmd == "odds":
            out = svc.get_odds(a.league, a.date, a.team, a.game_id, _csv(a.markets), _csv(a.books),
                               a.include_books, a.days_ahead)
        elif a.cmd == "public":
            out = svc.get_public_betting(a.league, a.game_id, a.team, a.date)
        elif a.cmd == "movement":
            out = svc.get_line_movement(a.game_id, _csv(a.books))
        else:
            out = svc.get_futures(a.league, a.market, a.limit) if a.market else svc.list_futures(a.league)
    except (ValueError, NotFoundError, ActionNetworkError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
