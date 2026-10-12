"""Inspect raw Action Network responses (developer tool).

    python -m action_network.probe "scoreboard/nfl?week=7" "scoreboard/nhl?date=20261013"

For each path prints game count, start-date range, top-level game keys, and which
book ids / odds types appear. With --annotate, prints GitHub Actions ::notice lines.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter

from . import config
from .client import ActionNetworkClient, ActionNetworkError


def summarize(path: str, client: ActionNetworkClient) -> dict:
    p, _, q = path.partition("?")
    params = dict(kv.split("=", 1) for kv in q.split("&") if "=" in kv)
    if p.startswith("scoreboard/") and "bookIds" not in params:
        params["bookIds"] = ",".join(map(str, config.DEFAULT_BOOK_IDS))
    try:
        payload, _, _ = client.get_json(p, params)
    except ActionNetworkError as exc:
        return {"path": path, "error": str(exc)}
    if not isinstance(payload, dict):
        return {"path": path, "type": type(payload).__name__}
    games = payload.get("games") or []
    starts = sorted(g.get("start_time", "")[:10] for g in games if g.get("start_time"))
    books = Counter()
    for g in games:
        for o in g.get("odds") or []:
            books[f"{o.get('book_id')}/{o.get('type')}"] += 1
    sample = games[0] if games else {}
    return {
        "path": path,
        "top_keys": sorted(payload.keys())[:20],
        "games": len(games),
        "dates": f"{starts[0]}..{starts[-1]}" if starts else None,
        "game_keys": sorted(k for k in sample.keys() if k not in ("odds", "teams"))[:40],
        "week": {k: sample.get(k) for k in ("week", "season", "season_type", "type") if k in sample},
        "book_rows": dict(books.most_common(25)),
        "games_with_odds": sum(1 for g in games if g.get("odds")),
        "sample_rows": [
            {k: o.get(k) for k in ("book_id", "type", "ml_away", "ml_home", "spread_away", "spread_away_line",
                                   "total", "over", "inserted") if k in o}
            for o in (sample.get("odds") or [])
        ][:12],
        "sample_status": {k: sample.get(k) for k in ("status", "real_status", "start_time")},
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--annotate", action="store_true")
    a = ap.parse_args(argv)
    client = ActionNetworkClient(min_interval=1.0)
    for path in a.paths:
        out = json.dumps(summarize(path, client))
        print(f"::notice title=Probe {path}::{out[:3500]}" if a.annotate else out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
