"""Publish static odds snapshots that chat agents (ChatGPT, Grok, Claude) can open by URL.

    python -m action_network.snapshot --out site

Writes, for each league and each Pacific-time date in the horizon:

    site/odds/<league>/<YYYY-MM-DD>.json   one sportsbook's lines, with ready-made pick strings
    site/odds/index.json                   what was published, when, and any errors
    site/index.html                        human-readable directory

Run on a schedule (see .github/workflows/publish_odds.yaml) and deploy to GitHub Pages.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import date, datetime, timedelta, timezone
from html import escape
from typing import Dict, List, Optional

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    from backports.zoneinfo import ZoneInfo  # type: ignore

from . import config
from .client import ActionNetworkError
from .service import OddsService

log = logging.getLogger("action_network.snapshot")

PACIFIC = ZoneInfo("America/Los_Angeles")
EASTERN = ZoneInfo(config.SITE_TIMEZONE)

# Label used in the parlay prompt -> internal league key.
LEAGUES: Dict[str, str] = {
    "MLB": "mlb", "NFL": "nfl", "NCAAF": "ncaaf", "NBA": "nba", "WNBA": "wnba",
    "NCAAB": "ncaab", "NHL": "nhl", "EPL": "epl", "LALIGA": "laliga", "MLS": "mls",
    "BUNDESLIGA": "bundesliga", "SERIEA": "seriea", "CHAMPS": "champs",
}
SOCCER = {"epl", "laliga", "mls", "bundesliga", "seriea", "champs"}
# Days ahead (Pacific) to publish. Football is weekly, so look further out.
HORIZON_DAYS = {"nfl": 7, "ncaaf": 7}
DEFAULT_HORIZON = int(os.environ.get("AN_SNAPSHOT_DAYS", "4"))

BOOK_ID = int(os.environ.get("AN_SNAPSHOT_BOOK_ID", "68"))
BOOK_LABEL = os.environ.get("AN_SNAPSHOT_BOOK_NAME", config.BOOK_NAMES.get(BOOK_ID, f"book_{BOOK_ID}"))
REFRESH_NOTE = os.environ.get("AN_SNAPSHOT_REFRESH_NOTE", "about every 30 minutes")


# ------------------------------------------------------------------ formatting
def fmt_point(p: float) -> str:
    """+3.5 / -1.5 / +3.0; keeps two decimals only for quarter lines (soccer -0.25)."""
    return f"{p:+.1f}" if round(p * 10) == p * 10 else f"{p:+.2f}"


def fmt_total(p: float) -> str:
    return f"{p:.1f}" if round(p * 10) == p * 10 else f"{p:.2f}"


def to_pacific(iso: Optional[str]) -> Optional[str]:
    if not iso:
        return None
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return dt.astimezone(PACIFIC).isoformat(timespec="seconds")


def pick_options(game: dict, line: dict, soccer: bool) -> List[dict]:
    """Every pick an agent may choose for this game, in the exact format the parlay software expects."""
    away, home = game["away"]["name"], game["home"]["name"]
    out: List[dict] = []
    ml = line["moneyline"]
    for team, side in ((away, "away"), (home, "home")):
        if ml[side] is not None:
            out.append({"market": "moneyline", "pick": f"{team} ML", "odds": ml[side]})
    if soccer and ml["draw"] is not None:
        out.append({"market": "moneyline", "pick": "Draw", "odds": ml["draw"]})
    for team, side in ((away, "away"), (home, "home")):
        s = line["spread"][side]
        if s["point"] is not None and s["price"] is not None:
            out.append({"market": "spread", "pick": f"{team} {fmt_point(s['point'])}", "odds": s["price"]})
    t = line["total"]
    if t["point"] is not None:
        if t["over"] is not None:
            out.append({"market": "total", "pick": f"Over {fmt_total(t['point'])}", "odds": t["over"]})
        if t["under"] is not None:
            out.append({"market": "total", "pick": f"Under {fmt_total(t['point'])}", "odds": t["under"]})
    return out


# ------------------------------------------------------------------ building
def collect_league(svc: OddsService, key: str, start_pt: date, horizon: int) -> Dict[str, List[dict]]:
    """Fetch every Eastern date that can hold a game on the Pacific dates we publish."""
    by_date: Dict[str, List[dict]] = {}
    seen = set()
    last_pt = start_pt + timedelta(days=horizon)
    raw: Dict[str, str] = {}
    for i in range(horizon + 2):  # +1: late Pacific games fall on the next Eastern date
        day = start_pt + timedelta(days=i)
        games, _, _ = svc._scoreboard_games(key, day)
        starts = sorted(g["start_time"][:10] for g in games if g["start_time"])
        raw[day.isoformat()] = f"{len(games)}" + (f" [{starts[0]}..{starts[-1]}]" if starts else "")
        for g in games:
            if g["game_id"] in seen or not g["start_time"]:
                continue
            seen.add(g["game_id"])
            pt_day = datetime.fromisoformat(to_pacific(g["start_time"])).date()
            if start_pt <= pt_day <= last_pt:
                by_date.setdefault(pt_day.isoformat(), []).append(g)
    log.info("%s raw games by fetch date: %s", key, raw)
    by_date["_raw"] = raw  # type: ignore[assignment]
    return by_date


def league_file(label: str, key: str, day: str, games: List[dict], retrieved: datetime,
                error: Optional[str] = None) -> dict:
    soccer = key in SOCCER
    published, skipped_started, skipped_no_book = [], 0, 0
    for g in sorted(games, key=lambda g: g["start_time"] or ""):
        start = datetime.fromisoformat(g["start_time"].replace("Z", "+00:00"))
        if g["status"] != "scheduled" or start <= retrieved:
            skipped_started += 1
            continue
        line = next((l for l in g["lines"] if l["book_id"] == BOOK_ID), None)
        options = pick_options(g, line, soccer) if line else []
        if not options:
            skipped_no_book += 1
            continue
        published.append({
            "game": f"{g['away']['name']} @ {g['home']['name']}",
            "away_team": g["away"]["name"],
            "home_team": g["home"]["name"],
            "start_time": to_pacific(g["start_time"]),
            "options": options,
            "line_updated_at": line.get("updated_at"),
        })
    body = {
        "league": label,
        "date": day,
        "timezone": "America/Los_Angeles",
        "sportsbook": BOOK_LABEL,
        "retrieved_at": retrieved.astimezone(PACIFIC).isoformat(timespec="seconds"),
        "refreshes": REFRESH_NOTE,
        "source": "Action Network odds feed (unofficial)",
        "how_to_use": (
            "Each game's 'options' lists every allowed pick with its exact 'pick' text and American 'odds'. "
            "Copy 'game', 'pick', 'odds' and 'start_time' exactly. Only games that had not started at "
            "retrieved_at are listed; still confirm start_time is in the future when you use it."
        ),
        "game_count": len(published),
        "games_excluded": {"started_or_not_scheduled": skipped_started, f"no_{BOOK_LABEL.lower().replace(' ', '_')}_line": skipped_no_book},
        "games": published,
    }
    if error:
        body["error"] = error
    return body


def write_json(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
        f.write("\n")


def build(out_dir: str, labels: Optional[List[str]] = None, svc: Optional[OddsService] = None,
          now: Optional[datetime] = None, base_url: str = "") -> dict:
    svc = svc or OddsService(enable_history=False)
    retrieved = now or datetime.now(timezone.utc)
    start_pt = retrieved.astimezone(PACIFIC).date()
    index = {"retrieved_at": retrieved.astimezone(PACIFIC).isoformat(timespec="seconds"),
             "sportsbook": BOOK_LABEL, "book_id": BOOK_ID, "leagues": {}}

    for label in labels or list(LEAGUES):
        key = LEAGUES[label]
        horizon = HORIZON_DAYS.get(key, DEFAULT_HORIZON)
        days = [(start_pt + timedelta(days=i)).isoformat() for i in range(horizon + 1)]
        error = None
        try:
            by_date = collect_league(svc, key, start_pt, horizon)
        except (ActionNetworkError, ValueError) as exc:
            log.error("%s failed: %s", label, exc)
            by_date, error = {}, f"Could not retrieve {label} odds at this time: {exc}"
        raw = by_date.pop("_raw", {})
        entries = []
        for day in days:
            body = league_file(label, key, day, by_date.get(day, []), retrieved, error)
            rel = f"odds/{label.lower()}/{day}.json"
            write_json(os.path.join(out_dir, rel), body)
            entries.append({"date": day, "games": body["game_count"], "excluded": body["games_excluded"],
                            "url": f"{base_url}{rel}"})
        index["leagues"][label] = {"files": entries, "raw_games_by_fetch_date": raw,
                                   **({"error": error} if error else {})}
        log.info("%s: %s", label, {e["date"]: e["games"] for e in entries})

    write_json(os.path.join(out_dir, "odds", "index.json"), index)
    with open(os.path.join(out_dir, "index.html"), "w") as f:
        f.write(render_index(index))
    open(os.path.join(out_dir, ".nojekyll"), "w").close()
    return index


def render_index(index: dict) -> str:
    rows = []
    for label, info in index["leagues"].items():
        links = " ".join(
            f'<a href="{escape(e["url"])}">{escape(e["date"][5:])}</a>&nbsp;<small>({e["games"]})</small>'
            for e in info["files"]
        )
        err = f' <em class="err">{escape(info["error"])}</em>' if info.get("error") else ""
        rows.append(f"<tr><th>{escape(label)}</th><td>{links}{err}</td></tr>")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Odds Snapshots</title>
<style>
:root{{--bg:#fff;--fg:#1a1a1a;--muted:#666;--line:#e5e5e5;--accent:#0b62c4;--err:#b3261e}}
@media (prefers-color-scheme:dark){{:root{{--bg:#141414;--fg:#eee;--muted:#9a9a9a;--line:#2a2a2a;--accent:#7ab7ff;--err:#ff8a80}}}}
body{{background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif;margin:0 auto;max-width:860px;padding:24px 16px}}
h1{{font-size:22px;margin:0 0 4px}} p{{color:var(--muted);margin:0 0 20px}}
table{{border-collapse:collapse;width:100%}} th,td{{border-top:1px solid var(--line);padding:8px 6px;text-align:left;vertical-align:top}}
th{{width:110px}} a{{color:var(--accent);margin-right:6px;white-space:nowrap}} small{{color:var(--muted)}} .err{{color:var(--err)}}
</style></head><body>
<h1>Odds snapshots</h1>
<p>{escape(index["sportsbook"])} lines, retrieved {escape(index["retrieved_at"])} (Pacific). Refreshes {escape(REFRESH_NOTE)}.
Files: <code>odds/&lt;league&gt;/&lt;YYYY-MM-DD&gt;.json</code> · <a href="odds/index.json">index.json</a></p>
<table>{''.join(rows)}</table>
</body></html>
"""


def show_books(svc: Optional[OddsService] = None) -> None:
    """Print Action Network's own book list for the ids we use (to verify BOOK_NAMES)."""
    svc = svc or OddsService(enable_history=False)
    try:
        payload, _, _ = svc.client.get_json("books")
    except ActionNetworkError as exc:
        print(f"books endpoint unavailable: {exc}")
        return
    books = payload.get("books", payload) if isinstance(payload, dict) else payload
    wanted = set(config.DEFAULT_BOOK_IDS) | {BOOK_ID}
    for b in books or []:
        if isinstance(b, dict) and (b.get("id") in wanted or "draftkings" in str(b.get("display_name", "")).lower()):
            print(json.dumps({k: b.get(k) for k in ("id", "display_name", "abbr", "state_code", "parent_name") if k in b}))


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    p = argparse.ArgumentParser(description="Publish static odds snapshots")
    p.add_argument("--out", default="site")
    p.add_argument("--leagues", help="Comma list of labels, e.g. NFL,EPL (default: all)")
    p.add_argument("--base-url", default=os.environ.get("AN_SNAPSHOT_BASE_URL", ""))
    p.add_argument("--show-books", action="store_true", help="Print Action Network's book ids/names and exit")
    a = p.parse_args(argv)
    if a.show_books:
        show_books()
        return 0
    labels = [x.strip().upper() for x in a.leagues.split(",")] if a.leagues else None
    index = build(a.out, labels, base_url=a.base_url)
    failed = [k for k, v in index["leagues"].items() if v.get("error")]
    # Fail the run only if everything failed (likely blocked or the API changed).
    return 1 if failed and len(failed) == len(index["leagues"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
