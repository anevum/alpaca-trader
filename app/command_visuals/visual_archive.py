"""Bounded source evidence for history and point-in-time replay, never broker authority."""
import json
from datetime import timedelta

from app.adaptive_policy import fingerprint
from app.market_fabric.contracts import utc
from .series_buffers import validate_point


class VisualArchive:
    def __init__(self, db, *, capacity=12000):
        self.db, self.capacity = db, capacity
        db.execute("CREATE TABLE IF NOT EXISTS shadow_visual (id TEXT PRIMARY KEY, series TEXT NOT NULL, source_at TEXT NOT NULL, available_at TEXT NOT NULL, body TEXT NOT NULL)")
        db.execute("CREATE INDEX IF NOT EXISTS shadow_visual_range ON shadow_visual(series,source_at,available_at)")
        db.execute("CREATE TABLE IF NOT EXISTS shadow_visual_meta (id INTEGER PRIMARY KEY CHECK(id=1), pruned INTEGER NOT NULL)")
        db.execute("INSERT OR IGNORE INTO shadow_visual_meta VALUES (1,0)")

    def append(self, series, point, available_at):
        validate_point(point)
        available = utc(available_at)
        source = utc(point["timestamp"])
        if source > available or not series or len(series) > 100:
            raise ValueError("visual archive source/availability violation")
        payload = {**point, "available_at": available.isoformat()}
        # Repeated reception of an unchanged source point is not new evidence.
        identity = fingerprint({"series": series, "point": point})
        encoded = json.dumps(payload, allow_nan=False, sort_keys=True)
        if len(encoded.encode()) > 16384:
            raise ValueError("visual archive payload too large")
        with self.db:
            added = self.db.execute("INSERT OR IGNORE INTO shadow_visual VALUES (?,?,?,?,?)",
                (identity, series, source.isoformat(), available.isoformat(), encoded)).rowcount
            removed = self.db.execute("DELETE FROM shadow_visual WHERE id NOT IN (SELECT id FROM shadow_visual ORDER BY available_at DESC,id DESC LIMIT ?)", (self.capacity,)).rowcount
            self.db.execute("UPDATE shadow_visual_meta SET pruned=pruned+? WHERE id=1", (removed,))
        return bool(added)

    def history(self, series, start, end, *, clock, limit=2400):
        start, end, clock = utc(start), utc(end), utc(clock)
        if start >= end or end-start > timedelta(days=1) or end > clock or type(limit) is not int or not 1 <= limit <= 5000:
            raise ValueError("invalid bounded visual range")
        # Select the latest revision available at the replay clock BEFORE limiting
        # distinct source points. Limiting raw revisions can return an obsolete
        # candle and let repeated corrections consume the entire response budget.
        rows = self.db.execute("""SELECT source_at,available_at,body FROM (
            SELECT source_at,available_at,body,
                ROW_NUMBER() OVER (PARTITION BY COALESCE(json_extract(body,'$.event_id'),source_at)
                    ORDER BY available_at DESC,rowid DESC) AS revision
            FROM shadow_visual WHERE series=? AND source_at>=? AND source_at<=? AND available_at<=?
        ) WHERE revision=1 ORDER BY source_at LIMIT ?""",
            (series,start.isoformat(),end.isoformat(),clock.isoformat(),limit+1)).fetchall()
        pruned = self.db.execute("SELECT pruned FROM shadow_visual_meta WHERE id=1").fetchone()[0]
        points = [json.loads(body) for _, _, body in rows[:limit]]
        return {"series_id":series, "points":points, "range_start":start.isoformat(), "range_end":end.isoformat(),
            "replay_clock":clock.isoformat(), "truncated":len(rows)>limit, "pruned_records":pruned,
            "coverage_state":"BOUNDED_OBSERVATIONS_ONLY", "entry_authority":False,
            "artifact_fingerprint":fingerprint({"series":series,"clock":clock.isoformat(),"points":points}),
            "methodology_version":"source-availability-replay-v2", "provenance":"DERIVED", "source":"RHEN/shadow_visual"}
