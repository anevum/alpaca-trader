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
    assert normalize_command("build the next thing") == "DIRECTIVE"


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


def test_do_that_creates_durable_job_spec():
    result = process_command(
        "do that",
        snapshot(),
        {"state": "HEALTHY"},
        requested_by="devon",
        source="command",
    )
    assert result.intent == "EXECUTE_NEXT"
    assert result.job["objective_key"] == "COMMAND"
    assert result.job["status"] == "QUEUED"
    assert result.job["requested_via"] == "command"


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


def test_freeform_directive_becomes_iren_triage_job():
    result = process_command(
        "Investigate the evidence gap",
        snapshot(),
        {"state": "HEALTHY"},
        requested_by="devon",
        source="slack",
    )
    assert result.intent == "DIRECTIVE"
    assert result.job["owner_system"] == "IREN"
    assert result.job["status"] == "QUEUED"


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
    assert summary["next_action"]["title"] == "Resolve evidence.loss"
    assert "IREN is DEGRADED." in summary["message"]
    assert "Next: Resolve evidence.loss." in summary["message"]


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


def test_continuous_planner_always_returns_fallback():
    data = {"objectives": [], "jobs": []}
    action = choose_next_action(data, {"state": "HEALTHY", "incidents": {}})
    assert action["job_type"] == "CONTROL_RECONCILE"
    assert action["title"] == "Reconcile system and derive next objective"


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


def test_autopilot_skips_manual_software_and_continues_independent_research():
    data = snapshot()
    data["objectives"].append({
        "objective_key": "RESEARCH",
        "title": "Continue crypto edge discovery",
        "description": "Run the next bounded GRAEN research problem.",
        "status": "READY",
        "priority": 80,
        "dependencies": ["ENGINE"],
        "owner_system": "GRAEN",
        "protected_action": False,
        "success_criteria": {"research_progress_restored": True},
        "metadata": {"job_type": "GRAEN_RESEARCH_PROBLEM"},
    })
    data["settings"] = {"autopilot_enabled": True, "autopilot_max_jobs_per_day": 3}
    decision = autopilot_decision(data, {"state": "HEALTHY", "incidents": {}})
    assert decision["should_create"] is True
    assert decision["action"]["job_type"] == "GRAEN_RESEARCH_PROBLEM"
    assert any("manual software" in row for row in decision["skipped_actions"])


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
    assert decision["reason"] == "same_action_already_attempted"


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
    assert "Resolve workflow.rhen.session_close" in decision["skipped_actions"]


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


def test_productivity_incident_routes_to_autonomous_graen_research():
    data = {"objectives": [], "jobs": []}
    control = {
        "state": "DEGRADED",
        "incidents": {
            "productivity.GRAEN": {
                "status": "OPEN",
                "severity": "warning",
                "reason": "autonomous_research_not_making_progress",
            }
        },
    }
    action = choose_next_action(data, control)
    assert action["job_type"] == "GRAEN_RESEARCH_PROBLEM"
    assert action["owner_system"] == "GRAEN"
    assert action["protected_action"] is False
    assert "do not repeat terminal hypotheses" in action["description"]


def test_autopilot_may_restart_safe_graen_research():
    data = {
        "objectives": [],
        "jobs": [],
        "settings": {
            "autopilot_enabled": True,
            "autopilot_max_jobs_per_day": 3,
        },
    }
    control = {
        "state": "DEGRADED",
        "incidents": {
            "productivity.GRAEN": {
                "status": "OPEN",
                "severity": "warning",
                "reason": "autonomous_research_not_making_progress",
            }
        },
    }
    decision = autopilot_decision(data, control)
    assert decision["should_create"] is True
    assert decision["action"]["job_type"] == "GRAEN_RESEARCH_PROBLEM"
