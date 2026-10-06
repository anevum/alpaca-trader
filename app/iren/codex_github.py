"""Bounded, GET-only GitHub evidence using the executor's existing connection."""
import asyncio
import re

import httpx
from urllib.parse import parse_qs, quote, urlparse

from .codex_handoff import (
    REQUIRED_CHECKS,
    configured_repository,
    repository_allowed,
)
from datetime import datetime, timezone



PINNED_RUNTIME_STATUSES = {
    "VELUM": {
        "revision": "6f22220557934839152aeddaf49ac367982defad",
        "context": "RHEN - rhen-velum",
        "project_id": "808098a9-937e-4ca4-ac98-dd2dcfef5d0c",
        "service_id": "55a298e1-ea60-4342-a10f-a736a2d71f8c",
        "environment_id": "63a64723-574d-497b-b01b-a9fef7ea78ab",
        "service_name": "rhen-velum",
    },
    "CRYPTO_EDGE": {
        "revision": "9628c08f0b7a065c580bd8779168f3ba526cb885",
        "context": "RHEN - rhen-crypto-edge-discovery",
        "project_id": "808098a9-937e-4ca4-ac98-dd2dcfef5d0c",
        "service_id": "4ed9d192-102c-4b66-8ed7-b9a650a064c5",
        "environment_id": "63a64723-574d-497b-b01b-a9fef7ea78ab",
        "service_name": "rhen-crypto-edge-discovery",
    },
}


def _railway_status_evidence(row, spec):
    target = str(row.get("target_url") or "")
    parsed = urlparse(target)
    parts = [part for part in parsed.path.split("/") if part]
    query = parse_qs(parsed.query)
    try:
        project = parts[parts.index("project") + 1]
        service = parts[parts.index("service") + 1]
    except (ValueError, IndexError):
        return None
    deployment = (query.get("id") or [None])[0]
    environment = (query.get("environmentId") or [None])[0]
    if (
        parsed.scheme != "https"
        or parsed.netloc != "railway.com"
        or project != spec["project_id"]
        or service != spec["service_id"]
        or environment != spec["environment_id"]
        or not isinstance(deployment, str)
        or not re.fullmatch(r"[0-9a-f-]{36}", deployment)
        or row.get("state") != "success"
    ):
        return None
    return {
        "verified": True,
        "revision": spec["revision"],
        "deployment": deployment,
        "service_name": spec["service_name"],
        "status_context": spec["context"],
        "evidence_source": "github_railway_deployment_status",
    }


# Two serial lookups must finish inside the controller's 12-second read timeout.
RUNTIME_LOOKUP_TIMEOUT_SECONDS = 4.0


def _provider_failure(exc):
    """Bounded diagnostics: never return exception URLs, response bodies or credentials."""
    detail = {"reason": "provider_unavailable", "error_type": type(exc).__name__}
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        detail["reason"] = "provider_timeout"
    elif isinstance(exc, httpx.HTTPStatusError):
        response = exc.response
        detail["http_status"] = response.status_code
        detail["reason"] = {
            401: "provider_authentication_failed",
            403: "provider_forbidden",
            404: "provider_not_found_or_inaccessible",
            429: "provider_rate_limited",
        }.get(response.status_code, "provider_http_error")
        if response.headers.get("x-ratelimit-remaining") == "0":
            detail["reason"] = "provider_rate_limited"
        # Exact known GitHub messages are useful for permission diagnosis. Arbitrary
        # provider text is deliberately excluded from both the API and runtime logs.
        try:
            body = response.json()
        except ValueError:
            body = None
        message = body.get("message") if isinstance(body, dict) else None
        if message in (
            "Bad credentials", "Not Found",
            "Resource not accessible by personal access token",
            "Resource not accessible by integration",
        ):
            detail["provider_message"] = message
    elif isinstance(exc, ValueError):
        detail["reason"] = "provider_invalid_response"
    return detail


async def inspect_runtime_inventory(
    get, *, unavailable_reason=None, repository: str | None = None
):
    observed_at = datetime.now(timezone.utc).isoformat()
    repository = repository or configured_repository()
    if not repository_allowed(repository):
        unavailable_reason = unavailable_reason or "provider_repository_not_allowed"
    services = {}
    # Reuse only successful responses within this observation, never stale evidence.
    responses = {}
    for name, spec in PINNED_RUNTIME_STATUSES.items():
        path = f"commits/{spec['revision']}/status"
        evidence = {
            "verified": False,
            "revision": spec["revision"],
            "deployment": None,
            "service_name": spec["service_name"],
            "status_context": spec["context"],
            "evidence_source": "github_railway_deployment_status",
            "provider_request": f"GET /repos/{repository}/{path}",
        }
        services[name] = evidence
        if unavailable_reason:
            evidence["reason"] = unavailable_reason
            continue
        try:
            if path not in responses:
                async with asyncio.timeout(RUNTIME_LOOKUP_TIMEOUT_SECONDS):
                    body = await get(path)
                if not isinstance(body, dict) or not isinstance(body.get("statuses"), list):
                    raise ValueError("invalid_status_envelope")
                if not all(isinstance(row, dict) for row in body["statuses"]):
                    raise ValueError("invalid_status_row")
                responses[path] = body
            rows = [
                row for row in responses[path]["statuses"]
                if row.get("context") == spec["context"]
            ]
            if not rows:
                evidence["reason"] = "status_missing"
            elif len(rows) != 1:
                evidence["reason"] = "status_ambiguous"
            else:
                verified = _railway_status_evidence(rows[0], spec)
                if verified:
                    evidence.update(verified, reason="verified")
                else:
                    evidence["reason"] = "status_not_verified"
        except (httpx.HTTPError, TimeoutError, ValueError) as exc:
            evidence.update(_provider_failure(exc))
    return {
        "schema_version": "iren_provider_inventory.v1",
        "observed_at": observed_at,
        "read_only": True,
        "provider_write_authority": False,
        "complete": all(row["verified"] is True for row in services.values()),
        "services": services,
    }


async def inspect_github(
    get, *, handoff_id=None, objective_key=None, pr_number=None,
    repository: str | None = None,
):
    repository = repository or configured_repository()
    if not repository_allowed(repository):
        raise ValueError("repository_not_allowed")
    branch = await get("branches/main")
    main_sha = branch["commit"]["sha"]
    result = {"observed_at": datetime.now(timezone.utc).isoformat(), "main_sha": main_sha, "association_valid": False, "merged": False,
              "landed": False, "ci_passed": False, "files": [], "model_invoked": False}
    if not handoff_id:
        return result
    if not re.fullmatch(r"[0-9a-f-]{36}", handoff_id):
        raise ValueError("invalid_handoff_id")
    expected_branch = "codex/handoff/" + handoff_id
    if pr_number is None:
        rows = await get("pulls?state=all&head=anevum:" + quote(expected_branch, safe="") + "&base=main&per_page=100")
        if len(rows) != 1:
            return {**result, "reason": "github_association_missing_or_ambiguous"}
        pr_number = rows[0]["number"]
    if type(pr_number) is not int or pr_number < 1:
        raise ValueError("invalid_pull_request")
    pr = await get(f"pulls/{pr_number}")
    body = pr.get("body") or ""
    marker = f"IREN-Handoff: {handoff_id}"
    objective_marker = f"IREN-Objective: {objective_key}"
    exact_markers = [line.strip() for line in body.splitlines()]
    valid = (pr.get("base", {}).get("ref") == "main" and
             pr.get("base", {}).get("repo", {}).get("full_name") == repository and
             pr.get("head", {}).get("repo", {}).get("full_name") == repository and
             pr.get("head", {}).get("ref") == expected_branch and
             exact_markers.count(marker) == 1 and exact_markers.count(objective_marker) == 1 and
             len([line for line in exact_markers if line.startswith("IREN-Handoff:")]) == 1)
    if not valid:
        return {**result, "reason": "github_association_mismatch"}
    result.update(association_valid=True, pr_number=pr_number, pr_url=pr.get("html_url"),
                  branch=expected_branch, merged=pr.get("merged") is True,
                  head_sha=pr["head"]["sha"], merge_sha=pr.get("merge_commit_sha"))
    if pr.get("changed_files", 0) > 100:
        return {**result, "reason": "diff_exceeds_bounded_review"}
    files = await get(f"pulls/{pr_number}/files?per_page=100")
    if len(files) != pr.get("changed_files"):
        return {**result, "reason": "diff_incomplete"}
    result["files"] = sorted({p for f in files for p in [f["filename"], f.get("previous_filename")] if p})
    if result["merged"]:
        compare = await get(f"compare/{result['merge_sha']}...{main_sha}")
        result["landed"] = compare.get("status") in {"identical", "ahead"}
    checks_ok = True
    for sha in sorted({pr["head"]["sha"], main_sha}):
        checks = await get(f"commits/{sha}/check-runs?per_page=100")
        rows = checks.get("check_runs") or []
        # Latest run per name; reject truncation, pending/failure and unexpected non-success.
        latest = {}
        for row in sorted(rows, key=lambda r: r.get("id", 0), reverse=True):
            if (row.get("app") or {}).get("slug") == "github-actions":
                latest.setdefault(row["name"], row)
        checks_ok = checks_ok and checks.get("total_count", 0) <= 100 and REQUIRED_CHECKS <= set(latest)
        checks_ok = checks_ok and all(
            r.get("status") == "completed" and r.get("conclusion") == "success"
            for r in latest.values()
        )
    result["ci_passed"] = bool(checks_ok)
    return result
