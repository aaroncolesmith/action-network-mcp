"""Loose team-name matching so agents can say "chiefs", "KC" or "Kansas City"."""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import List

# Common nicknames that share no text with the official name.
ALIASES = {
    "niners": "49ers",
    "sixers": "76ers",
    "pats": "patriots",
    "bucs": "buccaneers",
    "cards": "cardinals",
    "jags": "jaguars",
    "phins": "dolphins",
    "dubs": "warriors",
    "cavs": "cavaliers",
    "mavs": "mavericks",
    "wolves": "timberwolves",
    "blazers": "trail blazers",
    "nats": "nationals",
    "yanks": "yankees",
    "barca": "barcelona",
    "man utd": "manchester united",
    "man united": "manchester united",
    "man city": "manchester city",
}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower()).strip()


def _team_names(team: dict) -> List[str]:
    return [_norm(team.get(k) or "") for k in ("name", "short_name", "abbr") if team.get(k)]


def team_score(query: str, team: dict) -> float:
    """0..1 match score between a free-text query and a team record."""
    q = _norm(query)
    q = ALIASES.get(q, q)
    if not q:
        return 0.0
    best = 0.0
    for name in _team_names(team):
        if q == name:
            return 1.0
        words = name.split()
        if q in words or (len(q) >= 3 and re.search(rf"\b{re.escape(q)}\b", name)):
            best = max(best, 0.95)
        elif len(q) >= 4 and q in name:
            best = max(best, 0.85)
        best = max(best, SequenceMatcher(None, q, name).ratio() * 0.8)
    return best


def match_games(query: str, games: List[dict], threshold: float = 0.6) -> List[dict]:
    """Games where either side matches `query`, best match first."""
    scored = []
    for g in games:
        s = max(team_score(query, g["away"]), team_score(query, g["home"]))
        if s >= threshold:
            scored.append((s, g))
    if not scored:
        return []
    top = max(s for s, _ in scored)
    # keep only matches close to the best one (avoid "Texas" pulling every Texas school)
    return [g for s, g in sorted(scored, key=lambda x: -x[0]) if s >= top - 0.05]
