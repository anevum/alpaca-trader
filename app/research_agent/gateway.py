from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx

from .evidence import CanonicalEvidenceReader, EvidenceReadError
from .models import CanonicalEvidence


class ResearchGatewayError(RuntimeError):
    pass


class ResearchGateway:
    """Minimal canonical evidence/audit gateway for the isolated agent service."""

    def __init__(self, url: str, token: str, *, timeout_seconds: float = 12.0):
        self.url = url.strip()
        self.token = token.strip()
        self.timeout_seconds = timeout_seconds

    @property
    def configured(self) -> bool:
        return bool(self.url and self.token)

    def _headers(self) -> dict[str, str]:
        return {
            "accept": "application/json",
            "content-type": "application/json",
            "x-anevum-ingest-token": self.token,
        }

    async def fetch_document(self) -> Mapping[str, Any]:
        if not self.configured:
            raise ResearchGatewayError("research gateway is not configured")
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.get(self.url, headers=self._headers())
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:
            raise ResearchGatewayError(
                f"canonical evidence read failed: {type(exc).__name__}"
            ) from exc
        if not isinstance(payload, Mapping) or payload.get("ok") is not True:
            raise ResearchGatewayError("canonical evidence gateway returned an invalid response")
        evidence = payload.get("evidence")
        if not isinstance(evidence, Mapping):
            raise ResearchGatewayError("canonical evidence document is missing")
        return evidence

    async def fetch_evidence(self) -> CanonicalEvidence:
        document = await self.fetch_document()
        try:
            return CanonicalEvidenceReader(document).read()
        except EvidenceReadError as exc:
            raise ResearchGatewayError(f"canonical evidence is invalid: {exc}") from exc

    async def persist_run(self, record: Mapping[str, Any]) -> Mapping[str, Any]:
        if not self.configured:
            raise ResearchGatewayError("research gateway is not configured")
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(
                    self.url,
                    headers=self._headers(),
                    json={"action": "record_run", "run": dict(record)},
                )
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:
            raise ResearchGatewayError(
                f"research audit write failed: {type(exc).__name__}"
            ) from exc
        if not isinstance(payload, Mapping) or payload.get("ok") is not True:
            raise ResearchGatewayError("research audit gateway returned an invalid response")
        return payload

    async def persist_run_with_search_ledger(
        self,
        record: Mapping[str, Any],
        ledger: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        if not self.configured:
            raise ResearchGatewayError("research gateway is not configured")
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(
                    self.url,
                    headers=self._headers(),
                    json={
                        "action": "record_run_and_search_ledger",
                        "run": dict(record),
                        "search_ledger": dict(ledger),
                    },
                )
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:
            raise ResearchGatewayError(
                f"research audit/search-ledger write failed: {type(exc).__name__}"
            ) from exc
        if not isinstance(payload, Mapping) or payload.get("ok") is not True:
            raise ResearchGatewayError(
                "research audit/search-ledger gateway returned an invalid response"
            )
        return payload
