from __future__ import annotations

import json
from typing import Any

import httpx
import jwt
from jwt import InvalidTokenError


class CommandAuthError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


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

    async with httpx.AsyncClient(timeout=5.0) as http:
        response = await http.get(
            issuer + "/cdn-cgi/access/certs",
            headers={"accept": "application/json"},
        )
    if not response.is_success:
        raise CommandAuthError(503, "Cloudflare Access signing keys are unavailable")

    body = response.json()
    keys = body.get("keys") if isinstance(body, dict) else None
    if not isinstance(keys, list):
        raise CommandAuthError(503, "Cloudflare Access signing keys are invalid")
    jwk = next(
        (
            item
            for item in keys
            if isinstance(item, dict) and str(item.get("kid") or "") == kid
        ),
        None,
    )
    if jwk is None:
        raise CommandAuthError(401, "Cloudflare Access signing key is unknown")

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


async def verify_supabase_command_admin(
    token: str,
    *,
    supabase_url: str,
    publishable_key: str,
    founder_email: str,
) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=8.0) as http:
        response = await http.get(
            f"{supabase_url}/auth/v1/user",
            headers={
                "apikey": publishable_key,
                "Authorization": f"Bearer {token}",
                "Cache-Control": "no-store",
            },
        )
    payload = response.json() if response.content else {}
    if not response.is_success:
        raise CommandAuthError(401, "RHENLINK session is not valid")

    metadata = payload.get("app_metadata") or {}
    role = str(metadata.get("role") or "").strip().lower()
    email = str(payload.get("email") or "").strip().lower()
    confirmed = bool(payload.get("email_confirmed_at") or payload.get("confirmed_at"))
    authorized = (
        (email == str(founder_email).strip().lower() and confirmed)
        or metadata.get("command_admin") is True
        or metadata.get("wiki_admin") is True
        or role in {"owner", "founder", "admin", "command_admin", "wiki_admin"}
    )
    if not authorized:
        raise CommandAuthError(403, "COMMAND administrator authorization required")
    return payload


async def authenticate_command_admin(
    authorization: str | None,
    *,
    mode: str,
    team_domain: str,
    audience: str,
    allowed_emails: str,
    supabase_url: str,
    publishable_key: str,
    founder_email: str,
) -> dict[str, Any]:
    if not authorization or not authorization.startswith("Bearer "):
        raise CommandAuthError(401, "Private authorization required")

    token = authorization[7:].strip()
    if not token:
        raise CommandAuthError(401, "Private authorization required")

    normalized_mode = str(mode or "supabase").strip().lower()
    if normalized_mode not in {"supabase", "cloudflare_access", "dual"}:
        raise CommandAuthError(503, "COMMAND_AUTH_MODE is invalid")

    if normalized_mode in {"cloudflare_access", "dual"}:
        try:
            return await verify_cloudflare_access(
                token,
                team_domain=team_domain,
                audience=audience,
                allowed_emails=allowed_emails,
            )
        except CommandAuthError:
            if normalized_mode == "cloudflare_access":
                raise

    return await verify_supabase_command_admin(
        token,
        supabase_url=supabase_url,
        publishable_key=publishable_key,
        founder_email=founder_email,
    )
