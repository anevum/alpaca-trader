from app.iren.work import (
    action_signature,
    autopilot_decision,
    criteria_satisfied,
    dependencies_complete,
    choose_next_action,
    normalize_command,
    process_command,
    runtime_evidence_criteria,
    status_summary,
)


def snapshot():
    return {
        "objectives": [
            {
                "objective_key": "ROOT",
                "title": "Root",
                "status": "LOCKED",
                "priority": 100,
                "dependencies": [],
                "owner_system": "IREN",
                "metadata": {},
            },
            {
                "objective_key": "ENGINE",
                "title": "Engine",
                "status": "COMPLETE",
                "priority": 99,
                "dependencies": [],
                "owner_system": "IREN",
                "metadata": {},
            },
            {
                "objective_key": "COMMAND",
                "title": "Command",
                "description": "Build Command surface.",
                "status": "READY",
                "priority": 95,
                "dependencies": ["ENGINE"],
                "owner_system": "IREN",
                "protected_action": False,
                "success_criteria": {"dock": True},
                "metadata": {"job_type": "SOFTWARE_BUILD"},
            },
            {
                "objective_key": "GRAEN",
                "title": "GRAEN",
                "description": "Build GRAEN runtime.",
                "status": "READY",
                "priority": 90,
                "dependencies": ["ENGINE"],
                "owner_system": "GRAEN",
                "protected_action": False,
                "metadata": {"job_type": "SOFTWARE_BUILD"},
            },
        ],
        "jobs": [],
    }


def test_command_normalization():
    assert normalize_command("what's next?") == "NEXT"
    assert normalize_command("do that") == "EXECUTE_NEXT"
    assert normalize_command("status") == "STATUS"
    assert normalize_command("Current IREN status") == "STATUS"
    assert normalize_command("IREN status") == "STATUS"
    assert normalize_command("current status") == "STATUS"
    assert normalize_command("maintenance prompt") == "MAINTENANCE_PROMPT"
    assert normalize_command("maintenance prompt: fix Command telemetry") == "MAINTENANCE_PROMPT"
    assert normalize_command("generate codex maintenance prompt: verify Railway") == "MAINTENANCE_PROMPT"
    assert normalize_command("build the next thing") == "DIRECTIVE"


def test_maintenance_prompt_is_deterministic_read_only_and_grounded_in_iren_state():
    data = snapshot()
    control = {
        "state": "DEGRADED",
        "observed_at": "2026-10-06T06:00:00+00:00",
        "topology": {
            "inventory_complete": True,
            "services": [
                {
                    "service_id": "RHEN",
                    "service_name": "RHEN runtime",
                    "status": "HEALTHY",
                    "readiness": True,
                    "revision": "a" * 40,
                    "deployment": "deploy-rhen",
                }
            ],
        },
        "incidents": {
            "evidence.loss": {
                "status": "OPEN",
                "severity": "warning",
                "reason": "runtime_event_loss_increasing",
            }
        },
    }

    result = process_command(
        "maintenance prompt: focus on Command telemetry and keep trading behavior unchanged",
        data,
        control,
        requested_by="devon",
        source="command",
    )

    assert result.intent == "MAINTENANCE_PROMPT"
    assert result.job is None
    assert result.response["action_taken"] is False
    assert result.response["prompt_version"] == "iren-maintenance-v2"
    assert result.response["maintenance_focus"] == (
        "focus on Command telemetry and keep trading behavior unchanged"
    )
    prompt = result.response["maintenance_prompt"]
    assert "anevum/alpaca-trader" in prompt
    assert "anevum/anevum-web" in prompt
    assert "State: DEGRADED" in prompt
    assert "RHEN runtime | status=HEALTHY | ready=YES" in prompt
    assert "evidence.loss | severity=warning | reason=runtime_event_loss_increasing" in prompt
    assert "COMMAND | READY | Command | owner=IREN" in prompt
    assert "focus on Command telemetry and keep trading behavior unchanged" in prompt
    assert "Keep paid model/API worker spending disabled" in prompt
    assert "No model/API worker was invoked" in prompt
    assert "Do not manufacture churn" in prompt
    assert "SYSTEM RESPONSIBILITY MAP" in prompt
    assert "RESEARCH -> STRATEGY CONTROL LOOP" in prompt
    assert "JOB + OBJECTIVE ORCHESTRATION" in prompt
    assert result.response["maintenance_manifest"]["mode"] == "STABILIZE"
    assert result.response["maintenance_manifest"]["budget"]["primary_objectives"] == 1


def test_maintenance_prompt_treats_malformed_topology_as_missing_evidence():
    result = process_command(
        "maintenance prompt",
        {"objectives": [], "jobs": []},
        {"state": "HEALTHY", "incidents": {}, "topology": ["not", "a", "mapping"]},
        requested_by="devon",
        source="command",
    )

    assert "No service inventory supplied. Treat this as an evidence gap, not as healthy." in result.response["maintenance_prompt"]


def test_maintenance_prompt_reports_missing_service_inventory_instead_of_inventing_it():
    result = process_command(
        "maintenance prompt",
        {"objectives": [], "jobs": []},
        {"state": "HEALTHY", "incidents": {}},
        requested_by="devon",
        source="command",
    )

    prompt = result.response["maintenance_prompt"]
    assert "No service inventory supplied. Treat this as an evidence gap, not as healthy." in prompt
    assert "No pending canonical action." in prompt
    assert "No additional focus supplied." in prompt


def test_maintenance_v2_delta_becomes_verify_only_when_nothing_material_changed():
    data = {"objectives": [], "jobs": [], "commands": []}
    control = {
        "state": "HEALTHY",
        "observed_at": "2026-10-06T08:00:00+00:00",
        "topology": {"inventory_complete": True, "services": []},
        "incidents": {},
    }
    first = process_command(
        "maintenance prompt",
        data,
        control,
        requested_by="devon",
        source="command",
    )
    data["commands"] = [{
        "command_id": "prior",
        "status": "SUCCEEDED",
        "result": first.response,
    }]
    control["observed_at"] = "2026-10-06T08:05:00+00:00"

    second = process_command(
        "maintenance prompt",
        data,
        control,
        requested_by="devon",
        source="command",
    )

    manifest = second.response["maintenance_manifest"]
    assert manifest["mode"] == "VERIFY_ONLY"
    assert manifest["change_count"] == 0
    assert manifest["changed_since_previous"] is False
    assert "No material state delta" in second.response["maintenance_prompt"]
    assert "read-only verification" in second.response["maintenance_prompt"]


def test_maintenance_v2_detects_research_change_and_selects_research_mode():
    data = {"objectives": [], "jobs": [], "commands": []}
    control = {
        "state": "HEALTHY",
        "observed_at": "2026-10-06T08:00:00+00:00",
        "topology": {"inventory_complete": True, "services": []},
        "incidents": {},
    }
    first = process_command(
        "maintenance prompt",
        data,
        control,
        requested_by="devon",
        source="command",
    )
    data["commands"] = [{
        "command_id": "prior",
        "status": "SUCCEEDED",
        "result": first.response,
    }]
    data["maintenance_evidence"] = {
        "graen_problems": [{
            "problem_id": "g-1",
            "title": "BTC entry selectivity",
            "status": "RUNNING",
            "research_stage": "DEVELOPMENT",
            "candidate_id": "btc-v2",
            "family": "momentum",
        }],
        "graen_runs": [],
        "velum_replays": [],
        "nostra_calibrations": [],
        "nostra_forecasts": [],
        "strategy_activity_24h": [],
    }

    second = process_command(
        "maintenance prompt",
        data,
        control,
        requested_by="devon",
        source="command",
    )

    manifest = second.response["maintenance_manifest"]
    assert manifest["mode"] == "RESEARCH"
    assert manifest["change_count"] >= 1
    assert any("GRAEN problem" in item for item in manifest["changes"])
    prompt = second.response["maintenance_prompt"]
    assert "BTC entry selectivity" in prompt
    assert "advance one evidence chain" in prompt
    assert "do not rewrite its methodology or strategy mid-run" in prompt


def test_maintenance_v2_prioritizes_evidence_repair_over_research_activity():
    data = {
        "objectives": [],
        "jobs": [],
        "commands": [],
        "maintenance_evidence": {
            "graen_problems": [{
                "problem_id": "g-1",
                "title": "BTC research",
                "status": "RUNNING",
                "research_stage": "VALIDATION",
            }],
        },
    }
    result = process_command(
        "maintenance prompt",
        data,
        {
            "state": "STALE",
            "observed_at": "2026-10-06T08:00:00+00:00",
            "topology": {"inventory_complete": False, "services": []},
            "incidents": {},
        },
        requested_by="devon",
        source="command",
    )

    assert result.response["maintenance_manifest"]["mode"] == "EVIDENCE_REPAIR"
    assert result.response["maintenance_manifest"]["budget"]["parallel_research_threads"] == 0


def test_maintenance_v2_tracks_strategy_activity_without_forcing_live_promotion():
    data = {
        "objectives": [],
        "jobs": [],
        "commands": [],
        "maintenance_evidence": {
            "strategy_activity_24h": [{
                "strategy_version_id": "BTC-CANARY-001",
                "decision_cycles": 144,
                "fills": 3,
                "runtime_errors": 0,
                "run_count": 1,
                "last_event_at": "2026-10-06T08:00:00+00:00",
            }],
        },
    }
    result = process_command(
        "maintenance prompt",
        data,
        {
            "state": "HEALTHY",
            "topology": {"inventory_complete": True, "services": []},
            "incidents": {},
        },
        requested_by="devon",
        source="command",
    )

    prompt = result.response["maintenance_prompt"]
    assert "BTC-CANARY-001 | decisions=144 | fills=3 | errors=0 | runs=1" in prompt
    assert "Never overwrite a working strategy in place." in prompt
    assert "Do not silently expand broker-write authority" in prompt
    assert "Paper/forward/canary evidence must remain distinct from live performance." in prompt


def test_next_action_uses_ready_dependency_satisfied_objective():
    action = choose_next_action(snapshot())
    assert action["objective_key"] == "COMMAND"
    assert action["owner_system"] == "IREN"
    assert action["job_type"] == "SOFTWARE_BUILD"


def test_existing_active_job_prevents_duplicate_next_action():
    data = snapshot()
    data["jobs"].append({"objective_key": "COMMAND", "status": "WAITING"})
    action = choose_next_action(data)
    assert action["objective_key"] == "GRAEN"


def test_do_that_routes_software_work_to_manual_codex():
    result = process_command(
        "do that",
        snapshot(),
        {"state": "HEALTHY"},
        requested_by="devon",
        source="command",
    )
    assert result.intent == "EXECUTE_NEXT"
    assert result.job is None
    assert result.response["execution_mode"] == "codex/manual software"
    assert result.response["message"] == "Software work requires a manual Codex handoff. Nothing was queued."


def test_protected_objective_requires_approval():
    data = snapshot()
    data["objectives"][2]["protected_action"] = True
    result = process_command(
        "do that",
        data,
        {"state": "HEALTHY"},
        requested_by="devon",
        source="command",
    )
    assert result.job["status"] == "NEEDS_APPROVAL"
    assert result.job["requires_human"] is True


def test_freeform_directive_fails_closed_without_model_worker():
    result = process_command(
        "Investigate the evidence gap",
        snapshot(),
        {"state": "HEALTHY"},
        requested_by="devon",
        source="slack",
    )
    assert result.intent == "DIRECTIVE"
    assert result.job is None
    assert result.response["message"] == "Unsupported control."
    assert "prepare for Codex" in result.response["supported_actions"]


def test_current_iren_status_is_read_only_and_creates_no_job():
    result = process_command(
        "Current IREN status",
        snapshot(),
        {"state": "DEGRADED"},
        requested_by="devon",
        source="command",
    )

    assert result.intent == "STATUS"
    assert result.job is None
    assert result.response["message"].startswith("IREN is DEGRADED.")
    assert "Next:" in result.response["message"]


def test_status_message_reports_state_and_next_action():
    data = snapshot()
    summary = status_summary(
        data,
        {
            "state": "DEGRADED",
            "incidents": {
                "evidence.loss": {
                    "status": "OPEN",
                    "severity": "warning",
                    "reason": "runtime_event_loss_increasing",
                }
            },
        },
    )

    assert summary["control_state"] == "DEGRADED"
    assert summary["open_incidents"][0]["key"] == "evidence.loss"
    assert summary["next_action"]["title"] == "Verify evidence.loss"
    assert "IREN is DEGRADED." in summary["message"]
    assert "Next: Verify evidence.loss." in summary["message"]


def test_active_objective_is_actionable_when_no_ready_objective_exists():
    data = snapshot()
    data["objectives"][2]["status"] = "COMPLETE"
    data["objectives"][3]["status"] = "COMPLETE"
    data["objectives"].append({
        "objective_key": "ACTIVE-ONE",
        "title": "Continue active work",
        "description": "Keep moving.",
        "status": "ACTIVE",
        "priority": 80,
        "dependencies": [],
        "owner_system": "IREN",
        "protected_action": False,
        "success_criteria": {},
        "metadata": {},
    })

    action = choose_next_action(data, {"state": "HEALTHY", "incidents": {}})

    assert action["objective_key"] == "ACTIVE-ONE"
    assert action["job_type"] == "CONTROL_RECONCILE"
    assert action["reason"] == "highest_priority_active_objective"


def test_continuous_planner_returns_idle_when_no_real_work_exists():
    data = {"objectives": [], "jobs": []}
    action = choose_next_action(data, {"state": "HEALTHY", "incidents": {}})
    assert action is None


def test_execute_next_is_noop_when_no_real_work_exists():
    result = process_command(
        "do that",
        {"objectives": [], "jobs": []},
        {"state": "HEALTHY", "incidents": {}},
        requested_by="devon",
        source="command",
    )
    assert result.job is None
    assert result.response["execution_mode"] == "idle"
    assert result.response["action_taken"] is False
    assert result.response["message"] == "No pending safe action. Nothing executed."


def test_next_is_read_only_for_software_objective():
    result = process_command(
        "what's next?",
        snapshot(),
        {"state": "HEALTHY", "incidents": {}},
        requested_by="devon",
        source="command",
    )
    assert result.job is None
    assert result.response["execution_mode"] == "codex/manual software"
    assert result.response["action_taken"] is False
    assert result.response["next_action"]["objective_key"] == "COMMAND"


def test_status_command_returns_substantive_summary():
    result = process_command(
        "status",
        snapshot(),
        {"state": "HEALTHY", "incidents": {}},
        requested_by="devon",
        source="command",
    )
    assert result.intent == "STATUS"
    assert result.job is None
    assert result.response["message"].startswith("IREN is HEALTHY.")
    assert "Next:" in result.response["message"]


def test_autopilot_disabled_does_nothing():
    data = snapshot()
    data["settings"] = {"autopilot_enabled": False, "autopilot_max_jobs_per_day": 3}
    decision = autopilot_decision(data, {"state": "HEALTHY", "incidents": {}})
    assert decision["should_create"] is False
    assert decision["reason"] == "autopilot_disabled"


def test_autopilot_creates_only_safe_control_reconcile_work():
    data = snapshot()
    data["objectives"][2]["status"] = "COMPLETE"
    data["objectives"][3]["status"] = "COMPLETE"
    data["objectives"].append({
        "objective_key": "ACTIVE-SAFE",
        "title": "Reconcile stable build",
        "description": "Verify and advance stable build.",
        "status": "ACTIVE",
        "priority": 120,
        "dependencies": [],
        "owner_system": "IREN",
        "protected_action": False,
        "success_criteria": {},
        "metadata": {"job_type": "CONTROL_RECONCILE"},
    })
    data["settings"] = {"autopilot_enabled": True, "autopilot_max_jobs_per_day": 3}

    decision = autopilot_decision(data, {"state": "HEALTHY", "incidents": {}})

    assert decision["should_create"] is True
    assert decision["reason"] == "safe_action_ready"
    assert decision["action"]["job_type"] == "CONTROL_RECONCILE"
    assert len(decision["action_signature"]) == 64


def test_autopilot_refuses_model_backed_software_build():
    data = snapshot()
    data["settings"] = {"autopilot_enabled": True, "autopilot_max_jobs_per_day": 3}
    decision = autopilot_decision(data, {"state": "HEALTHY", "incidents": {}})
    assert decision["should_create"] is False
    assert decision["reason"] == "executor_capability_required"
    assert decision["action"]["job_type"] == "SOFTWARE_BUILD"


def test_autopilot_does_not_repeat_same_completed_action():
    data = snapshot()
    data["objectives"][2]["status"] = "COMPLETE"
    data["objectives"][3]["status"] = "COMPLETE"
    data["objectives"].append({
        "objective_key": "ACTIVE-SAFE",
        "title": "Reconcile stable build",
        "description": "Verify and advance stable build.",
        "status": "ACTIVE",
        "priority": 120,
        "dependencies": [],
        "owner_system": "IREN",
        "protected_action": False,
        "success_criteria": {},
        "metadata": {"job_type": "CONTROL_RECONCILE"},
    })
    data["settings"] = {"autopilot_enabled": True, "autopilot_max_jobs_per_day": 3}
    control = {"state": "HEALTHY", "incidents": {}, "observed_at": "2026-10-02T03:00:00+00:00"}
    action = choose_next_action(data, control)
    sig = action_signature(action, control)
    data["jobs"].append({
        "job_id": "auto-1",
        "status": "SUCCEEDED",
        "requested_via": "autopilot",
        "created_at": "2026-10-02T03:01:00+00:00",
        "metadata": {"action_signature": sig},
    })

    decision = autopilot_decision(
        data,
        control,
        now=__import__("datetime").datetime(2026, 10, 2, 4, 0, tzinfo=__import__("datetime").timezone.utc),
    )
    assert decision["should_create"] is False
    assert decision["reason"] == "no_action"
    assert "Reconcile stable build" in decision["skipped_actions"]


def test_autopilot_honors_daily_cap():
    data = snapshot()
    data["objectives"][2]["status"] = "COMPLETE"
    data["objectives"][3]["status"] = "COMPLETE"
    data["settings"] = {"autopilot_enabled": True, "autopilot_max_jobs_per_day": 2}
    data["jobs"] = [
        {"status": "SUCCEEDED", "requested_via": "autopilot", "created_at": "2026-10-02T05:00:00+00:00"},
        {"status": "SUCCEEDED", "requested_via": "autopilot", "created_at": "2026-10-02T06:00:00+00:00"},
    ]
    decision = autopilot_decision(
        data,
        {"state": "HEALTHY", "incidents": {}},
        now=__import__("datetime").datetime(2026, 10, 2, 8, 0, tzinfo=__import__("datetime").timezone.utc),
    )
    assert decision["should_create"] is False
    assert decision["reason"] == "autopilot_daily_cap_reached"


def test_open_incident_maps_to_control_verify():
    data = snapshot()
    action = choose_next_action(
        data,
        {
            "state": "DEGRADED",
            "incidents": {
                "scheduler.health": {
                    "status": "OPEN",
                    "severity": "warning",
                    "reason": "canonical_scheduler_degraded",
                }
            },
        },
    )
    assert action["job_type"] == "CONTROL_VERIFY"
    assert action["incident"]["key"] == "scheduler.health"


def test_control_verify_is_autopilot_safe():
    data = snapshot()
    data["settings"] = {"autopilot_enabled": True, "autopilot_max_jobs_per_day": 3}
    decision = autopilot_decision(
        data,
        {
            "state": "DEGRADED",
            "incidents": {
                "evidence.stale": {
                    "status": "OPEN",
                    "severity": "warning",
                    "reason": "evidence_delivery_timestamp_stale",
                }
            },
        },
    )
    assert decision["should_create"] is True
    assert decision["action"]["job_type"] == "CONTROL_VERIFY"


def test_criteria_verifier_is_subset_based_and_fail_closed():
    assert criteria_satisfied(
        {"a": True, "nested": {"b": 3}},
        {"a": True, "nested": {"b": 3, "extra": 4}, "other": 1},
    )
    assert not criteria_satisfied({"a": True}, {"a": False})
    assert not criteria_satisfied({}, {})
    assert not criteria_satisfied({"missing": True}, {})


def test_dependencies_complete_requires_all_dependencies():
    objectives = [
        {"objective_key": "A", "status": "COMPLETE"},
        {"objective_key": "B", "status": "READY"},
    ]
    assert dependencies_complete({"dependencies": ["A"]}, objectives)
    assert not dependencies_complete({"dependencies": ["A", "B"]}, objectives)


def test_autopilot_skips_already_verified_open_incident_and_advances():
    data = snapshot()
    data["objectives"][2]["status"] = "COMPLETE"
    data["objectives"][3]["status"] = "COMPLETE"
    data["objectives"].append({
        "objective_key": "CAPS",
        "title": "Verify capabilities",
        "description": "Verify deterministic capabilities.",
        "status": "READY",
        "priority": 120,
        "dependencies": [],
        "owner_system": "IREN",
        "protected_action": False,
        "success_criteria": {"safe_executor_registry": True},
        "metadata": {"job_type": "CONTROL_CAPABILITIES"},
    })
    data["settings"] = {"autopilot_enabled": True, "autopilot_max_jobs_per_day": 6}
    control = {
        "state": "DEGRADED",
        "incidents": {
            "workflow.rhen.session_close": {
                "status": "OPEN",
                "severity": "warning",
                "reason": "current_workflow_failed",
            }
        },
    }
    incident_action = choose_next_action(data, control)
    data["jobs"].append({
        "job_id": "verify-incident",
        "status": "SUCCEEDED",
        "requested_via": "autopilot",
        "created_at": "2026-10-02T10:00:00+00:00",
        "metadata": {
            "action_signature": action_signature(incident_action, control),
        },
    })

    decision = autopilot_decision(
        data,
        control,
        now=__import__("datetime").datetime(
            2026, 10, 2, 11, 0,
            tzinfo=__import__("datetime").timezone.utc,
        ),
    )
    assert decision["should_create"] is True
    assert decision["action"]["objective_key"] == "CAPS"
    assert "Verify workflow.rhen.session_close" in decision["skipped_actions"]


def test_autopilot_daily_cap_uses_new_york_business_day():
    data = {"objectives": [], "jobs": [], "settings": {
        "autopilot_enabled": True,
        "autopilot_max_jobs_per_day": 1,
    }}
    data["jobs"] = [{
        "status": "SUCCEEDED",
        "requested_via": "autopilot",
        "created_at": "2026-10-02T00:30:00+00:00",
        "metadata": {},
    }]
    decision = autopilot_decision(
        data,
        {"state": "HEALTHY", "incidents": {}},
        now=__import__("datetime").datetime(
            2026, 10, 2, 5, 0,
            tzinfo=__import__("datetime").timezone.utc,
        ),
    )
    assert decision["reason"] != "autopilot_daily_cap_reached"


def test_command_processor_keeps_read_only_next_separate_from_handoff_creation():
    import inspect
    from app.iren.work import IrenWorkEngine

    source = inspect.getsource(IrenWorkEngine._process_commands)
    assert 'elif result.intent == "CODEX_HANDOFF":' in source
    assert 'result.intent in {"CODEX_HANDOFF", "NEXT", "EXECUTE_NEXT"}' not in source
    assert '"No active Codex handoff to verify."' in source


def test_runtime_evidence_criteria_requires_complete_inventory():
    assert runtime_evidence_criteria({
        "topology": {"inventory_complete": True, "inventory_gaps": {}}
    }) == {"complete_deployment_inventory": True}
    assert runtime_evidence_criteria({
        "topology": {"inventory_complete": False, "inventory_gaps": {"VELUM": ["deployment"]}}
    }) == {"complete_deployment_inventory": False}
    assert runtime_evidence_criteria({}) == {"complete_deployment_inventory": False}


def test_runtime_evidence_verifier_retries_until_inventory_complete():
    import inspect
    from app.iren.work import IrenWorkEngine

    source = inspect.getsource(IrenWorkEngine._execute_jobs)
    assert '"SUCCEEDED" if inventory_complete else "QUEUED"' in source
    assert "retrying=not inventory_complete" in source


def test_work_prompt_stops_at_research_review_boundary_instead_of_restarting_exhausted_program():
    data = {
        "objectives": [],
        "jobs": [],
        "commands": [],
        "maintenance_evidence": {
            "research_control": {
                "schema_version": "research_control.v1",
                "mode": "RESEARCH_REVIEW_REQUIRED",
                "reason": (
                    "The bounded hypothesis family is exhausted. Do not restart it. "
                    "A materially new hypothesis family requires a deliberate Work/Codex pass."
                ),
                "review_required": True,
                "review_kind": "NEW_HYPOTHESIS_FAMILY",
                "work_credit_recommended": True,
                "problem_id": "g-exhausted",
                "stage": "ADAPTIVE_PROGRAM_EXHAUSTED",
                "decision": "NEEDS_NEW_HYPOTHESIS_ENGINE",
                "next_action": "MODEL_HYPOTHESIS_GENERATION_REQUIRED",
                "rejected_generations": 12,
            },
            "graen_problems": [{
                "problem_id": "g-exhausted",
                "title": "Adaptive acceleration",
                "status": "WAITING",
                "research_stage": "ADAPTIVE_PROGRAM_EXHAUSTED",
                "candidate_id": "CRYPTO-ACCEL-ADAPTIVE-012",
                "family": "cross_sectional_acceleration",
            }],
            "graen_runs": [],
            "velum_replays": [],
            "nostra_calibrations": [],
            "nostra_forecasts": [],
            "strategy_activity_24h": [],
        },
    }
    control = {
        "state": "HEALTHY",
        "observed_at": "2026-10-06T17:00:00+00:00",
        "topology": {"inventory_complete": True, "services": []},
        "incidents": {},
    }

    result = process_command(
        "maintenance prompt",
        data,
        control,
        requested_by="devon",
        source="command",
    )

    manifest = result.response["maintenance_manifest"]
    prompt = result.response["maintenance_prompt"]
    assert manifest["mode"] == "RESEARCH"
    assert "NEW_HYPOTHESIS_FAMILY" in manifest["driver"]
    assert manifest["state"]["research_control"]["mode"] == "RESEARCH_REVIEW_REQUIRED"
    assert "Research control:" in prompt
    assert "RESEARCH_REVIEW_REQUIRED" in prompt
    assert "MODEL_HYPOTHESIS_GENERATION_REQUIRED" in prompt
    assert "do not restart the exhausted program" in prompt
    assert "parameter shuffling inside the rejected family does not satisfy" in prompt
