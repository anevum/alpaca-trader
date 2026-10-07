from __future__ import annotations

import json
from time import monotonic
from typing import Any

import httpx
import jwt
from jwt import InvalidTokenError


class CommandAuthError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


_JWKS_TTL_SECONDS = 300.0
_JWKS_STALE_IF_ERROR_SECONDS = 3600.0
_JWKS_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}


def _matching_jwk(keys: list[dict[str, Any]], kid: str) -> dict[str, Any] | None:
    return next((item for item in keys if str(item.get("kid") or "") == kid), None)


async def _fetch_cloudflare_jwks(issuer: str) -> list[dict[str, Any]]:
    try:
        async with httpx.AsyncClient(timeout=5.0) as http:
            response = await http.get(
                issuer + "/cdn-cgi/access/certs",
                headers={"accept": "application/json"},
            )
    except httpx.HTTPError as exc:
        raise CommandAuthError(503, "Cloudflare Access signing keys are unavailable") from exc

    if not response.is_success:
        raise CommandAuthError(503, "Cloudflare Access signing keys are unavailable")
    try:
        body = response.json()
    except ValueError as exc:
        raise CommandAuthError(503, "Cloudflare Access signing keys are invalid") from exc
    keys = body.get("keys") if isinstance(body, dict) else None
    if not isinstance(keys, list) or not all(isinstance(item, dict) for item in keys):
        raise CommandAuthError(503, "Cloudflare Access signing keys are invalid")
    return keys


async def _resolve_cloudflare_jwk(issuer: str, kid: str) -> dict[str, Any]:
    now = monotonic()
    cached = _JWKS_CACHE.get(issuer)
    cached_key = None
    cached_age = None
    if cached is not None:
        cached_at, cached_keys = cached
        cached_age = max(0.0, now - cached_at)
        cached_key = _matching_jwk(cached_keys, kid)
        if cached_key is not None and cached_age <= _JWKS_TTL_SECONDS:
            return cached_key

    try:
        keys = await _fetch_cloudflare_jwks(issuer)
    except CommandAuthError:
        if (
            cached_key is not None
            and cached_age is not None
            and cached_age <= _JWKS_STALE_IF_ERROR_SECONDS
        ):
            return cached_key
        raise

    _JWKS_CACHE[issuer] = (monotonic(), keys)
    jwk = _matching_jwk(keys, kid)
    if jwk is None:
        raise CommandAuthError(401, "Cloudflare Access signing key is unknown")
    return jwk


def normalized_team_domain(value: str) -> str:
    domain = str(value or "").strip().rstrip("/")
    if not domain or not domain.startswith("https://"):
        raise CommandAuthError(503, "Cloudflare Access team domain is not configured")
    return domain


async def verify_cloudflare_access(
    token: str,
    *,
    team_domain: str,
    audience: str,
    allowed_emails: str = "",
) -> dict[str, Any]:
    assertion = str(token or "").strip()
    if not assertion:
        raise CommandAuthError(401, "Cloudflare Access authorization required")

    issuer = normalized_team_domain(team_domain)
    aud = str(audience or "").strip()
    if not aud:
        raise CommandAuthError(503, "Cloudflare Access audience is not configured")

    try:
        header = jwt.get_unverified_header(assertion)
    except InvalidTokenError as exc:
        raise CommandAuthError(401, "Cloudflare Access token is invalid") from exc

    kid = str(header.get("kid") or "")
    if not kid:
        raise CommandAuthError(401, "Cloudflare Access token is invalid")

    jwk = await _resolve_cloudflare_jwk(issuer, kid)

    try:
        public_key = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(jwk))
        payload = jwt.decode(
            assertion,
            public_key,
            algorithms=["RS256"],
            issuer=issuer,
            audience=aud,
            options={"require": ["exp", "iat", "iss", "aud"]},
        )
    except InvalidTokenError as exc:
        raise CommandAuthError(401, "Cloudflare Access token is invalid") from exc

    email = str(payload.get("email") or "").strip().lower()
    if not email:
        raise CommandAuthError(401, "Cloudflare Access identity is missing email")

    allowed = {
        item.strip().lower()
        for item in str(allowed_emails or "").split(",")
        if item.strip()
    }
    if allowed and email not in allowed:
        raise CommandAuthError(403, "Cloudflare Access identity is not authorized")

    return {
        "id": str(payload.get("sub") or ""),
        "email": email,
        "auth_source": "cloudflare_access",
        "app_metadata": {"command_admin": True, "role": "command_admin"},
    }


async def authenticate_command_admin(
    authorization: str | None,
    *,
    team_domain: str,
    audience: str,
    allowed_emails: str,
) -> dict[str, Any]:
    if not authorization or not authorization.startswith("Bearer "):
        raise CommandAuthError(401, "Private authorization required")

    token = authorization[7:].strip()
    if not token:
        raise CommandAuthError(401, "Private authorization required")

    return await verify_cloudflare_access(
        token,
        team_domain=team_domain,
        audience=audience,
        allowed_emails=allowed_emails,
    )
