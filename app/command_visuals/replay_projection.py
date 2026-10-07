from app.market_fabric.contracts import utc


def replay_frame(points, clock):
    # Both observation time and availability time matter; late corrections are not
    # exposed to an earlier replay frame merely because their bar timestamp is old.
    return [{**p, "source": "VELUM_REPLAY", "replay_time": utc(clock).isoformat(), "entry_authority": False}
            for p in points if p.get("available_at") and utc(p["timestamp"]) <= utc(clock)
            and utc(p["available_at"]) <= utc(clock)
            and utc(p.get("feature_as_of", p["timestamp"])) <= utc(clock)]
