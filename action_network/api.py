"""HTTP server: REST + OpenAPI for any agent framework, and MCP at /mcp.

    uvicorn action_network.api:app --host 0.0.0.0 --port 8000

Endpoints
    GET  /health                  liveness (no auth)
    GET  /openapi.json, /docs     machine + human API docs (no auth)
    GET  /v1/leagues
    GET  /v1/games?league=nfl&date=today
    GET  /v1/odds?league=nfl&team=chiefs
    GET  /v1/public?league=nfl&team=chiefs
    GET  /v1/games/{game_id}/movement
    GET  /v1/futures?league=nfl            (list markets)
    GET  /v1/futures?league=nfl&market=mvp (one market)
    POST /mcp                     MCP streamable HTTP endpoint

Auth: if AN_API_TOKEN is set, every route except /health and docs requires
`Authorization: Bearer <token>` (or `X-API-Key: <token>`).

Background polling: set AN_POLL_LEAGUES=nfl,nba and AN_POLL_INTERVAL=300 to keep
line history accumulating even when no agent is asking.
"""
import asyncio
import contextlib
import hmac
import logging
import os
from typing import Optional

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse

from . import __version__
from .client import ActionNetworkError, UnknownLeagueError
from .mcp_server import mcp
from .service import NotFoundError, get_service

log = logging.getLogger("action_network.api")

API_TOKEN = os.environ.get("AN_API_TOKEN", "")
POLL_LEAGUES = [x.strip() for x in os.environ.get("AN_POLL_LEAGUES", "").split(",") if x.strip()]
POLL_INTERVAL = float(os.environ.get("AN_POLL_INTERVAL", "300"))
# Host the server is reachable as; "127.0.0.1" turns on MCP DNS-rebinding protection.
PUBLIC_HOST = os.environ.get("AN_HOST", "0.0.0.0")
OPEN_PATHS = {"/health", "/docs", "/openapi.json", "/redoc", "/docs/oauth2-redirect"}

mcp_app = mcp.streamable_http_app(
    streamable_http_path="/mcp", stateless_http=True, json_response=True, host=PUBLIC_HOST
)


async def _poller() -> None:
    svc = get_service()
    while True:
        try:
            counts = await asyncio.to_thread(svc.poll, POLL_LEAGUES, 1)
            log.info("polled %s", counts)
        except Exception as exc:  # pragma: no cover
            log.warning("poll failed: %s", exc)
        await asyncio.sleep(POLL_INTERVAL)


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI):
    task = asyncio.create_task(_poller()) if POLL_LEAGUES else None
    async with mcp.session_manager.run():
        yield
    if task:
        task.cancel()


app = FastAPI(
    title="Action Network Odds API",
    version=__version__,
    description=(
        "Live sports odds from Action Network for agents. Prices are American odds. "
        "`best` is the best price per side across books; `consensus` is the median line with "
        "no-vig win probabilities. Every response carries `as_of` (UTC). Also served as MCP at /mcp."
    ),
    lifespan=lifespan,
)

# Serve MCP from the same process (shares cache + history with REST).
app.router.routes.extend(mcp_app.routes)


@app.middleware("http")
async def auth(request: Request, call_next):
    if API_TOKEN and request.url.path not in OPEN_PATHS:
        header = request.headers.get("authorization", "")
        token = header[7:] if header.lower().startswith("bearer ") else request.headers.get("x-api-key", "")
        if not hmac.compare_digest(token.encode(), API_TOKEN.encode()):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
    return await call_next(request)


@app.exception_handler(UnknownLeagueError)
async def _bad_league(_: Request, exc: UnknownLeagueError):
    return JSONResponse({"error": str(exc)}, status_code=400)


@app.exception_handler(ValueError)
async def _bad_value(_: Request, exc: ValueError):
    return JSONResponse({"error": str(exc)}, status_code=400)


@app.exception_handler(NotFoundError)
async def _not_found(_: Request, exc: NotFoundError):
    return JSONResponse({"error": str(exc)}, status_code=404)


@app.exception_handler(ActionNetworkError)
async def _upstream(_: Request, exc: ActionNetworkError):
    return JSONResponse({"error": f"Action Network upstream error: {exc}", "upstream_status": exc.status_code},
                        status_code=502)


def _csv(value: Optional[str]) -> Optional[list]:
    return [v.strip() for v in value.split(",") if v.strip()] if value else None


LEAGUE_Q = Query(..., description="nfl, nba, mlb, nhl, ncaaf, ncaab, wnba, soccer, epl, laliga, bundesliga, seriea, mls, champs")
DATE_Q = Query(None, description="YYYY-MM-DD, today, tomorrow, yesterday (US Eastern)")


@app.get("/health", tags=["meta"])
def health() -> dict:
    return {"ok": True, "version": __version__}


@app.get("/v1/leagues", tags=["odds"], operation_id="list_leagues")
def leagues() -> dict:
    """Supported leagues and sportsbooks."""
    return get_service().list_leagues()


@app.get("/v1/games", tags=["odds"], operation_id="get_games")
def games(league: str = LEAGUE_Q, date: Optional[str] = DATE_Q) -> dict:
    """Schedule and live status for a league on one date."""
    return get_service().get_games(league, date)


@app.get("/v1/odds", tags=["odds"], operation_id="get_odds")
def odds(
    league: str = LEAGUE_Q,
    team: Optional[str] = Query(None, description="Team name, nickname, or abbreviation"),
    date: Optional[str] = DATE_Q,
    game_id: Optional[int] = Query(None, description="Action Network game id"),
    markets: Optional[str] = Query(None, description="Comma list: moneyline,spread,total"),
    books: Optional[str] = Query(None, description="Comma list of books, e.g. DraftKings,FanDuel"),
    include_books: bool = Query(False, description="Include the per-book line table"),
    days_ahead: Optional[int] = Query(None, ge=0, le=14, description="Days to search forward for a team"),
) -> dict:
    """Consensus line, no-vig win probability, and best available price per side."""
    return get_service().get_odds(league, date, team, game_id, _csv(markets), _csv(books), include_books, days_ahead)


@app.get("/v1/public", tags=["odds"], operation_id="get_public_betting")
def public(
    league: str = LEAGUE_Q,
    team: Optional[str] = None,
    game_id: Optional[int] = None,
    date: Optional[str] = DATE_Q,
) -> dict:
    """Ticket % vs money % on each side."""
    return get_service().get_public_betting(league, game_id, team, date)


@app.get("/v1/games/{game_id}/movement", tags=["odds"], operation_id="get_line_movement")
def movement(game_id: int, books: Optional[str] = Query(None, description="Comma list of books")) -> dict:
    """Line movement since this server started tracking the game."""
    return get_service().get_line_movement(game_id, _csv(books))


@app.get("/v1/futures", tags=["odds"], operation_id="get_futures")
def futures(
    league: str = LEAGUE_Q,
    market: Optional[str] = Query(None, description="Market id or keywords; omit to list markets"),
    limit: int = Query(25, ge=1, le=200),
) -> dict:
    """List futures markets, or prices for one market."""
    svc = get_service()
    if not market:
        return svc.list_futures(league)
    return svc.get_futures(league, market, limit)
