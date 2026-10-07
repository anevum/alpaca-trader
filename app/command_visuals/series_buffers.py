from collections import deque

from app.market_fabric.contracts import utc

PROVENANCE = {"OBSERVED", "DERIVED", "FORECAST", "OPERATIONAL"}


def validate_point(point):
    if point.get("provenance") not in PROVENANCE or not point.get("source") or not point.get("quality_state"):
        raise ValueError("visual provenance/source/quality required")
    utc(point["timestamp"])
    if point["provenance"] in {"DERIVED", "FORECAST"} and not point.get("methodology_version"):
        raise ValueError("methodology required")


class SeriesBuffer:
    def __init__(self, capacity=2400):
        if not 1 <= capacity <= 5000:
            raise ValueError("invalid visual capacity")
        self.points = deque(maxlen=capacity)

    def apply(self, point):
        validate_point(point)
        timestamp = utc(point["timestamp"])
        if self.points and timestamp < utc(self.points[-1]["timestamp"]):
            return None
        if self.points and timestamp == utc(self.points[-1]["timestamp"]):
            if self.points[-1] == point:
                return None
            self.points[-1] = dict(point)
            return "series_patch_last"
        self.points.append(dict(point))
        return "series_append"
