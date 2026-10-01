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
    calls = {"access": 0}

    async def deny_access(*args, **kwargs):
        calls["access"] += 1
        raise command_access.CommandAuthError(401, "invalid access")

    monkeypatch.setattr(command_access, "verify_cloudflare_access", deny_access)

    with pytest.raises(command_access.CommandAuthError):
        run(
            command_access.authenticate_command_admin(
                "Bearer test",
                team_domain="https://anevum.cloudflareaccess.com",
                audience="aud",
                allowed_emails="",
            )
        )
    assert calls["access"] == 1
    assert not hasattr(command_access, "verify_supabase_command_admin")


def test_command_auth_forwards_access_configuration(monkeypatch):
    observed = {}

    async def access(token, **kwargs):
        observed["token"] = token
        observed.update(kwargs)
        return {
            "email": "devon@anevum.com",
            "auth_source": "cloudflare_access",
        }

    monkeypatch.setattr(command_access, "verify_cloudflare_access", access)

    identity = run(
        command_access.authenticate_command_admin(
            "Bearer access-token",
            team_domain="https://wispy-tooth-095a.cloudflareaccess.com",
            audience="audience",
            allowed_emails="devon@anevum.com",
        )
    )

    assert identity["auth_source"] == "cloudflare_access"
    assert observed == {
        "token": "access-token",
        "team_domain": "https://wispy-tooth-095a.cloudflareaccess.com",
        "audience": "audience",
        "allowed_emails": "devon@anevum.com",
    }
