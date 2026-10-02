"""Bounded, GET-only GitHub evidence using the executor's existing connection."""
import re
from urllib.parse import quote

from .codex_handoff import REQUIRED_CHECKS, REPOSITORY


async def inspect_github(get, *, handoff_id=None, objective_key=None, pr_number=None):
    branch = await get("branches/main")
    main_sha = branch["commit"]["sha"]
    result = {"main_sha": main_sha, "association_valid": False, "merged": False,
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
             pr.get("base", {}).get("repo", {}).get("full_name") == REPOSITORY and
             pr.get("head", {}).get("repo", {}).get("full_name") == REPOSITORY and
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
        checks_ok = checks_ok and all(r.get("status") == "completed" and r.get("conclusion") == "success" for r in latest.values())
        statuses = await get(f"commits/{sha}/status")
        checks_ok = checks_ok and (statuses.get("total_count") == 0 or statuses.get("state") == "success")
    result["ci_passed"] = bool(checks_ok)
    return result
