from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any


ALLOWED_STATUSES = {"RESEARCH_ONLY", "SHADOW_APPROVED"}


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def artifact_checksum(payload: dict[str, Any]) -> str:
    unsigned = dict(payload)
    unsigned.pop("checksum", None)
    return hashlib.sha256(canonical_json(unsigned).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class LogisticArtifact:
    model_key: str
    target: str
    feature_names: tuple[str, ...]
    means: tuple[float, ...]
    scales: tuple[float, ...]
    weights: tuple[float, ...]
    intercept: float
    trained_through: str
    status: str
    checksum: str

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "LogisticArtifact":
        expected = artifact_checksum(payload)
        actual = str(payload.get("checksum") or "")
        if len(actual) != 64 or actual != expected:
            raise ValueError("model artifact checksum mismatch")
        status = str(payload.get("status") or "")
        if status not in ALLOWED_STATUSES:
            raise ValueError("model artifact status is not permitted in shadow service")
        names = tuple(str(item) for item in payload.get("feature_names") or ())
        means = tuple(float(item) for item in payload.get("means") or ())
        scales = tuple(float(item) for item in payload.get("scales") or ())
        weights = tuple(float(item) for item in payload.get("weights") or ())
        if not names or not (len(names) == len(means) == len(scales) == len(weights)):
            raise ValueError("model artifact feature vectors are inconsistent")
        if any(value <= 0 for value in scales):
            raise ValueError("model artifact scale must be positive")
        return cls(
            model_key=str(payload.get("model_key") or ""),
            target=str(payload.get("target") or ""),
            feature_names=names,
            means=means,
            scales=scales,
            weights=weights,
            intercept=float(payload.get("intercept") or 0.0),
            trained_through=str(payload.get("trained_through") or ""),
            status=status,
            checksum=actual,
        )

    @classmethod
    def from_json(cls, raw: str) -> "LogisticArtifact":
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("model artifact must be a JSON object")
        return cls.from_dict(payload)

    def probability(self, features: dict[str, float]) -> float | None:
        if any(name not in features for name in self.feature_names):
            return None
        z = self.intercept
        for name, mean, scale, weight in zip(
            self.feature_names,
            self.means,
            self.scales,
            self.weights,
        ):
            z += ((float(features[name]) - mean) / scale) * weight
        if z >= 0:
            return 1.0 / (1.0 + math.exp(-z))
        exp_z = math.exp(z)
        return exp_z / (1.0 + exp_z)
