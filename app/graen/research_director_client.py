from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

import httpx


DIRECTOR_SCHEMA_VERSION = "graen.research-director.v1"


class ResearchDirectorClientError(RuntimeError):
    pass


class ResearchDirectorClient:
    def __init__(
        self,
        url: str | None = None,
        token: str | None = None,
        *,
        timeout_seconds: float = 210.0,
    ):
        base = (
            url
            if url is not None
            else os.environ.get("RAILWAY_SERVICE_RHEN_RESEARCH_AGENT_URL", "")
        )
        self.base_url = str(base or "").strip().rstrip("/")
        self.token = str(
            token
            if token is not None
            else os.environ.get("GRAEN_RESEARCH_DIRECTOR_TOKEN", "")
        ).strip()
        self.timeout_seconds = timeout_seconds

    @property
    def configured(self) -> bool:
        return bool(self.base_url and len(self.token) >= 32)

    async def research(
        self,
        *,
        objective: str,
        canonical_evidence: Mapping[str, Any],
    ) -> dict[str, Any]:
        if not self.configured:
            raise ResearchDirectorClientError(
                "GRAEN research director connection is not configured"
            )
        url = self.base_url + "/v1/director/research"
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(
                    url,
                    headers={
                        "x-graen-research-director-token": self.token,
                        "content-type": "application/json",
                    },
                    json={
                        "objective": objective,
                        "canonical_evidence": dict(canonical_evidence),
                    },
                )
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:
            raise ResearchDirectorClientError(
                f"GRAEN research director request failed: {type(exc).__name__}"
            ) from exc
        if not isinstance(payload, Mapping) or payload.get("ok") is not True:
            raise ResearchDirectorClientError(
                "GRAEN research director returned an invalid response"
            )
        decision = payload.get("decision")
        if not isinstance(decision, Mapping):
            raise ResearchDirectorClientError("research director decision is missing")
        if decision.get("schema_version") != DIRECTOR_SCHEMA_VERSION:
            raise ResearchDirectorClientError("research director schema mismatch")
        if decision.get("research_only") is not True:
            raise ResearchDirectorClientError("research director escaped research-only scope")
        if decision.get("execution_authority") is not False:
            raise ResearchDirectorClientError("research director claimed execution authority")
        if decision.get("live_execution_authorized") is not False:
            raise ResearchDirectorClientError("research director claimed live authorization")
        return dict(payload)
