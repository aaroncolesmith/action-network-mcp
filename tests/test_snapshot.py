import json
from datetime import datetime, timezone

from action_network import snapshot
from action_network.snapshot import build, fmt_point
from conftest import FakeResponse

NOW = datetime(2026, 10, 11, 15, 0, tzinfo=timezone.utc)  # 08:00 Pacific

SOCCER = {
    "games": [
        {"id": 900, "status": "scheduled", "start_time": "2026-10-12T04:30:00.000Z", "league_name": "epl",
         "away_team_id": 1, "home_team_id": 2,
         "teams": [{"id": 1, "full_name": "Arsenal"}, {"id": 2, "full_name": "Chelsea"}],
         "odds": [{"book_id": 68, "type": "game", "ml_away": 120, "ml_home": 210, "draw": 240,
                   "spread_away": -0.25, "spread_home": 0.25, "spread_away_line": -105, "spread_home_line": -115,
                   "total": 2.5, "over": -130, "under": 110}]},
        {"id": 901, "status": "scheduled", "start_time": "2026-10-11T23:00:00.000Z", "league_name": "mls",
         "away_team_id": 3, "home_team_id": 4,
         "teams": [{"id": 3, "full_name": "Portland Timbers"}, {"id": 4, "full_name": "Seattle Sounders FC"}],
         "odds": [{"book_id": 68, "type": "game", "ml_away": 250, "ml_home": -110, "draw": 270}]},
    ]
}


def _with_soccer(session):
    original = session.get

    def get(url, timeout=None):
        if "/scoreboard/soccer" in url:
            session.calls.append(url)
            return FakeResponse(SOCCER if "date=20261011" in url else {"games": []})
        return original(url, timeout)

    session.get = get


def test_soccer_competitions_are_filtered(svc, session):
    _with_soccer(session)
    assert [g["matchup"] for g in svc.get_games("epl", "2026-10-11")["games"]] == ["Arsenal @ Chelsea"]
    assert [g["game_id"] for g in svc.get_games("mls", "2026-10-11")["games"]] == [901]
    assert svc.get_games("soccer", "2026-10-11")["count"] == 2
    assert len([c for c in session.calls if "/scoreboard/soccer" in c]) == 1  # one fetch, shared by cache


def test_build_snapshot(svc, session, tmp_path):
    _with_soccer(session)
    index = build(str(tmp_path), ["NFL", "EPL", "MLS"], svc=svc, now=NOW, base_url="https://x/")

    nfl = json.loads((tmp_path / "odds/nfl/2026-10-11.json").read_text())
    assert nfl["league"] == "NFL" and nfl["sportsbook"] == "DraftKings"
    assert nfl["retrieved_at"] == "2026-10-11T08:00:00-07:00"
    assert nfl["game_count"] == 1                                   # the completed game is excluded
    assert nfl["games_excluded"]["started_or_not_scheduled"] == 1
    g = nfl["games"][0]
    assert g["game"] == "Kansas City Chiefs @ Las Vegas Raiders"
    assert g["start_time"] == "2026-10-11T13:25:00-07:00"
    picks = {o["pick"]: o["odds"] for o in g["options"]}
    assert picks == {
        "Kansas City Chiefs ML": -165, "Las Vegas Raiders ML": 140,
        "Kansas City Chiefs -3.5": -105, "Las Vegas Raiders +3.5": -115,
        "Over 44.5": -112, "Under 44.5": -108,
    }

    # 04:30Z Oct 12 is 21:30 Pacific Oct 11: bucketed by Pacific date
    epl = json.loads((tmp_path / "odds/epl/2026-10-11.json").read_text())
    picks = {o["pick"]: o["odds"] for o in epl["games"][0]["options"]}
    assert picks["Draw"] == 240 and picks["Arsenal -0.25"] == -105 and picks["Over 2.5"] == -130
    mls = json.loads((tmp_path / "odds/mls/2026-10-11.json").read_text())
    assert [o["pick"] for o in mls["games"][0]["options"]] == ["Portland Timbers ML", "Seattle Sounders FC ML", "Draw"]

    assert len(index["leagues"]["NFL"]["files"]) == 8                 # today + 7 days
    assert len(index["leagues"]["EPL"]["files"]) == snapshot.DEFAULT_HORIZON + 1
    assert index["leagues"]["NFL"]["files"][0]["url"] == "https://x/odds/nfl/2026-10-11.json"
    assert (tmp_path / "odds/nfl/2026-10-18.json").exists()
    assert "Odds snapshots" in (tmp_path / "index.html").read_text()


def test_build_records_errors(svc, session, tmp_path, monkeypatch):
    monkeypatch.setattr("action_network.client.time.sleep", lambda s: None)
    session.fail_with = 403
    index = build(str(tmp_path), ["NBA"], svc=svc, now=NOW)
    body = json.loads((tmp_path / "odds/nba/2026-10-11.json").read_text())
    assert "403" in body["error"] and body["games"] == []
    assert "error" in index["leagues"]["NBA"]


def test_fmt_point():
    assert [fmt_point(x) for x in (-1.5, 3.0, 0.0, -0.25, 0.75)] == ["-1.5", "+3.0", "+0.0", "-0.25", "+0.75"]
