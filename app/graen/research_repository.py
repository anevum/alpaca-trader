"""GitHub transport for bounded research promotion.

Credentials must already be authorized in the runtime. A ChatGPT connector
session is never treated as a transferable runtime credential.
"""
from __future__ import annotations

import base64
import os
from urllib.parse import quote
import httpx

from graen.engineering import CANONICAL_BRANCH, REPOSITORY, IntegrityError


class TransportUnavailable(RuntimeError):
    pass


class AuthorizationDenied(RuntimeError):
    pass


class GitHubRepository:
    def __init__(self, token=None, client=None):
        self.token = token if token is not None else os.getenv("GRAEN_RESEARCH_GITHUB_TOKEN", "")
        self.client = client

    @property
    def configured(self):
        return bool(self.token)

    async def request(self, method, path, payload=None):
        if not self.configured:
            raise AuthorizationDenied("runtime_github_authorization_not_configured")
        headers = {
            "Authorization": "Bearer " + self.token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        url = "https://api.github.com" + path
        try:
            if self.client is not None:
                response = await self.client.request(method, url, headers=headers, json=payload)
            else:
                async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
                    response = await client.request(method, url, headers=headers, json=payload)
        except httpx.TransportError as exc:
            raise TransportUnavailable(type(exc).__name__) from exc
        if response.status_code in (401, 403):
            raise AuthorizationDenied("github_authorization_denied")
        if response.status_code >= 500:
            raise TransportUnavailable("github_unavailable")
        response.raise_for_status()
        return response.json()

    @property
    def root(self):
        return "/repos/" + REPOSITORY

    async def head(self, branch=CANONICAL_BRANCH):
        data = await self.request("GET", self.root + "/git/ref/heads/" + quote(branch, safe=""))
        return data["object"]["sha"]

    async def create_branch(self, branch, base):
        if await self.head() != base:
            raise IntegrityError("canonical_branch_moved")
        try:
            response = await self.request("POST", self.root + "/git/refs", {"ref": "refs/heads/" + branch, "sha": base})
            return response["object"]["sha"]
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 422:
                raise
            current = await self.head(branch)
            if current != base:
                raise IntegrityError("existing_branch_not_fresh")
            return current

    async def _contents(self, branch, path):
        try:
            return await self.request("GET", self.root + "/contents/" + quote(path, safe="/") + "?ref=" + quote(branch, safe=""))
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise

    async def bundle_matches(self, branch, files):
        for path, expected in files.items():
            blob = await self._contents(branch, path)
            if not blob or blob.get("type") != "file" or blob.get("encoding") != "base64":
                return False
            if base64.b64decode(blob["content"]).decode() != expected:
                return False
        return True

    async def write_primary(self, branch, parent, files, message):
        query = """mutation($input:CreateCommitOnBranchInput!) {
          createCommitOnBranch(input:$input) { commit { oid } }
        }"""
        additions = [{"path": path, "contents": base64.b64encode(value.encode()).decode()} for path, value in sorted(files.items())]
        result = await self.request("POST", "/graphql", {
            "query": query, "variables": {"input": {
                "branch": {"repositoryNameWithOwner": REPOSITORY, "branchName": branch},
                "expectedHeadOid": parent, "message": {"headline": message},
                "fileChanges": {"additions": additions},
            }},
        })
        if result.get("errors"):
            kinds = {error.get("type") for error in result["errors"]}
            if kinds & {"FORBIDDEN", "UNAUTHORIZED", "NOT_FOUND"}:
                raise AuthorizationDenied("graphql_authorization_denied")
            if kinds <= {"INTERNAL", "SERVICE_UNAVAILABLE"}:
                raise TransportUnavailable("graphql_unavailable")
            raise IntegrityError("graphql_write_rejected")
        return result["data"]["createCommitOnBranch"]["commit"]["oid"]

    async def write_fallback(self, branch, parent, files, message):
        if await self.head(branch) != parent:
            raise IntegrityError("branch_moved_before_fallback")
        commit = await self.request("GET", self.root + "/git/commits/" + parent)
        tree = await self.request("POST", self.root + "/git/trees", {
            "base_tree": commit["tree"]["sha"],
            "tree": [{"path": path, "mode": "100644", "type": "blob", "content": value} for path, value in sorted(files.items())],
        })
        result = await self.request("POST", self.root + "/git/commits", {
            "message": message, "tree": tree["sha"], "parents": [parent],
        })
        await self.request("PATCH", self.root + "/git/refs/heads/" + quote(branch, safe=""), {"sha": result["sha"], "force": False})
        return result["sha"]

    async def write(self, branch, parent, files, message):
        if await self.head(branch) != parent:
            raise IntegrityError("branch_moved_before_write")
        try:
            return await self.write_primary(branch, parent, files, message), "github_graphql"
        except TransportUnavailable:
            current = await self.head(branch)
            # An ambiguous timeout may have committed successfully. Never overwrite.
            if current != parent:
                if await self.bundle_matches(branch, files):
                    return current, "primary_reconciled"
                raise IntegrityError("ambiguous_primary_write")
            return await self.write_fallback(branch, parent, files, message), "github_git_data_api"

    async def open_pr(self, branch, title, body):
        prs = await self.request("GET", self.root + "/pulls?state=all&head=anevum:" + quote(branch, safe="") + "&base=" + CANONICAL_BRANCH)
        if prs:
            if len(prs) != 1 or prs[0].get("state") != "open":
                raise IntegrityError("branch_has_terminal_pull_request")
            return prs[0]["number"]
        result = await self.request("POST", self.root + "/pulls", {
            "title": title, "body": body, "head": branch, "base": CANONICAL_BRANCH,
        })
        return result["number"]

    async def pr(self, number):
        return await self.request("GET", self.root + "/pulls/" + str(number))

    async def diff(self, number):
        # Compiler permits exactly three files. Reject pagination/oversized diffs.
        info = await self.pr(number)
        if info.get("changed_files") != 3:
            raise IntegrityError("unexpected_diff_size")
        return await self.request("GET", self.root + "/pulls/" + str(number) + "/files?per_page=100")

    async def ci(self, head):
        response = await self.request("GET", self.root + "/actions/runs?event=pull_request&head_sha=" + head + "&per_page=100")
        if response.get("total_count", 0) > 100:
            raise IntegrityError("ci_pagination_requires_review")
        runs = response.get("workflow_runs", [])
        for run in runs:
            result = await self.request("GET", self.root + "/actions/runs/" + str(run["id"]) + "/jobs?filter=latest&per_page=100")
            if result.get("total_count", 0) > 100:
                raise IntegrityError("ci_jobs_truncated")
            run["jobs"] = result.get("jobs", [])
        return runs

    async def merge(self, number, head):
        # GitHub enforces repository protections; no force/admin bypass is used.
        return await self.request("PUT", self.root + "/pulls/" + str(number) + "/merge", {
            "sha": head, "merge_method": "merge",
        })
