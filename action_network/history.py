"""Line-movement history.

Every fresh upstream scoreboard fetch is snapshotted into SQLite, but a row is
only written when a book's line actually changed. Movement is therefore known
from the moment this service (or its poller) started watching a game.
"""
from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import List, Optional

from . import config
from .odds import book_name, parse_game

FIELDS = [
    "ml_away", "ml_home", "draw",
    "spread_away", "spread_away_price", "spread_home", "spread_home_price",
    "total", "over", "under",
]

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS line_snapshots (
    game_id INTEGER NOT NULL,
    league TEXT NOT NULL,
    book_id INTEGER NOT NULL,
    observed_at TEXT NOT NULL,
    {", ".join(f"{f} REAL" for f in FIELDS)}
);
CREATE INDEX IF NOT EXISTS ix_snap_game ON line_snapshots (game_id, book_id, observed_at);
"""


def _flatten(line: dict) -> dict:
    return {
        "ml_away": line["moneyline"]["away"],
        "ml_home": line["moneyline"]["home"],
        "draw": line["moneyline"]["draw"],
        "spread_away": line["spread"]["away"]["point"],
        "spread_away_price": line["spread"]["away"]["price"],
        "spread_home": line["spread"]["home"]["point"],
        "spread_home_price": line["spread"]["home"]["price"],
        "total": line["total"]["point"],
        "over": line["total"]["over"],
        "under": line["total"]["under"],
    }


class HistoryStore:
    def __init__(self, path: str = config.HISTORY_DB_PATH) -> None:
        self.path = path
        self._lock = threading.Lock()
        if os.path.dirname(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    # ---------------------------------------------------------------- write
    def record_scoreboard(self, league: str, payload: dict, observed_at: Optional[datetime] = None) -> int:
        """Snapshot all lines in a scoreboard payload; returns rows written."""
        ts = (observed_at or datetime.now(timezone.utc)).isoformat()
        written = 0
        with self._lock:
            cur = self._conn.cursor()
            for game in (payload or {}).get("games") or []:
                g = parse_game(game, league)
                for line in g["lines"]:
                    if line["book_id"] is None:
                        continue
                    flat = _flatten(line)
                    if all(v is None for v in flat.values()):
                        continue
                    last = cur.execute(
                        f"SELECT {', '.join(FIELDS)} FROM line_snapshots WHERE game_id=? AND book_id=? "
                        "ORDER BY observed_at DESC LIMIT 1",
                        (g["game_id"], line["book_id"]),
                    ).fetchone()
                    if last and all(last[f] == flat[f] for f in FIELDS):
                        continue
                    cur.execute(
                        f"INSERT INTO line_snapshots (game_id, league, book_id, observed_at, {', '.join(FIELDS)}) "
                        f"VALUES (?, ?, ?, ?, {', '.join('?' for _ in FIELDS)})",
                        (g["game_id"], league, line["book_id"], ts, *[flat[f] for f in FIELDS]),
                    )
                    written += 1
            self._conn.commit()
        return written

    # ----------------------------------------------------------------- read
    def movement(self, game_id: int, book_ids: Optional[List[int]] = None, max_changes: int = 20) -> dict:
        with self._lock:
            rows = self._conn.execute(
                f"SELECT book_id, observed_at, {', '.join(FIELDS)} FROM line_snapshots "
                "WHERE game_id=? ORDER BY book_id, observed_at",
                (game_id,),
            ).fetchall()
        by_book: dict = {}
        for r in rows:
            if book_ids and r["book_id"] not in book_ids:
                continue
            by_book.setdefault(r["book_id"], []).append(dict(r))

        books = []
        for book_id, snaps in by_book.items():
            first, last = snaps[0], snaps[-1]
            books.append({
                "book": book_name(book_id),
                "book_id": book_id,
                "first_seen": first["observed_at"],
                "last_change": last["observed_at"],
                "open": {f: first[f] for f in FIELDS},
                "current": {f: last[f] for f in FIELDS},
                "delta": {
                    f: (round(last[f] - first[f], 2) if last[f] is not None and first[f] is not None else None)
                    for f in FIELDS
                },
                "num_changes": len(snaps) - 1,
                "changes": [
                    {"observed_at": s["observed_at"], **{f: s[f] for f in FIELDS}} for s in snaps[-max_changes:]
                ],
            })
        books.sort(key=lambda b: (b["book_id"] != config.CONSENSUS_BOOK_ID, b["book"]))
        return {
            "game_id": game_id,
            "tracked": bool(books),
            "note": "History starts when this service first saw the game; 'open' is the earliest snapshot, not necessarily the true opener.",
            "books": books,
        }
