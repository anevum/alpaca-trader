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
