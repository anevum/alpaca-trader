from app.iren.work import choose_next_action, normalize_command, process_command


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
