"""MCP server exposing Action Network odds as agent tools.

Run locally over stdio (Claude Code, Claude Desktop, Cursor, any MCP client):
    python -m action_network.mcp_server

Or over HTTP as part of the REST app (see action_network.api), at /mcp.
"""
import functools
from typing import Literal, Optional

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from .client import ActionNetworkError
from .service import get_service

INSTRUCTIONS = """\
Live sports betting odds from Action Network across major US sportsbooks.
Typical flow: get_odds(league, team=...) for a specific game, or get_odds(league) for a slate.
Prices are American odds. 'best' = best price available per side across books;
'consensus' = median line across books with no-vig win probabilities.
Every response includes `as_of` (UTC). Data is cached for ~45s upstream.
Leagues: nfl, nba, mlb, nhl, ncaaf, ncaab, wnba, soccer (all), epl, laliga, bundesliga, seriea, mls, champs.
"""

League = Literal[
    "nfl", "nba", "mlb", "nhl", "ncaaf", "ncaab", "wnba",
    "soccer", "epl", "laliga", "bundesliga", "seriea", "mls", "champs",
]
Market = Literal["moneyline", "spread", "total"]

READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=True)

mcp = MCPServer(name="action-network-odds", instructions=INSTRUCTIONS, version="0.1.0")


def agent_errors(fn):
    """Surface expected failures (bad league, no game found, upstream down) to the agent."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ActionNetworkError as exc:
            raise ToolError(f"Action Network upstream error: {exc}") from exc
        except (ValueError, LookupError) as exc:
            raise ToolError(str(exc)) from exc

    return wrapper


@mcp.tool(annotations=READ_ONLY)
@agent_errors
def list_leagues() -> dict:
    """List supported leagues and the sportsbooks that odds are pulled from."""
    return get_service().list_leagues()


@mcp.tool(annotations=READ_ONLY)
@agent_errors
def get_games(league: League, date: Optional[str] = None) -> dict:
    """Schedule and live status for a league on one date.

    Args:
        league: nfl, nba, mlb, nhl, ncaaf, ncaab, wnba, soccer, epl, laliga, bundesliga, seriea, mls, or champs.
        date: YYYY-MM-DD, 'today' (default), 'tomorrow', or 'yesterday'. US Eastern dates.
    """
    return get_service().get_games(league, date)


@mcp.tool(annotations=READ_ONLY)
@agent_errors
def get_odds(
    league: League,
    team: Optional[str] = None,
    date: Optional[str] = None,
    game_id: Optional[int] = None,
    markets: Optional[list[Market]] = None,
    books: Optional[list[str]] = None,
    include_books: bool = False,
    days_ahead: Optional[int] = None,
) -> dict:
    """Current odds: consensus line, no-vig win probability, and best available price per side.

    Args:
        league: nfl, nba, mlb, nhl, ncaaf, ncaab, wnba, soccer, epl, laliga, bundesliga, seriea, mls, or champs.
        team: Optional team name/nickname/abbreviation ("chiefs", "KC"). Without a date,
            searches forward from today (7 days for football, 2 otherwise).
        date: YYYY-MM-DD, 'today', or 'tomorrow'. Omit with no team to get today's slate.
        game_id: Action Network game id from get_games.
        markets: Subset of ["moneyline", "spread", "total"]. Default all.
        books: Restrict to these books, e.g. ["DraftKings", "FanDuel"].
        include_books: Also return the per-book line table (larger response).
        days_ahead: Override how many days forward to search when looking up by team.
    """
    return get_service().get_odds(league, date, team, game_id, markets, books, include_books, days_ahead)


@mcp.tool(annotations=READ_ONLY)
@agent_errors
def get_public_betting(
    league: League, team: Optional[str] = None, game_id: Optional[int] = None, date: Optional[str] = None
) -> dict:
    """Public betting splits: % of tickets vs % of money on each side (sharp vs public signal).

    Args:
        league: League slug.
        team: Optional team to find one game.
        game_id: Optional Action Network game id.
        date: Optional date; default today (or searches forward when team is given).
    """
    return get_service().get_public_betting(league, game_id, team, date)


@mcp.tool(annotations=READ_ONLY)
@agent_errors
def get_line_movement(game_id: int, books: Optional[list[str]] = None) -> dict:
    """How a game's lines have moved since this server started tracking it (open vs current, each change).

    Args:
        game_id: Action Network game id (from get_games or get_odds).
        books: Optional book filter, e.g. ["Consensus", "DraftKings"].
    """
    return get_service().get_line_movement(game_id, books)


@mcp.tool(annotations=READ_ONLY)
@agent_errors
def list_futures(league: League) -> dict:
    """List available futures markets for a league (MVP, win totals, champion, ...)."""
    return get_service().list_futures(league)


@mcp.tool(annotations=READ_ONLY)
@agent_errors
def get_futures(league: League, market: str, limit: int = 25) -> dict:
    """Prices for one futures market, aggregated across books, most likely outcome first.

    Args:
        league: League slug.
        market: Market id from list_futures, or keywords like "mvp" or "super bowl".
        limit: Max outcomes to return.
    """
    return get_service().get_futures(league, market, limit)


def main() -> None:
    mcp.run("stdio")


if __name__ == "__main__":
    main()
