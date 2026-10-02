from __future__ import annotations

import base64
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import httpx
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

UTC = timezone.utc


def _enabled(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)).strip())
    except (TypeError, ValueError):
        return default


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)).strip())
    except (TypeError, ValueError):
        return default


def _slug(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip().lower())
    return value.strip("-")[:48] or "work"


def _extract_response_text(body: dict[str, Any]) -> str:
    direct = body.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct
    texts: list[str] = []
    for item in body.get("output") or []:
        if not isinstance(item, dict):
            continue
        for part in item.get("content") or []:
            if not isinstance(part, dict):
                continue
            text = part.get("text")
            if isinstance(text, str):
                texts.append(text)
    return "\n".join(texts).strip()


def _parse_json_object(text: str) -> dict[str, Any]:
    value = json.loads(text.strip())
    if not isinstance(value, dict):
        raise ValueError("model_output_not_object")
    return value


class JobEnvelope(BaseModel):
    job_id: str = Field(min_length=1, max_length=128)
    objective_key: str | None = Field(default=None, max_length=240)
    title: str = Field(min_length=1, max_length=240)
    instructions: str = Field(default="", max_length=12000)
    owner_system: str = Field(default="IREN", max_length=80)
    job_type: str = Field(default="AGENT_WORK", max_length=80)
    protected_action: bool = False
    requires_human: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


@dataclass
class ModelUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0

    def add(self, body: dict[str, Any]) -> None:
        usage = body.get("usage") or {}
        self.input_tokens += int(usage.get("input_tokens") or 0)
        self.output_tokens += int(usage.get("output_tokens") or 0)
        self.calls += 1


class ExecutorRuntime:
    supported_job_types = {"SOFTWARE_BUILD", "GRAEN_RESEARCH_PROBLEM", "AGENT_WORK"}
    software_write_prefixes = (
        "app/iren/",
        "foundation/iren_",
        "tests/test_iren_",
        "docs/iren/",
        "db/migrations/",
    )
    software_denied_fragments = (
        "trading",
        "broker",
        "position",
        "risk",
        "alpaca",
        "crypto",
        "strategy",
        "order",
        "execution",
    )

    @property
    def service_enabled(self) -> bool:
        return _enabled("IREN_EXECUTOR_ENABLED", True)

    @property
    def model_execution_authorized(self) -> bool:
        return _enabled("IREN_MODEL_EXECUTION_AUTHORIZED", False)

    @property
    def token(self) -> str:
        return os.getenv("IREN_EXECUTOR_TOKEN", "").strip()

    @property
    def graen_url(self) -> str:
        return os.getenv("GRAEN_SERVICE_URL", "").strip().rstrip("/")

    @property
    def graen_token(self) -> str:
        return os.getenv("GRAEN_ADMIN_TOKEN", "").strip()

    @property
    def graen_configured(self) -> bool:
        return self.graen_url.startswith("http") and len(self.graen_token) >= 32

    @property
    def openai_key(self) -> str:
        return os.getenv("OPENAI_API_KEY", "").strip()

    @property
    def github_token(self) -> str:
        return os.getenv("IREN_GITHUB_TOKEN", "").strip()

    @property
    def github_repo(self) -> str:
        return os.getenv("IREN_GITHUB_REPOSITORY", "anevum/alpaca-trader").strip()

    @property
    def base_branch(self) -> str:
        return os.getenv("IREN_GITHUB_BASE_BRANCH", "main").strip() or "main"

    @property
    def model_name(self) -> str:
        return os.getenv("IREN_MODEL_NAME", "gpt-6-astra").strip() or "gpt-6-astra"

    @property
    def reasoning_effort(self) -> str:
        value = os.getenv("IREN_MODEL_REASONING_EFFORT", "high").strip().lower()
        return value if value in {"low", "medium", "high"} else "high"

    @property
    def max_calls_per_job(self) -> int:
        return max(1, min(4, _int_env("IREN_MODEL_MAX_CALLS_PER_JOB", 2)))

    @property
    def max_output_tokens(self) -> int:
        return max(1000, min(30000, _int_env("IREN_MODEL_MAX_OUTPUT_TOKENS", 12000)))

    @property
    def daily_budget_usd(self) -> float:
        return max(0.0, _float_env("IREN_MODEL_DAILY_BUDGET_USD", 0.0))

    @property
    def job_budget_usd(self) -> float:
        return max(0.0, _float_env("IREN_MODEL_JOB_BUDGET_USD", 0.0))

    @property
    def input_rate_per_million(self) -> float:
        return max(0.0, _float_env("IREN_MODEL_INPUT_USD_PER_MILLION", 10.0))

    @property
    def output_rate_per_million(self) -> float:
        return max(0.0, _float_env("IREN_MODEL_OUTPUT_USD_PER_MILLION", 50.0))

    @property
    def software_backend_configured(self) -> bool:
        return (
            self.model_execution_authorized
            and self.openai_key.startswith("sk-")
            and len(self.github_token) >= 20
            and self.github_repo == "anevum/alpaca-trader"
            and self.daily_budget_usd > 0
            and self.job_budget_usd > 0
            and self.job_budget_usd <= self.daily_budget_usd
        )

    def estimate_cost(self, usage: ModelUsage) -> float:
        return (
            usage.input_tokens * self.input_rate_per_million / 1_000_000
            + usage.output_tokens * self.output_rate_per_million / 1_000_000
        )

    def projected_call_cost(self, input_text: str) -> float:
        # Deliberately conservative pre-call bound: assume at most one token per
        # UTF-8 character, then add the configured maximum output allocation.
        projected_input_tokens = len(input_text)
        return (
            projected_input_tokens * self.input_rate_per_million / 1_000_000
            + self.max_output_tokens * self.output_rate_per_million / 1_000_000
        )

    def health(self) -> dict[str, Any]:
        return {
            "ok": True,
            "system": "IREN",
            "service": "iren-executor",
            "version": "iren-executor-v1.1.0",
            "revision": os.getenv("RAILWAY_GIT_COMMIT_SHA"),
            "deployment": os.getenv("RAILWAY_DEPLOYMENT_ID"),
            "codex_handoff": {"version": "v1", "github_read_only": True, "auto_merge": False},
            "service_enabled": self.service_enabled,
            "model_execution_authorized": self.model_execution_authorized,
            "model_invoked": False,
            "spending_authority": bool(
                self.model_execution_authorized
                and self.daily_budget_usd > 0
                and self.job_budget_usd > 0
            ),
            "supported_job_types": sorted(self.supported_job_types),
            "graen_configured": self.graen_configured,
            "software_backend_configured": self.software_backend_configured,
            "software_worker": {
                "model": self.model_name,
                "reasoning_effort": self.reasoning_effort,
                "max_calls_per_job": self.max_calls_per_job,
                "max_output_tokens": self.max_output_tokens,
                "daily_budget_usd": self.daily_budget_usd,
                "job_budget_usd": self.job_budget_usd,
                "repository": self.github_repo,
                "base_branch": self.base_branch,
                "draft_pr_only": True,
                "auto_merge": False,
                "budget_required": True,
                "protected_actions_fail_closed": True,
                "provider": "openai_responses_api",
                "allowed_write_prefixes": list(self.software_write_prefixes),
            },
            "execution_backends": {
                "software_build": (
                    "openai_github_draft_pr"
                    if self.software_backend_configured
                    else "configuration_required"
                ),
                "graen_research_problem": (
                    "graen_problem_api" if self.graen_configured else "not_configured"
                ),
                "agent_work": "external_worker_required",
            },
        }

    def accept(self, job: JobEnvelope) -> dict[str, Any]:
        job_type = job.job_type.strip().upper()
        if job.protected_action or job.requires_human:
            return {
                "accepted": False,
                "job_id": job.job_id,
                "status": "NEEDS_APPROVAL",
                "reason": "protected_action_requires_human_authority",
                "model_invoked": False,
            }
        if job_type not in self.supported_job_types:
            return {
                "accepted": False,
                "job_id": job.job_id,
                "status": "BLOCKED",
                "reason": "unsupported_job_type",
                "job_type": job_type,
                "model_invoked": False,
            }
        if not self.service_enabled:
            return {
                "accepted": False,
                "job_id": job.job_id,
                "status": "WAITING",
                "reason": "iren_executor_disabled",
                "model_invoked": False,
            }
        if job_type == "SOFTWARE_BUILD":
            if not self.model_execution_authorized:
                return {
                    "accepted": True,
                    "job_id": job.job_id,
                    "status": "NEEDS_APPROVAL",
                    "reason": "model_execution_not_authorized",
                    "required_authority": "model_api_spending_and_software_execution",
                    "model_invoked": False,
                }
            if not self.software_backend_configured:
                missing = []
                if not self.openai_key.startswith("sk-"):
                    missing.append("OPENAI_API_KEY")
                if len(self.github_token) < 20:
                    missing.append("IREN_GITHUB_TOKEN")
                if self.daily_budget_usd <= 0:
                    missing.append("IREN_MODEL_DAILY_BUDGET_USD")
                if self.job_budget_usd <= 0:
                    missing.append("IREN_MODEL_JOB_BUDGET_USD")
                if (
                    self.daily_budget_usd > 0
                    and self.job_budget_usd > self.daily_budget_usd
                ):
                    missing.append("IREN_MODEL_JOB_BUDGET_USD<=IREN_MODEL_DAILY_BUDGET_USD")
                return {
                    "accepted": True,
                    "job_id": job.job_id,
                    "status": "WAITING",
                    "reason": "software_worker_configuration_required",
                    "missing_configuration": missing,
                    "model_invoked": False,
                }
            return {
                "accepted": True,
                "job_id": job.job_id,
                "status": "RUNNING",
                "reason": "software_worker_ready",
                "model_invoked": False,
            }
        if job_type == "GRAEN_RESEARCH_PROBLEM":
            return {
                "accepted": self.graen_configured,
                "job_id": job.job_id,
                "status": "WAITING" if self.graen_configured else "BLOCKED",
                "reason": (
                    "graen_submission_ready"
                    if self.graen_configured
                    else "graen_problem_api_not_configured"
                ),
                "model_invoked": False,
            }
        return {
            "accepted": True,
            "job_id": job.job_id,
            "status": "WAITING",
            "reason": "external_execution_backend_not_configured",
            "model_invoked": False,
        }

    def _github_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.github_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def _github_json(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        url = f"https://api.github.com/repos/{self.github_repo}/{path.lstrip('/')}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.request(
                method,
                url,
                headers=self._github_headers(),
                json=json_body,
            )
            response.raise_for_status()
            if response.status_code == 204:
                return {}
            return response.json()

    async def _repo_tree(self) -> list[str]:
        branch = await self._github_json("GET", f"branches/{quote(self.base_branch, safe='')}")
        sha = str(((branch or {}).get("commit") or {}).get("sha") or "")
        if not sha:
            raise ValueError("github_base_sha_missing")
        tree = await self._github_json("GET", f"git/trees/{sha}?recursive=1")
        return [
            str(row.get("path"))
            for row in (tree or {}).get("tree") or []
            if row.get("type") == "blob"
        ]

    async def _file_text(self, path: str, ref: str) -> tuple[str, str]:
        body = await self._github_json(
            "GET",
            f"contents/{quote(path, safe='/')}?ref={quote(ref, safe='')}",
        )
        content = base64.b64decode(body.get("content") or "").decode("utf-8")
        return content, str(body.get("sha") or "")

    def _write_path_allowed(self, path: str) -> bool:
        normalized = path.strip().lstrip("/")
        lower = normalized.lower()
        if not any(normalized.startswith(prefix) for prefix in self.software_write_prefixes):
            return False
        if normalized.startswith("db/migrations/"):
            return True
        return not any(fragment in lower for fragment in self.software_denied_fragments)

    async def _openai_json(
        self,
        *,
        instructions: str,
        input_text: str,
        usage: ModelUsage,
    ) -> dict[str, Any]:
        if usage.calls >= self.max_calls_per_job:
            raise RuntimeError("model_call_limit_reached")
        projected_total = self.estimate_cost(usage) + self.projected_call_cost(input_text)
        if projected_total > self.job_budget_usd:
            raise RuntimeError("model_job_budget_insufficient_for_bounded_call")
        payload = {
            "model": self.model_name,
            "instructions": instructions,
            "input": input_text,
            "reasoning": {"effort": self.reasoning_effort},
            "max_output_tokens": self.max_output_tokens,
            "store": False,
        }
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                "https://api.openai.com/v1/responses",
                headers={
                    "Authorization": f"Bearer {self.openai_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()
            body = response.json()
        usage.add(body)
        if self.estimate_cost(usage) > self.job_budget_usd:
            raise RuntimeError("model_job_budget_exceeded")
        return _parse_json_object(_extract_response_text(body))

    async def _create_branch(self, base_sha: str, job: JobEnvelope) -> str:
        branch_name = (
            f"iren/worker/{_slug(job.objective_key or job.title)}-"
            f"{_slug(job.job_id)[-12:]}"
        )[:120]
        await self._github_json(
            "POST",
            "git/refs",
            json_body={"ref": f"refs/heads/{branch_name}", "sha": base_sha},
        )
        return branch_name

    async def run_software_build(self, job: JobEnvelope) -> dict[str, Any]:
        if not self.software_backend_configured:
            return self.accept(job)

        usage = ModelUsage()
        try:
            branch_info = await self._github_json(
                "GET",
                f"branches/{quote(self.base_branch, safe='')}",
            )
            base_sha = str(((branch_info or {}).get("commit") or {}).get("sha") or "")
            if not base_sha:
                raise ValueError("github_base_sha_missing")
            tree = await self._repo_tree()
            visible = [
                path
                for path in tree
                if (
                    path.startswith("app/iren/")
                    or path.startswith("foundation/iren_")
                    or path.startswith("tests/test_iren_")
                    or path.startswith("docs/iren/")
                    or path.startswith("db/migrations/")
                )
            ][:500]

            plan = await self._openai_json(
                instructions=(
                    "You are the bounded IREN software worker. Analyze the task and choose "
                    "the minimum files needed. You may work only on IREN control-plane code, "
                    "IREN Foundation gateway code, IREN tests/docs, and migrations. Never "
                    "modify trading, broker, risk, position sizing, strategy, crypto execution, "
                    "credentials, deployment settings, or unrelated subsystems. Return valid JSON "
                    "only with keys: summary (string), files_to_read (array of repository paths), "
                    "files_to_change (array of repository paths). Do not propose more than 8 files."
                ),
                input_text=json.dumps(
                    {
                        "title": job.title,
                        "instructions": job.instructions,
                        "objective_key": job.objective_key,
                        "success_criteria": job.metadata.get("success_criteria") or {},
                        "repository_files": visible,
                    }
                ),
                usage=usage,
            )
            read_paths = [
                str(path)
                for path in plan.get("files_to_read") or []
                if str(path) in tree
            ][:8]
            change_paths = [
                str(path)
                for path in plan.get("files_to_change") or []
                if self._write_path_allowed(str(path))
            ][:8]
            if not change_paths:
                raise ValueError("model_proposed_no_allowed_changes")
            for path in change_paths:
                if path not in read_paths and path in tree:
                    read_paths.append(path)

            context: dict[str, str] = {}
            file_shas: dict[str, str] = {}
            total_chars = 0
            for path in read_paths[:8]:
                text, sha = await self._file_text(path, self.base_branch)
                if total_chars + len(text) > 180_000:
                    break
                context[path] = text
                file_shas[path] = sha
                total_chars += len(text)

            patch = await self._openai_json(
                instructions=(
                    "Implement the requested IREN control-plane software change. Return valid JSON "
                    "only with keys: pr_title, pr_body, files. files must be an array of objects "
                    "with path and content containing the COMPLETE UTF-8 file contents. Change only "
                    "paths explicitly listed in allowed_change_paths. Preserve existing behavior "
                    "outside the task. Add or update tests. Do not include secrets. Do not merge, "
                    "deploy, alter trading/risk/broker behavior, or broaden authority."
                ),
                input_text=json.dumps(
                    {
                        "task": {
                            "title": job.title,
                            "instructions": job.instructions,
                            "objective_key": job.objective_key,
                            "success_criteria": job.metadata.get("success_criteria") or {},
                        },
                        "allowed_change_paths": change_paths,
                        "files": context,
                    }
                ),
                usage=usage,
            )

            edits = patch.get("files") or []
            if not isinstance(edits, list) or not edits:
                raise ValueError("model_returned_no_files")
            normalized_edits: list[tuple[str, str]] = []
            for row in edits[:8]:
                if not isinstance(row, dict):
                    raise ValueError("invalid_model_file_edit")
                path = str(row.get("path") or "").strip()
                text = row.get("content")
                if path not in change_paths or not self._write_path_allowed(path):
                    raise ValueError(f"model_write_path_rejected:{path}")
                if not isinstance(text, str) or not text:
                    raise ValueError(f"model_file_content_invalid:{path}")
                normalized_edits.append((path, text))

            branch_name = await self._create_branch(base_sha, job)
            for path, text in normalized_edits:
                payload: dict[str, Any] = {
                    "message": f"IREN worker: {job.title}"[:120],
                    "content": base64.b64encode(text.encode("utf-8")).decode("ascii"),
                    "branch": branch_name,
                }
                if path in file_shas:
                    payload["sha"] = file_shas[path]
                await self._github_json(
                    "PUT",
                    f"contents/{quote(path, safe='/')}",
                    json_body=payload,
                )

            pr = await self._github_json(
                "POST",
                "pulls",
                json_body={
                    "title": str(patch.get("pr_title") or job.title)[:240],
                    "body": (
                        str(patch.get("pr_body") or "")
                        + "\n\n---\nCreated by bounded IREN model worker v1. "
                        "Draft only; CI and human/IREN verification required before merge."
                    )[:60000],
                    "head": branch_name,
                    "base": self.base_branch,
                    "draft": True,
                },
            )
            return {
                "accepted": True,
                "job_id": job.job_id,
                "status": "SUCCEEDED",
                "reason": "draft_pull_request_created",
                "model_invoked": True,
                "model": self.model_name,
                "reasoning_effort": self.reasoning_effort,
                "model_calls": usage.calls,
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "estimated_job_cost_usd": round(self.estimate_cost(usage), 6),
                "branch": branch_name,
                "pull_request_number": pr.get("number"),
                "pull_request_url": pr.get("html_url"),
                "draft": True,
                "auto_merge": False,
                "changed_files": [path for path, _ in normalized_edits],
                "base_sha": base_sha,
            }
        except Exception as exc:
            return {
                "accepted": True,
                "job_id": job.job_id,
                "status": "FAILED",
                "reason": "software_worker_failed",
                "error_type": type(exc).__name__,
                "error": str(exc)[:500],
                "model_invoked": usage.calls > 0,
                "model": self.model_name,
                "model_calls": usage.calls,
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "estimated_job_cost_usd": round(self.estimate_cost(usage), 6),
            }

    async def submit_graen(self, job: JobEnvelope) -> dict[str, Any]:
        if not self.graen_configured:
            return {
                "accepted": False,
                "job_id": job.job_id,
                "status": "BLOCKED",
                "reason": "graen_problem_api_not_configured",
                "model_invoked": False,
            }
        success_criteria = job.metadata.get("success_criteria")
        if not isinstance(success_criteria, dict):
            success_criteria = {}
        payload = {
            "title": job.title,
            "statement": job.instructions,
            "domain": "CRYPTO_STRATEGY_RESEARCH",
            "priority": 100,
            "source": "IREN",
            "requested_by": "iren-executor",
            "linked_iren_job_id": job.job_id,
            "constraints": {
                "research_only": True,
                "production_authority": False,
                "broker_authority": False,
                "risk_or_sizing_authority": False,
                "crypto_execution_enabled": False,
                "falsification_required": True,
                "preserve_search_history": True,
            },
            "success_criteria": success_criteria,
            "metadata": {
                "objective_key": job.objective_key,
                "requested_via": "iren-executor",
            },
        }
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(
                    f"{self.graen_url}/v1/problems",
                    headers={"x-graen-admin-token": self.graen_token},
                    json=payload,
                )
                response.raise_for_status()
                body = response.json()
        except Exception as exc:
            return {
                "accepted": True,
                "job_id": job.job_id,
                "status": "WAITING",
                "reason": "graen_problem_submission_failed",
                "error_type": type(exc).__name__,
                "model_invoked": False,
            }
        problem = body.get("problem") if isinstance(body, dict) else None
        problem = problem if isinstance(problem, dict) else {}
        return {
            "accepted": True,
            "job_id": job.job_id,
            "status": "WAITING",
            "reason": "graen_problem_submitted",
            "graen_problem_id": problem.get("problem_id"),
            "graen_problem_key": problem.get("problem_key"),
            "model_invoked": False,
        }


runtime = ExecutorRuntime()
app = FastAPI(title="IREN Executor Boundary", version="1.1.0")


def _require_token(value: str | None) -> None:
    expected = runtime.token
    if len(expected) < 32 or value != expected:
        raise HTTPException(status_code=401, detail="unauthorized")


@app.get("/health")
async def health():
    return runtime.health()


@app.get("/v1/capabilities")
async def capabilities(x_anevum_scheduler_token: str | None = Header(default=None)):
    _require_token(x_anevum_scheduler_token)
    return runtime.health()


@app.post("/v1/jobs/accept")
async def accept_job(
    job: JobEnvelope,
    x_anevum_scheduler_token: str | None = Header(default=None),
):
    _require_token(x_anevum_scheduler_token)
    accepted = runtime.accept(job)
    job_type = job.job_type.strip().upper()
    if job_type == "SOFTWARE_BUILD" and accepted.get("status") == "RUNNING":
        return await runtime.run_software_build(job)
    if job_type == "GRAEN_RESEARCH_PROBLEM" and accepted.get("accepted"):
        return await runtime.submit_graen(job)
    return accepted

@app.get("/v1/codex/github")
async def codex_github(
    handoff_id: str | None = None,
    objective_key: str | None = None,
    pr_number: int | None = None,
    x_anevum_scheduler_token: str | None = Header(default=None),
):
    _require_token(x_anevum_scheduler_token)
    from .codex_github import inspect_github
    if runtime.github_repo != "anevum/alpaca-trader" or len(runtime.github_token) < 20:
        raise HTTPException(status_code=503, detail="github_evidence_unavailable")
    async def get(path):
        return await runtime._github_json("GET", path)
    try:
        return await inspect_github(get, handoff_id=handoff_id, objective_key=objective_key, pr_number=pr_number)
    except (httpx.HTTPError, ValueError, KeyError):
        raise HTTPException(status_code=503, detail="github_evidence_unavailable")
