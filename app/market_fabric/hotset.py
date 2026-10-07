"""Canonical discovery to bounded market-stream hotset selection.

The selector is shadow-only orchestration. It never evaluates trading signals,
changes broker authority or submits orders. Discovery rank comes from the
existing RHEN DynamicUniverse exposed by the champion health read.
"""
from __future__ import annotations

import json
from datetime import datetime

from .contracts import utc


class HotsetSelector:
    METHODOLOGY_VERSION = "canonical-discovery-hotset-v1"

    def __init__(self, db, *, capacity, pinned=(), min_dwell_seconds=300, max_changes=4):
        if not 1 <= int(capacity) <= 30:
            raise ValueError("invalid hotset capacity")
        if not 0 <= int(max_changes) <= int(capacity):
            raise ValueError("invalid hotset change limit")
        if int(min_dwell_seconds) < 0:
            raise ValueError("invalid hotset dwell")
        self.db = db
        self.capacity = int(capacity)
        self.pinned = self._clean(pinned)
        if len(self.pinned) > self.capacity:
            raise ValueError("pinned symbols exceed hotset capacity")
        self.min_dwell_seconds = int(min_dwell_seconds)
        self.max_changes = int(max_changes)
        self.last_rotation_at = None
        self.rotations = 0
        self._restored = ()
        db.execute("CREATE TABLE IF NOT EXISTS shadow_hotset_state (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL)")
        saved = db.execute("SELECT body FROM shadow_hotset_state WHERE id=1").fetchone()
        if saved:
            try:
                body = json.loads(saved[0])
                symbols = self._clean(body.get("symbols", ()))
                if len(symbols) == self.capacity and all(s in symbols for s in self.pinned):
                    self._restored = symbols
                    stamp = body.get("last_rotation_at")
                    self.last_rotation_at = utc(stamp) if stamp else None
                    self.rotations = max(0, int(body.get("rotations", 0)))
            except (ValueError, TypeError, KeyError, json.JSONDecodeError):
                self._restored = ()
                self.last_rotation_at = None
                self.rotations = 0

    @staticmethod
    def _clean(symbols):
        rows = []
        for value in symbols or ():
            symbol = str(value or "").strip().upper()
            if not symbol or "/" in symbol or " " in symbol:
                continue
            if not all(c.isalnum() or c in {".", "-"} for c in symbol):
                continue
            if symbol not in rows:
                rows.append(symbol)
        return tuple(rows)

    @property
    def restored_symbols(self):
        return self._restored

    def _persist(self, symbols, now):
        with self.db:
            self.db.execute(
                "INSERT INTO shadow_hotset_state VALUES (1,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body",
                (json.dumps({"symbols":list(symbols),"last_rotation_at":utc(now).isoformat(),
                             "rotations":self.rotations,"methodology_version":self.METHODOLOGY_VERSION},
                            allow_nan=False),)
            )

    def propose(self, ranked_symbols, current_symbols, now):
        now = utc(now)
        ranked = self._clean(ranked_symbols)
        current = self._clean(current_symbols)
        pool = self._clean((*self.pinned, *ranked))
        base = {
            "integrated": True,
            "methodology_version": self.METHODOLOGY_VERSION,
            "capacity": self.capacity,
            "discovery_count": len(ranked),
            "pinned_count": len(self.pinned),
            "rotation_count": self.rotations,
            "last_rotation_at": self.last_rotation_at.isoformat() if self.last_rotation_at else None,
            "entry_authority": False,
            "broker_write_authority": False,
        }
        if len(pool) < self.capacity:
            return current, {**base,"active":False,"quality_state":"UNAVAILABLE",
                "reason":"DISCOVERY_UNDERSIZED","changed":False,"added_count":0,"removed_count":0}

        desired = tuple(pool[:self.capacity])
        if len(current) != self.capacity:
            self.rotations += 1
            self.last_rotation_at = now
            self._restored = desired
            self._persist(desired, now)
            return desired, {**base,"active":True,"quality_state":"ROTATING",
                "reason":"INITIALIZE_EXACT_CAPACITY","changed":True,
                "added_count":len(set(desired)-set(current)),"removed_count":len(set(current)-set(desired)),
                "rotation_count":self.rotations,"last_rotation_at":now.isoformat()}

        if set(current) == set(desired):
            return current, {**base,"active":True,"quality_state":"LIVE",
                "reason":"TARGET_STABLE","changed":False,"added_count":0,"removed_count":0}

        if self.last_rotation_at is not None and (now-self.last_rotation_at).total_seconds() < self.min_dwell_seconds:
            return current, {**base,"active":True,"quality_state":"LIVE",
                "reason":"DWELL_HOLD","changed":False,"added_count":0,"removed_count":0}

        additions = [s for s in desired if s not in current]
        removals = [s for s in reversed(current) if s not in desired and s not in self.pinned]
        changes = min(self.max_changes, len(additions), len(removals))
        if changes <= 0:
            return current, {**base,"active":True,"quality_state":"DEGRADED",
                "reason":"PINNED_OR_CHANGE_LIMIT_BLOCKED","changed":False,"added_count":0,"removed_count":0}

        remove = set(removals[:changes])
        add = additions[:changes]
        next_symbols = [s for s in current if s not in remove]
        next_symbols.extend(s for s in add if s not in next_symbols)
        # Keep deterministic capacity and preserve pinned symbols.
        next_symbols = list(self._clean(next_symbols))
        for symbol in desired:
            if len(next_symbols) >= self.capacity:
                break
            if symbol not in next_symbols:
                next_symbols.append(symbol)
        next_symbols = tuple(next_symbols[:self.capacity])
        if len(next_symbols) != self.capacity or not all(s in next_symbols for s in self.pinned):
            return current, {**base,"active":False,"quality_state":"UNAVAILABLE",
                "reason":"ROTATION_INVARIANT_FAILED","changed":False,"added_count":0,"removed_count":0}

        self.rotations += 1
        self.last_rotation_at = now
        self._restored = next_symbols
        self._persist(next_symbols, now)
        return next_symbols, {**base,"active":True,"quality_state":"ROTATING",
            "reason":"CANONICAL_DISCOVERY_ROTATION","changed":True,
            "added_count":len(set(next_symbols)-set(current)),
            "removed_count":len(set(current)-set(next_symbols)),
            "rotation_count":self.rotations,"last_rotation_at":now.isoformat()}
