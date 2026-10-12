"""Turn raw scoreboard payloads into compact, agent-friendly records.

Pure functions, no I/O — easy to test and reuse from the archiver.
Prices are American odds throughout.
"""
from __future__ import annotations

from statistics import median
from typing import Any, Dict, Iterable, List, Optional

from . import config

MARKETS = ("moneyline", "spread", "total")


# ---------------------------------------------------------------- helpers
def _num(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return f


def _price(value: Any) -> Optional[int]:
    f = _num(value)
    if f is None or f == 0:
        return None
    return int(round(f))


def implied_probability(american: Optional[float]) -> Optional[float]:
    """American odds -> implied probability (includes vig)."""
    if american is None or american == 0:
        return None
    if american < 0:
        return -american / (-american + 100.0)
    return 100.0 / (american + 100.0)


def no_vig(probs: Dict[str, Optional[float]]) -> Dict[str, Optional[float]]:
    vals = {k: v for k, v in probs.items() if v is not None}
    total = sum(vals.values())
    if not vals or total <= 0:
        return {k: None for k in probs}
    return {k: (round(v / total, 4) if v is not None else None) for k, v in probs.items()}


def book_name(book_id: Optional[int]) -> str:
    if book_id is None:
        return "unknown"
    return config.BOOK_NAMES.get(int(book_id), f"book_{book_id}")


def _median_price(values: Iterable[Optional[float]]) -> Optional[int]:
    vals = [v for v in values if v is not None]
    return int(round(median(vals))) if vals else None


def _median_point(values: Iterable[Optional[float]]) -> Optional[float]:
    vals = [v for v in values if v is not None]
    return round(median(vals) * 2) / 2 if vals else None  # nearest half point


# ----------------------------------------------------------- per-book line
def parse_book_line(row: dict) -> dict:
    book_id = row.get("book_id")
    return {
        "book_id": book_id,
        "book": book_name(book_id),
        "moneyline": {
            "away": _price(row.get("ml_away")),
            "home": _price(row.get("ml_home")),
            "draw": _price(row.get("draw")),
        },
        "spread": {
            "away": {"point": _num(row.get("spread_away")), "price": _price(row.get("spread_away_line"))},
            "home": {"point": _num(row.get("spread_home")), "price": _price(row.get("spread_home_line"))},
        },
        "total": {
            "point": _num(row.get("total")),
            "over": _price(row.get("over")),
            "under": _price(row.get("under")),
        },
        "team_totals": {
            "away": {"point": _num(row.get("away_total")), "over": _price(row.get("away_over")), "under": _price(row.get("away_under"))},
            "home": {"point": _num(row.get("home_total")), "over": _price(row.get("home_over")), "under": _price(row.get("home_under"))},
        },
        "updated_at": row.get("inserted"),
    }


def _public(row: Optional[dict]) -> Optional[dict]:
    if not row:
        return None
    keys = [
        "ml_home_public", "ml_away_public", "spread_home_public", "spread_away_public",
        "total_over_public", "total_under_public", "ml_home_money", "ml_away_money",
        "spread_home_money", "spread_away_money", "total_over_money", "total_under_money",
    ]
    if all(_num(row.get(k)) is None for k in keys):
        return None
    out = {
        "tickets_pct": {
            "moneyline": {"home": _num(row.get("ml_home_public")), "away": _num(row.get("ml_away_public"))},
            "spread": {"home": _num(row.get("spread_home_public")), "away": _num(row.get("spread_away_public"))},
            "total": {"over": _num(row.get("total_over_public")), "under": _num(row.get("total_under_public"))},
        },
        "money_pct": {
            "moneyline": {"home": _num(row.get("ml_home_money")), "away": _num(row.get("ml_away_money"))},
            "spread": {"home": _num(row.get("spread_home_money")), "away": _num(row.get("spread_away_money"))},
            "total": {"over": _num(row.get("total_over_money")), "under": _num(row.get("total_under_money"))},
        },
        "num_bets": _num(row.get("num_bets")),
    }
    return out


# ------------------------------------------------------------- best lines
def _best_price(lines: List[dict], getter) -> Optional[dict]:
    best = None
    for ln in lines:
        p = getter(ln)
        if p is not None and (best is None or p > best["price"]):
            best = {"price": p, "book": ln["book"], "book_id": ln["book_id"]}
    return best


def _best_point(lines: List[dict], point_get, price_get, prefer_high: bool) -> Optional[dict]:
    best = None
    for ln in lines:
        pt, pr = point_get(ln), price_get(ln)
        if pt is None or pr is None:
            continue
        key = (pt if prefer_high else -pt, pr)
        if best is None or key > best["_key"]:
            best = {"_key": key, "point": pt, "price": pr, "book": ln["book"], "book_id": ln["book_id"]}
    if best:
        best.pop("_key")
    return best


def best_lines(lines: List[dict]) -> dict:
    """Best available price per side across bettable books."""
    lines = [ln for ln in lines if ln["book_id"] not in config.NON_MARKET_BOOK_IDS]
    return {
        "moneyline": {
            "away": _best_price(lines, lambda l: l["moneyline"]["away"]),
            "home": _best_price(lines, lambda l: l["moneyline"]["home"]),
            "draw": _best_price(lines, lambda l: l["moneyline"]["draw"]),
        },
        "spread": {
            "away": _best_point(lines, lambda l: l["spread"]["away"]["point"], lambda l: l["spread"]["away"]["price"], True),
            "home": _best_point(lines, lambda l: l["spread"]["home"]["point"], lambda l: l["spread"]["home"]["price"], True),
        },
        "total": {
            "over": _best_point(lines, lambda l: l["total"]["point"], lambda l: l["total"]["over"], False),
            "under": _best_point(lines, lambda l: l["total"]["point"], lambda l: l["total"]["under"], True),
        },
    }


def consensus_line(lines: List[dict]) -> dict:
    """Median line across bettable books, plus no-vig win probabilities."""
    lines = [ln for ln in lines if ln["book_id"] not in config.NON_MARKET_BOOK_IDS]
    ml = {side: _median_price(l["moneyline"][side] for l in lines) for side in ("away", "home", "draw")}
    out = {
        "books_counted": len(lines),
        "moneyline": ml,
        "spread": {
            side: {
                "point": _median_point(l["spread"][side]["point"] for l in lines),
                "price": _median_price(l["spread"][side]["price"] for l in lines),
            }
            for side in ("away", "home")
        },
        "total": {
            "point": _median_point(l["total"]["point"] for l in lines),
            "over": _median_price(l["total"]["over"] for l in lines),
            "under": _median_price(l["total"]["under"] for l in lines),
        },
        "win_probability_no_vig": no_vig({side: implied_probability(ml[side]) for side in ("away", "home", "draw")}),
    }
    if ml["draw"] is None:
        out["moneyline"].pop("draw")
        out["win_probability_no_vig"].pop("draw", None)
    return out


# ------------------------------------------------------------------ games
def _team(team: Optional[dict], team_id: Any) -> dict:
    team = team or {}
    return {
        "id": team_id,
        "name": team.get("full_name"),
        "short_name": team.get("display_name") or team.get("short_name"),
        "abbr": team.get("abbr"),
        "record": _record(team.get("standings")),
    }


def _record(standings: Any) -> Optional[str]:
    if not isinstance(standings, dict):
        return None
    w, l = standings.get("win"), standings.get("loss")
    if w is None or l is None:
        return None
    rec = f"{int(w)}-{int(l)}"
    for extra in ("ties", "draw", "overtime_losses"):
        if standings.get(extra):
            rec += f"-{int(standings[extra])}"
    return rec


def parse_game(game: dict, league: str) -> dict:
    """One scoreboard game -> header + per-book lines (unsummarized)."""
    teams = {t.get("id"): t for t in game.get("teams") or []}
    away_id, home_id = game.get("away_team_id"), game.get("home_team_id")
    box = game.get("boxscore") or {}
    score = None
    if _num(box.get("total_away_points")) is not None or _num(box.get("total_home_points")) is not None:
        score = {"away": _num(box.get("total_away_points")), "home": _num(box.get("total_home_points"))}

    rows = [r for r in game.get("odds") or [] if r.get("type") in (None, "game")]
    consensus_row = next((r for r in rows if r.get("book_id") == config.CONSENSUS_BOOK_ID), None)
    if consensus_row is None or _public(consensus_row) is None:
        consensus_row = next((r for r in rows if _public(r) is not None), consensus_row)

    return {
        "game_id": game.get("id"),
        "league": league,
        "status": game.get("status"),
        "start_time": game.get("start_time"),
        "week": game.get("week"),
        "away": _team(teams.get(away_id), away_id),
        "home": _team(teams.get(home_id), home_id),
        "score": score,
        "broadcast": (game.get("broadcast") or {}).get("network"),
        "lines": [parse_book_line(r) for r in rows],
        "public": _public(consensus_row),
    }


def matchup_label(g: dict) -> str:
    return f"{g['away'].get('name') or g['away']['id']} @ {g['home'].get('name') or g['home']['id']}"


def summarize_game(
    g: dict,
    markets: Iterable[str] = MARKETS,
    books: Optional[Iterable[str]] = None,
    include_books: bool = True,
) -> dict:
    """Compact odds view: consensus + best line (+ optional per-book detail)."""
    markets = [m for m in markets if m in MARKETS] or list(MARKETS)
    lines = g["lines"]
    if books:
        wanted = {str(b).strip().lower() for b in books}
        lines = [l for l in lines if l["book"].lower() in wanted or str(l["book_id"]) in wanted]

    def pick(d: dict) -> dict:
        return {k: v for k, v in d.items() if k in markets}

    cons = consensus_line(lines)
    out = {
        "game_id": g["game_id"],
        "league": g["league"],
        "matchup": matchup_label(g),
        "status": g["status"],
        "start_time": g["start_time"],
        "away": g["away"],
        "home": g["home"],
        "score": g["score"],
        "consensus": {**pick(cons), "win_probability_no_vig": cons["win_probability_no_vig"], "books_counted": cons["books_counted"]},
        "best": pick(best_lines(lines)),
    }
    if include_books:
        out["books"] = [
            {"book": l["book"], "book_id": l["book_id"], **pick(l), "updated_at": l["updated_at"]}
            for l in lines
        ]
    return out


def game_header(g: dict) -> dict:
    return {
        "game_id": g["game_id"],
        "league": g["league"],
        "matchup": matchup_label(g),
        "status": g["status"],
        "start_time": g["start_time"],
        "away": g["away"],
        "home": g["home"],
        "score": g["score"],
        "broadcast": g["broadcast"],
        "books_with_odds": len([l for l in g["lines"] if l["book_id"] not in config.NON_MARKET_BOOK_IDS]),
    }
