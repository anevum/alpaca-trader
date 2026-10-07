"""Bounded checkpoints; quotes are ephemeral, recovery never grants authority."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .contracts import normalize, utc


class StreamCheckpoint:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS checkpoint (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS summary (bucket TEXT PRIMARY KEY, body TEXT NOT NULL)")

    def save_summary(self, bucket, summary, *, retain=1440):
        with self.db:
            self.db.execute("INSERT INTO summary VALUES (?,?) ON CONFLICT(bucket) DO UPDATE SET body=excluded.body", (bucket, json.dumps(summary, allow_nan=False)))
            self.db.execute("DELETE FROM summary WHERE bucket NOT IN (SELECT bucket FROM summary ORDER BY bucket DESC LIMIT ?)", (retain,))

    def save(self, store, now):
        body = {"version": 1, "saved_at": utc(now).isoformat(), "context": store.context,
                "generation": store.generation,
                "bars": {s: list(row["bars"]) for s, row in store.rows.items()}}
        with self.db:
            self.db.execute("INSERT INTO checkpoint VALUES (1,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body", (json.dumps(body, allow_nan=False),))

    def restore(self, store, now):
        row = self.db.execute("SELECT body FROM checkpoint WHERE id=1").fetchone()
        if not row:
            return 0
        body = json.loads(row[0])
        if body.get("version") != 1 or tuple(body.get("context") or ()) != store.context or utc(body["saved_at"]) > utc(now):
            return 0
        return merge_bars(store, body.get("bars", {}), now)

    def close(self):
        self.db.close()


def merge_bars(store, bars, now, *, starts_at=None):
    count = 0
    for symbol in store.symbols:
        for bar in sorted(bars.get(symbol, []), key=lambda b: b.get("timestamp", b.get("t", "")))[-store.max_bars:]:
            timestamp = utc(bar.get("timestamp", bar.get("t")))
            # Bootstrap only completed same-session bars. No forward fill, no source substitution.
            if timestamp >= utc(now) or (utc(now)-timestamp).total_seconds() < 60:
                continue
            if starts_at and timestamp < utc(starts_at):
                continue
            if bar.get("feed", store.context[0]) != store.context[0]:
                continue
            raw = {"T": "b", "S": symbol, "t": timestamp.isoformat(),
                   **{short: bar.get(long, bar.get(short)) for short, long in
                      (("o", "open"), ("h", "high"), ("l", "low"), ("c", "close"), ("v", "volume"))}}
            event = normalize(raw, generation=store.generation, sequence=0, feed=store.context[0],
                              session=store.context[1].split("/")[-1], session_id=store.context[1], received_at=now)
            count += int(store.apply(event))
    return count
