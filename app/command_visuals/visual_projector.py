from datetime import datetime, timezone

from .publisher import LivePublisher
from .series_buffers import SeriesBuffer


class VisualProjector:
    def __init__(self, store, *, flush_ms=125):
        self.store = store
        self.scanner = {s: store.snapshot(s, datetime.now(timezone.utc)) for s in store.symbols}
        self.series = {}
        self.executions = SeriesBuffer()
        self.system = {"entry_authority": False, "mode": "SHADOW", "quality_state": "WARMING",
                       "provenance": "OPERATIONAL", "source": "RHEN/market_fabric"}
        self.publisher = LivePublisher(self.snapshot, flush_ms=flush_ms)

    def snapshot(self):
        return {"visual_schema": "command-visual.v1", "scanner": dict(self.scanner),
                "series": {k: list(v.points) for k,v in self.series.items()},
                "execution_events": list(self.executions.points), "system": dict(self.system)}

    def market(self, event, now):
        row = self.store.snapshot(event.symbol, now)
        self.scanner[event.symbol] = row
        self.publisher.stage("scanner:"+event.symbol, "scanner_patch", row)
        if event.kind == "quote" and row["mid"] is not None:
            self.point("mid:"+event.symbol, {"timestamp": event.source_at.isoformat(), "value": row["mid"],
                       "symbol": event.symbol, "provenance": "DERIVED", "source": f"ALPACA/{event.feed}",
                       "methodology_version": "quote-mid-v1", "quality_state": row["quality_state"],
                       "data_character": "INDICATIVE" if event.feed == "overnight" else "OBSERVED_QUOTE_MID",
                       "session": event.session})
        elif event.kind in {"bar", "bar_revision"}:
            key = "candles:"+event.symbol
            series = self.series.setdefault(key, SeriesBuffer())
            point = self.store._bar(event)
            if event.kind == "bar_revision" and series.points and point["timestamp"] != series.points[-1]["timestamp"]:
                # Rare historical correction uses reset, never patches a different candle.
                for i, old in enumerate(series.points):
                    if old["timestamp"] == point["timestamp"]:
                        series.points[i] = point
                        self.publisher.stage(key, "series_reset", {"series_id": key, "points": list(series.points)})
                        break
            else:
                self.point(key, point)

    def point(self, key, point):
        series = self.series.setdefault(key, SeriesBuffer())
        kind = series.apply(point)
        if kind:
            # Distinct timestamps are retained through a flush; only repeated same-point patches coalesce.
            self.publisher.stage(f"{key}:{point['timestamp']}", kind, {"series_id": key, "point": point})

    def critical(self, marker):
        if not marker:
            return
        # Execution events are keyed by event_id, not timestamp (multiple fills can share a time).
        if any(p["event_id"] == marker["event_id"] for p in self.executions.points):
            return
        self.executions.points.append(dict(marker))
        self.publisher.send("execution_event", marker)

    def system_patch(self, patch):
        self.system.update(patch)
        self.publisher.stage("system", "system_patch", dict(self.system))
