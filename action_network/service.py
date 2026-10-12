"""High-level operations shared by the REST API, MCP server, and CLI.

Every public method returns plain JSON-serializable dicts with an `as_of`
timestamp so callers can judge freshness.
"""
from __future__ import annotations

import logging
from datetime import date as date_cls, datetime, timedelta, timezone
from statistics import median
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    from backports.zoneinfo import ZoneInfo  # type: ignore

from . import config
from .client import ActionNetworkClient, ActionNetworkError, league_spec
from .history import HistoryStore
from .odds import MARKETS, book_name, game_header, implied_probability, parse_game, summarize_game
from .teams import match_games

log = logging.getLogger(__name__)

# How far ahead to look when an agent asks by team with no date.
DEFAULT_LOOKAHEAD_DAYS = {"nfl": 7, "ncaaf": 7}


class NotFoundError(LookupError):
    pass


def site_today() -> date_cls:
    return datetime.now(ZoneInfo(config.SITE_TIMEZONE)).date()


def parse_date(value: Optional[str]) -> date_cls:
    """Accepts None/'today', 'tomorrow', 'yesterday', 'YYYY-MM-DD', 'YYYYMMDD'."""
    if value is None or str(value).strip().lower() in ("", "today"):
        return site_today()
    v = str(value).strip().lower()
    if v == "tomorrow":
        return site_today() + timedelta(days=1)
    if v == "yesterday":
        return site_today() - timedelta(days=1)
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"Unrecognized date '{value}'. Use YYYY-MM-DD, 'today', or 'tomorrow'.")


def site_date(iso: str) -> date_cls:
    """UTC start_time -> the US Eastern calendar date the site files it under."""
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(ZoneInfo(config.SITE_TIMEZONE)).date()


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


class OddsService:
    def __init__(self, client: Optional[ActionNetworkClient] = None, history: Optional[HistoryStore] = None,
                 enable_history: bool = True) -> None:
        self.client = client or ActionNetworkClient()
        if history is None and enable_history and config.HISTORY_DB_PATH:
            try:
                history = HistoryStore(config.HISTORY_DB_PATH)
            except Exception as exc:  # pragma: no cover - disk issues shouldn't kill live odds
                log.warning("History disabled: %s", exc)
        self.history = history

    # ---------------------------------------------------------- internals
    def _scoreboard_games(self, league: str, day: date_cls) -> Tuple[List[dict], datetime, bool]:
        """Games on one site (US Eastern) date."""
        spec = league_spec(league)
        if spec.get("weekly"):
            return self._weekly_games(league, day)
        payload, fetched_at, cached = self.client.scoreboard(league, day.strftime("%Y%m%d"))
        return self._parse_payload(league, payload, fetched_at, cached), fetched_at, cached

    def _parse_payload(self, league: str, payload: Any, fetched_at: datetime, cached: bool) -> List[dict]:
        spec = league_spec(league)
        if not isinstance(payload, dict) or "games" not in payload:
            raise ActionNetworkError("Unexpected scoreboard response shape (no 'games' key)")
        if not cached and self.history is not None:
            try:
                self.history.record_scoreboard(spec["path"], payload, fetched_at)
            except Exception as exc:  # pragma: no cover
                log.warning("History write failed: %s", exc)
        games = payload.get("games") or []
        if spec.get("league_name"):
            games = [g for g in games if (g.get("league_name") or "").lower() == spec["league_name"]]
        return [parse_game(g, league) for g in games]

    def _weekly_games(self, league: str, day: date_cls, max_extra_weeks: int = 2) -> Tuple[List[dict], datetime, bool]:
        """Football: start from the current week and page forward until `day` is covered."""
        payload, oldest, all_cached = self.client.scoreboard(league)
        games = self._parse_payload(league, payload, oldest, all_cached)
        weeks = [g["week"] for g in games if isinstance(g.get("week"), int)]
        week = max(weeks) if weeks else None
        for _ in range(max_extra_weeks):
            last = max((site_date(g["start_time"]) for g in games if g["start_time"]), default=None)
            if week is None or last is None or day <= last:
                break
            week += 1
            payload, fetched_at, cached = self.client.scoreboard(league, week=week)
            more = self._parse_payload(league, payload, fetched_at, cached)
            if not more:
                break
            games += more
            oldest, all_cached = min(oldest, fetched_at), all_cached and cached
        seen, out = set(), []
        for g in games:
            if g["game_id"] not in seen and g["start_time"] and site_date(g["start_time"]) == day:
                seen.add(g["game_id"])
                out.append(g)
        return out, oldest, all_cached

    def _find(self, league: str, date: Optional[str], team: Optional[str], game_id: Optional[int],
              days_ahead: Optional[int]) -> Tuple[List[dict], List[str], datetime, bool]:
        """Locate games; searches forward day by day when only team/game_id is given."""
        league = league.lower()
        if date is not None:
            days = [parse_date(date)]
        else:
            ahead = days_ahead if days_ahead is not None else DEFAULT_LOOKAHEAD_DAYS.get(league, 2)
            start = site_today() - (timedelta(days=1) if game_id else timedelta(0))
            days = [start + timedelta(days=i) for i in range(max(1, ahead + 1))] if (team or game_id) else [site_today()]

        oldest: Optional[datetime] = None
        all_cached = True
        searched: List[str] = []
        for day in days:
            games, fetched_at, cached = self._scoreboard_games(league, day)
            searched.append(day.isoformat())
            oldest = fetched_at if oldest is None or fetched_at < oldest else oldest
            all_cached = all_cached and cached
            if game_id is not None:
                games = [g for g in games if str(g["game_id"]) == str(game_id)]
            if team:
                games = match_games(team, games)
            if games or not (team or game_id):
                return games, searched, oldest, all_cached
        return [], searched, oldest or datetime.now(timezone.utc), all_cached

    def _envelope(self, as_of: datetime, cached: bool, **body: Any) -> dict:
        return {"source": "actionnetwork.com", "as_of": _iso(as_of), "cached": cached, **body}

    # ------------------------------------------------------------- public
    def list_leagues(self) -> dict:
        return {
            "leagues": [
                {"league": k, "verified": v["verified"]} for k, v in sorted(config.LEAGUES.items())
            ],
            "books": [{"book_id": b, "book": book_name(b), "bettable": b not in config.NON_MARKET_BOOK_IDS}
                      for b in config.DEFAULT_BOOK_IDS],
        }

    def get_games(self, league: str, date: Optional[str] = None) -> dict:
        league_spec(league)
        day = parse_date(date)
        games, fetched_at, cached = self._scoreboard_games(league.lower(), day)
        return self._envelope(fetched_at, cached, league=league.lower(), date=day.isoformat(),
                              count=len(games), games=[game_header(g) for g in games])

    def get_odds(
        self,
        league: str,
        date: Optional[str] = None,
        team: Optional[str] = None,
        game_id: Optional[int] = None,
        markets: Optional[Iterable[str]] = None,
        books: Optional[Iterable[str]] = None,
        include_books: bool = False,
        days_ahead: Optional[int] = None,
    ) -> dict:
        league_spec(league)
        games, searched, as_of, cached = self._find(league, date, team, game_id, days_ahead)
        if (team or game_id) and not games:
            what = f"team '{team}'" if team else f"game_id {game_id}"
            raise NotFoundError(f"No {league} game found for {what} on {', '.join(searched)}")
        mk = [m.strip().lower() for m in markets] if markets else list(MARKETS)
        return self._envelope(
            as_of, cached, league=league.lower(), dates_searched=searched, count=len(games),
            games=[summarize_game(g, mk, books, include_books) for g in games],
        )

    def get_public_betting(self, league: str, game_id: Optional[int] = None, team: Optional[str] = None,
                           date: Optional[str] = None) -> dict:
        league_spec(league)
        games, searched, as_of, cached = self._find(league, date, team, game_id, None)
        if (team or game_id) and not games:
            raise NotFoundError(f"No {league} game found on {', '.join(searched)}")
        out = []
        for g in games:
            out.append({
                "game_id": g["game_id"],
                "matchup": f"{g['away']['name']} @ {g['home']['name']}",
                "start_time": g["start_time"],
                "public": g["public"],
            })
        return self._envelope(as_of, cached, league=league.lower(), count=len(out), games=out,
                              note="tickets_pct = share of bets; money_pct = share of dollars wagered.")

    def get_line_movement(self, game_id: int, books: Optional[Iterable[str]] = None) -> dict:
        if self.history is None:
            raise NotFoundError("Line history is disabled on this server (AN_HISTORY_DB is empty).")
        ids = None
        if books:
            wanted = {str(b).strip().lower() for b in books}
            ids = [b for b in set(config.BOOK_NAMES) | set(config.DEFAULT_BOOK_IDS)
                   if book_name(b).lower() in wanted or str(b) in wanted]
        result = self.history.movement(int(game_id), ids)
        result["as_of"] = _iso(datetime.now(timezone.utc))
        return result

    def poll(self, leagues: Iterable[str], days_ahead: int = 1) -> Dict[str, int]:
        """Fetch upcoming scoreboards so history accumulates without agent traffic."""
        counts: Dict[str, int] = {}
        for league in leagues:
            n = 0
            for i in range(days_ahead + 1):
                try:
                    games, _, _ = self._scoreboard_games(league, site_today() + timedelta(days=i))
                    n += len(games)
                except Exception as exc:
                    log.warning("Poll %s failed: %s", league, exc)
            counts[league] = n
        return counts

    # ------------------------------------------------------------ futures
    def _futures_types(self, league: str) -> Tuple[List[dict], datetime, bool]:
        prefix = f"{league.lower()}_"
        found: Dict[str, dict] = {}
        oldest, all_cached = datetime.now(timezone.utc), True
        for league_id in config.FUTURES_LEAGUE_IDS:
            try:
                payload, fetched_at, cached = self.client.futures_available(league_id)
            except ActionNetworkError as exc:
                log.info("futures/available for league %s failed: %s", league_id, exc)
                continue
            oldest, all_cached = min(oldest, fetched_at), all_cached and cached
            for f in (payload or {}).get("futures") or []:
                t = f.get("type") or ""
                if t.startswith(prefix) and t not in found:
                    found[t] = {"market": t, "name": f.get("name") or f.get("display_name"), "league_id": league_id}
        return list(found.values()), oldest, all_cached

    def list_futures(self, league: str) -> dict:
        types, as_of, cached = self._futures_types(league)
        return self._envelope(as_of, cached, league=league.lower(), count=len(types),
                              markets=[{"market": t["market"], "name": t["name"]} for t in types])

    def get_futures(self, league: str, market: str, limit: int = 25) -> dict:
        types, _, _ = self._futures_types(league)
        q = market.strip().lower()
        match = next((t for t in types if t["market"].lower() == q), None)
        if match is None:
            words = [w for w in q.replace("_", " ").split() if w]
            scored = [(sum(w in f"{t['market']} {t['name'] or ''}".lower().replace("_", " ") for w in words), t)
                      for t in types]
            scored = [s for s in scored if s[0] > 0]
            if not scored:
                raise NotFoundError(f"No {league} futures market matching '{market}'. Call list_futures first.")
            match = max(scored, key=lambda s: (s[0], -len(s[1]["market"])))[1]

        payload, as_of, cached = self.client.futures_market(match["league_id"], match["market"])
        return self._envelope(as_of, cached, league=league.lower(), market=match["market"],
                              name=(payload or {}).get("name") or match["name"],
                              outcomes=summarize_futures(payload)[: max(1, limit)])


_default_service: Optional[OddsService] = None


def get_service() -> OddsService:
    """Process-wide shared service, so REST and MCP share one cache and history."""
    global _default_service
    if _default_service is None:
        _default_service = OddsService()
    return _default_service


def set_service(service: Optional[OddsService]) -> None:
    global _default_service
    _default_service = service


def summarize_futures(payload: dict) -> List[dict]:
    """Aggregate a futures market across books (mirrors the archiver's get_bet_data)."""
    payload = payload or {}
    teams = {t.get("id"): t.get("full_name") for t in payload.get("teams") or []}
    players = {p.get("id"): p.get("full_name") for p in payload.get("players") or []}
    groups: Dict[tuple, dict] = {}
    for book in payload.get("books") or []:
        book_id = book.get("book_id")
        if book_id in config.NON_MARKET_BOOK_IDS:
            continue
        for o in book.get("odds") or []:
            money = o.get("money")
            if money in (None, 0):
                continue
            pid = o.get("player_id")
            try:
                pid = int(pid) if pid not in (None, "", 0, "0") else None
            except (TypeError, ValueError):
                pid = None
            name = players.get(pid) if pid else teams.get(o.get("team_id"))
            if not name or name == "0":
                continue
            key = (name, o.get("value"), o.get("option_type_id"))
            g = groups.setdefault(key, {"outcome": name, "value": o.get("value"),
                                        "option_type_id": o.get("option_type_id"), "prices": []})
            g["prices"].append((float(money), book_id))
    out = []
    for g in groups.values():
        prices = [p for p, _ in g["prices"]]
        best_price, best_book = max(g["prices"], key=lambda x: x[0])
        med = median(prices)
        out.append({
            "outcome": g["outcome"],
            "value": g["value"],
            "option_type_id": g["option_type_id"],
            "median_price": int(round(med)),
            "best_price": int(round(best_price)),
            "best_book": book_name(best_book),
            "book_count": len(set(b for _, b in g["prices"])),
            "implied_probability": round(implied_probability(med) or 0, 4),
        })
    out.sort(key=lambda r: -r["implied_probability"])
    return out
