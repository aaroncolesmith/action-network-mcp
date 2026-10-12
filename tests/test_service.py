import pytest

from action_network import client as client_mod
from action_network.client import ActionNetworkError, UnknownLeagueError
from action_network.odds import implied_probability
from action_network.service import NotFoundError, parse_date


def test_odds_by_team_summarizes_consensus_and_best(svc):
    out = svc.get_odds("nfl", team="chiefs")
    assert out["count"] == 1
    g = out["games"][0]
    assert g["matchup"] == "Kansas City Chiefs @ Las Vegas Raiders"
    assert g["away"]["record"] == "4-1"

    # consensus excludes Consensus(15)/Open(30) rows: books 68, 69, 75
    cons = g["consensus"]
    assert cons["books_counted"] == 3
    assert cons["moneyline"] == {"away": -160, "home": 135}
    assert cons["spread"]["away"]["point"] == -3.5
    assert cons["total"]["point"] == 44.5
    p = cons["win_probability_no_vig"]
    assert abs(p["away"] + p["home"] - 1) < 1e-3 and p["away"] > 0.58

    best = g["best"]
    assert best["moneyline"]["home"] == {"price": 140, "book": "DraftKings", "book_id": 68}
    assert best["moneyline"]["away"]["book"] == "FanDuel"           # -155 beats -160/-165
    assert best["spread"]["away"]["point"] == -3.0                  # Chiefs -3 at FanDuel beats -3.5
    assert best["spread"]["home"] == {"point": 3.5, "price": -110, "book": "BetMGM", "book_id": 75}
    assert best["total"]["over"]["point"] == 44.0                   # lowest total for over
    assert best["total"]["under"]["point"] == 45.0                  # highest total for under
    assert "books" not in g                                         # compact by default


def test_nicknames_abbr_and_misses(svc):
    assert svc.get_odds("nfl", team="KC")["games"][0]["game_id"] == 256913
    assert svc.get_odds("nfl", team="niners")["games"][0]["game_id"] == 256914
    with pytest.raises(NotFoundError):
        svc.get_odds("nfl", team="packers", days_ahead=1)


def test_filters_markets_books_and_ignores_non_game_rows(svc):
    g = svc.get_odds("nfl", game_id=256913, markets=["moneyline"], books=["DraftKings"], include_books=True)["games"][0]
    assert set(g["best"]) == {"moneyline"}
    assert [b["book"] for b in g["books"]] == ["DraftKings"]
    assert g["books"][0]["moneyline"]["away"] == -165             # not the first-half row


def test_slate_cache_and_as_of(svc, session):
    first = svc.get_games("nfl", "2026-10-11")
    assert first["count"] == 2 and first["cached"] is False and first["as_of"].endswith("+00:00")
    second = svc.get_games("nfl", "20261011")
    assert second["cached"] is True
    assert len([c for c in session.calls if "scoreboard" in c]) == 1
    assert first["games"][1]["score"] == {"away": 24.0, "home": 17.0}


def test_public_betting(svc):
    pub = svc.get_public_betting("nfl", team="raiders")["games"][0]["public"]
    assert pub["tickets_pct"]["spread"]["away"] == 69
    assert pub["money_pct"]["total"]["under"] == 60
    assert pub["num_bets"] == 15234


def test_line_movement_records_only_changes(svc, session):
    svc.get_games("nfl", "2026-10-11")
    svc.client.cache.clear()
    svc.get_games("nfl", "2026-10-11")                              # identical lines: no new rows
    session.scoreboard["games"][0]["odds"][2]["ml_home"] = 150       # DraftKings moves
    svc.client.cache.clear()
    svc.get_games("nfl", "2026-10-11")

    mv = svc.get_line_movement(256913, books=["DraftKings"])
    dk = mv["books"][0]
    assert dk["book"] == "DraftKings" and dk["num_changes"] == 1
    assert dk["open"]["ml_home"] == 140 and dk["current"]["ml_home"] == 150 and dk["delta"]["ml_home"] == 10
    full = svc.get_line_movement(256913)
    assert full["books"][0]["book"] == "Consensus"


def test_futures(svc):
    markets = svc.list_futures("nfl")["markets"]
    assert {m["market"] for m in markets} == {"nfl_futures_mvp", "nfl_futures_super_bowl"}
    out = svc.get_futures("nfl", "mvp")
    assert out["market"] == "nfl_futures_mvp"
    top = out["outcomes"][0]
    assert top["outcome"] == "Patrick Mahomes"
    assert top["best_price"] == 500 and top["best_book"] == "FanDuel"
    assert top["book_count"] == 2                                   # consensus row (9999) excluded


def test_errors(svc, session, monkeypatch):
    with pytest.raises(UnknownLeagueError):
        svc.get_odds("xfl")
    with pytest.raises(ValueError):
        parse_date("next week")
    monkeypatch.setattr(client_mod.time, "sleep", lambda s: None)
    session.fail_with = 503
    svc.client.cache.clear()
    with pytest.raises(ActionNetworkError) as exc:
        svc.get_games("nfl", "2026-10-12")
    assert exc.value.status_code == 503
    assert len([c for c in session.calls if "20261012" in c]) == 2  # 1 try + 1 retry


def test_implied_probability():
    assert implied_probability(-110) == pytest.approx(0.5238, abs=1e-4)
    assert implied_probability(150) == pytest.approx(0.4)
    assert implied_probability(None) is None
