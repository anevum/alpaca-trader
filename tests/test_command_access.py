import asyncio

import pytest

from app import command_access


def run(coro):
    return asyncio.run(coro)


def test_cloudflare_mode_requires_bearer_authorization():
    with pytest.raises(command_access.CommandAuthError) as exc:
        run(
            command_access.authenticate_command_admin(
                None,
                mode="cloudflare_access",
                team_domain="https://anevum.cloudflareaccess.com",
                audience="aud",
                allowed_emails="",
                supabase_url="https://example.supabase.co",
                publishable_key="key",
                founder_email="founder@example.com",
            )
        )
    assert exc.value.status_code == 401


def test_cloudflare_team_domain_must_be_https():
    with pytest.raises(command_access.CommandAuthError) as exc:
        command_access.normalized_team_domain("anevum.cloudflareaccess.com")
    assert exc.value.status_code == 503


def test_cloudflare_mode_never_falls_back_to_supabase(monkeypatch):
    calls = {"supabase": 0}

    async def deny_access(*args, **kwargs):
        raise command_access.CommandAuthError(401, "invalid access")

    async def supabase(*args, **kwargs):
        calls["supabase"] += 1
        return {"email": "legacy@example.com"}

    monkeypatch.setattr(command_access, "verify_cloudflare_access", deny_access)
    monkeypatch.setattr(command_access, "verify_supabase_command_admin", supabase)

    with pytest.raises(command_access.CommandAuthError):
        run(
            command_access.authenticate_command_admin(
                "Bearer test",
                mode="cloudflare_access",
                team_domain="https://anevum.cloudflareaccess.com",
                audience="aud",
                allowed_emails="",
                supabase_url="https://example.supabase.co",
                publishable_key="key",
                founder_email="founder@example.com",
            )
        )
    assert calls["supabase"] == 0


def test_dual_mode_preserves_legacy_fallback_during_cutover(monkeypatch):
    async def deny_access(*args, **kwargs):
        raise command_access.CommandAuthError(401, "not access")

    async def supabase(*args, **kwargs):
        return {"email": "founder@example.com", "auth_source": "supabase"}

    monkeypatch.setattr(command_access, "verify_cloudflare_access", deny_access)
    monkeypatch.setattr(command_access, "verify_supabase_command_admin", supabase)

    identity = run(
        command_access.authenticate_command_admin(
            "Bearer legacy-token",
            mode="dual",
            team_domain="https://anevum.cloudflareaccess.com",
            audience="aud",
            allowed_emails="",
            supabase_url="https://example.supabase.co",
            publishable_key="key",
            founder_email="founder@example.com",
        )
    )
    assert identity["email"] == "founder@example.com"


def test_invalid_auth_mode_fails_closed():
    with pytest.raises(command_access.CommandAuthError) as exc:
        run(
            command_access.authenticate_command_admin(
                "Bearer token",
                mode="unexpected",
                team_domain="https://anevum.cloudflareaccess.com",
                audience="aud",
                allowed_emails="",
                supabase_url="https://example.supabase.co",
                publishable_key="key",
                founder_email="founder@example.com",
            )
        )
    assert exc.value.status_code == 503
