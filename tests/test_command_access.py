import asyncio

import pytest

from app import command_access


def run(coro):
    return asyncio.run(coro)


def test_command_auth_requires_bearer_authorization():
    with pytest.raises(command_access.CommandAuthError) as exc:
        run(
            command_access.authenticate_command_admin(
                None,
                team_domain="https://anevum.cloudflareaccess.com",
                audience="aud",
                allowed_emails="",
            )
        )
    assert exc.value.status_code == 401


def test_cloudflare_team_domain_must_be_https():
    with pytest.raises(command_access.CommandAuthError) as exc:
        command_access.normalized_team_domain("anevum.cloudflareaccess.com")
    assert exc.value.status_code == 503


def test_command_auth_has_no_legacy_fallback(monkeypatch):
    async def deny_access(*args, **kwargs):
        raise command_access.CommandAuthError(401, "invalid access")

    monkeypatch.setattr(command_access, "verify_cloudflare_access", deny_access)

    with pytest.raises(command_access.CommandAuthError) as exc:
        run(
            command_access.authenticate_command_admin(
                "Bearer test",
                team_domain="https://anevum.cloudflareaccess.com",
                audience="aud",
                allowed_emails="",
            )
        )
    assert exc.value.status_code == 401


def test_empty_bearer_fails_closed():
    with pytest.raises(command_access.CommandAuthError) as exc:
        run(
            command_access.authenticate_command_admin(
                "Bearer ",
                team_domain="https://anevum.cloudflareaccess.com",
                audience="aud",
                allowed_emails="",
            )
        )
    assert exc.value.status_code == 401


def test_cloudflare_jwks_are_cached(monkeypatch):
    command_access._JWKS_CACHE.clear()
    calls = {"count": 0}

    async def fake_fetch(_issuer):
        calls["count"] += 1
        return [{"kid": "key-1", "kty": "RSA"}]

    monkeypatch.setattr(command_access, "_fetch_cloudflare_jwks", fake_fetch)
    first = run(command_access._resolve_cloudflare_jwk("https://team.example", "key-1"))
    second = run(command_access._resolve_cloudflare_jwk("https://team.example", "key-1"))
    assert first["kid"] == second["kid"] == "key-1"
    assert calls["count"] == 1


def test_cloudflare_jwks_timeout_fails_as_controlled_503(monkeypatch):
    command_access._JWKS_CACHE.clear()

    async def fail_fetch(_issuer):
        raise command_access.CommandAuthError(503, "Cloudflare Access signing keys are unavailable")

    monkeypatch.setattr(command_access, "_fetch_cloudflare_jwks", fail_fetch)
    with pytest.raises(command_access.CommandAuthError) as exc:
        run(command_access._resolve_cloudflare_jwk("https://team.example", "key-1"))
    assert exc.value.status_code == 503


def test_cloudflare_jwks_stale_cache_survives_transient_fetch_failure(monkeypatch):
    command_access._JWKS_CACHE.clear()
    command_access._JWKS_CACHE["https://team.example"] = (
        command_access.monotonic() - command_access._JWKS_TTL_SECONDS - 1,
        [{"kid": "key-1", "kty": "RSA"}],
    )

    async def fail_fetch(_issuer):
        raise command_access.CommandAuthError(503, "Cloudflare Access signing keys are unavailable")

    monkeypatch.setattr(command_access, "_fetch_cloudflare_jwks", fail_fetch)
    jwk = run(command_access._resolve_cloudflare_jwk("https://team.example", "key-1"))
    assert jwk["kid"] == "key-1"
