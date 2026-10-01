from __future__ import annotations

import json
from typing import Any

import httpx
import jwt
from jwt import InvalidTokenError


class AccessConfigurationError(RuntimeError):
    pass


class AccessAuthorizationError(PermissionError):
    pass


def normalize_team_domain(value: str) -> str:
    domain = str(value or "").strip().rstrip("/")
    if not domain:
        raise AccessConfigurationError("cloudflare_access_team_domain_missing")
    if not domain.startswith("https://"):
        raise AccessConfigurationError("cloudflare_access_team_domain_invalid")
    return domain


async def verify_access_assertion(
    assertion: str | None,
    *,
    team_domain: str,
    audience: str,
    allowed_emails: str = "",
) -> dict[str, Any]:
    token = str(assertion or "").strip()
    if not token:
        raise AccessAuthorizationError("cloudflare_access_assertion_missing")
    issuer = normalize_team_domain(team_domain)
    aud = str(audience or "").strip()
    if not aud:
        raise AccessConfigurationError("cloudflare_access_audience_missing")

    try:
        header = jwt.get_unverified_header(token)
    except InvalidTokenError as exc:
        raise AccessAuthorizationError("cloudflare_access_assertion_invalid") from exc

    kid = str(header.get("kid") or "")
    if not kid:
        raise AccessAuthorizationError("cloudflare_access_kid_missing")

    async with httpx.AsyncClient(timeout=5.0) as http:
        response = await http.get(
            issuer + "/cdn-cgi/access/certs",
            headers={"accept": "application/json"},
        )
    if not response.is_success:
        raise AccessConfigurationError("cloudflare_access_jwks_unavailable")

    jwks = response.json()
    keys = jwks.get("keys") if isinstance(jwks, dict) else None
    if not isinstance(keys, list):
        raise AccessConfigurationError("cloudflare_access_jwks_invalid")
    key = next(
        (
            item
            for item in keys
            if isinstance(item, dict) and str(item.get("kid") or "") == kid
        ),
        None,
    )
    if key is None:
        raise AccessAuthorizationError("cloudflare_access_signing_key_unknown")

    try:
        public_key = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(key))
        payload = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            audience=aud,
            issuer=issuer,
            options={"require": ["exp", "iat", "iss", "aud"]},
        )
    except InvalidTokenError as exc:
        raise AccessAuthorizationError("cloudflare_access_assertion_invalid") from exc

    email = str(payload.get("email") or "").strip().lower()
    if not email:
        raise AccessAuthorizationError("cloudflare_access_email_missing")

    allowed = {
        item.strip().lower()
        for item in str(allowed_emails or "").split(",")
        if item.strip()
    }
    if allowed and email not in allowed:
        raise AccessAuthorizationError("cloudflare_access_identity_not_allowed")

    return {
        "email": email,
        "sub": str(payload.get("sub") or ""),
        "aud": payload.get("aud"),
        "iss": payload.get("iss"),
    }
