"""Static configuration for the Action Network client.

Everything that encodes knowledge about Action Network's (undocumented) web API
lives here, so when the site changes there is exactly one place to fix.
Most values can be overridden with environment variables.
"""
from __future__ import annotations

import json
import os
from typing import Dict, List

API_BASE = os.environ.get("AN_API_BASE", "https://api.actionnetwork.com/web/v1")

HEADERS = {
    "Accept": "application/json",
    "Origin": "https://www.actionnetwork.com",
    "Referer": "https://www.actionnetwork.com/",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
    ),
}

# League slug -> scoreboard path + extra query params.
# "verified" = already used successfully by scripts/action_network.py.
LEAGUES: Dict[str, dict] = {
    "nfl": {"path": "nfl", "params": {}, "verified": True},
    "nba": {"path": "nba", "params": {}, "verified": True},
    "mlb": {"path": "mlb", "params": {}, "verified": True},
    "ncaab": {"path": "ncaab", "params": {"division": "D1", "tournament": "0"}, "verified": True},
    "soccer": {"path": "soccer", "params": {}, "verified": True},
    "nhl": {"path": "nhl", "params": {}, "verified": False},
    "ncaaf": {"path": "ncaaf", "params": {"division": "FBS"}, "verified": False},
    "wnba": {"path": "wnba", "params": {}, "verified": False},
    # Soccer competitions share the soccer scoreboard and are filtered by the game's
    # league_name (values seen in the archived soccer data).
    "epl": {"path": "soccer", "params": {}, "league_name": "epl", "verified": True},
    "laliga": {"path": "soccer", "params": {}, "league_name": "laliga", "verified": True},
    "bundesliga": {"path": "soccer", "params": {}, "league_name": "bundesliga", "verified": True},
    "seriea": {"path": "soccer", "params": {}, "league_name": "seriea", "verified": True},
    "mls": {"path": "soccer", "params": {}, "league_name": "mls", "verified": True},
    "champs": {"path": "soccer", "params": {}, "league_name": "champions", "verified": True},
}

# Book ids requested on every scoreboard call (same set the archiver uses).
DEFAULT_BOOK_IDS: List[int] = [15, 30, 68, 69, 71, 75, 76, 79, 123, 247, 972]

# Names confirmed against Action Network's /books endpoint (Oct 2026). Most ids are the
# NJ book. 76 was not listed there. Override with AN_BOOK_NAMES='{"68": "DraftKings"}'.
BOOK_NAMES: Dict[int, str] = {
    15: "Consensus",
    30: "Open",
    68: "DraftKings",
    69: "FanDuel",
    71: "BetRivers",
    75: "BetMGM",
    76: "book_76",
    79: "bet365",
    123: "Caesars",
    247: "Unibet",
    972: "BetRivers NY",
}
if os.environ.get("AN_BOOK_NAMES"):
    BOOK_NAMES.update({int(k): v for k, v in json.loads(os.environ["AN_BOOK_NAMES"]).items()})

# Rows that are not bettable markets; excluded from "best line" and consensus math.
NON_MARKET_BOOK_IDS = {
    int(x) for x in os.environ.get("AN_NON_MARKET_BOOK_IDS", "15,30").split(",") if x.strip()
}
CONSENSUS_BOOK_ID = int(os.environ.get("AN_CONSENSUS_BOOK_ID", "15"))

# League ids the archiver walks for futures; types are filtered by "<slug>_" prefix.
FUTURES_LEAGUE_IDS: List[int] = [
    int(x) for x in os.environ.get("AN_FUTURES_LEAGUE_IDS", "0,1,2").split(",") if x.strip()
]

# Freshness / politeness.
SCOREBOARD_TTL_SECONDS = float(os.environ.get("AN_SCOREBOARD_TTL", "45"))
FUTURES_TTL_SECONDS = float(os.environ.get("AN_FUTURES_TTL", "600"))
MIN_REQUEST_INTERVAL_SECONDS = float(os.environ.get("AN_MIN_REQUEST_INTERVAL", "1.0"))
REQUEST_TIMEOUT_SECONDS = float(os.environ.get("AN_REQUEST_TIMEOUT", "15"))
MAX_RETRIES = int(os.environ.get("AN_MAX_RETRIES", "3"))

# Games are dated in US Eastern on actionnetwork.com.
SITE_TIMEZONE = os.environ.get("AN_SITE_TZ", "America/New_York")

# SQLite file for line-movement snapshots. Empty string disables history.
HISTORY_DB_PATH = os.environ.get("AN_HISTORY_DB", "data/odds_history.sqlite")
