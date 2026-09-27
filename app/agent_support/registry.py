from __future__ import annotations

import json
from pathlib import Path

REGISTRY_PATH = Path(__file__).with_name("registry.json")
REQUIRED = {
    "agent_id", "canonical_name", "version", "role", "lifecycle_status",
    "authority_boundary", "allowed_capabilities", "forbidden_capabilities",
    "permitted_trigger_types", "automatic_invocation_allowed", "owning_subsystem",
    "expected_inputs", "expected_outputs", "escalation_behavior",
}


def load_registry(path: Path = REGISTRY_PATH) -> dict:
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("schema_version") != "rhen-agent-registry-v1":
        raise ValueError("unsupported registry schema")
    agents = document.get("agents")
    if not isinstance(agents, list) or len(agents) != 3:
        raise ValueError("registry must contain exactly three initial roles")
    ids = set()
    roles = set()
    for agent in agents:
        if not isinstance(agent, dict) or REQUIRED - agent.keys():
            raise ValueError("incomplete registry entry")
        for key in REQUIRED - {"automatic_invocation_allowed"}:
            value = agent[key]
            if not isinstance(value, (str, list)) or not value:
                raise ValueError(f"invalid registry {key}")
            if isinstance(value, list) and not all(isinstance(v, str) and v for v in value):
                raise ValueError(f"invalid registry {key}")
        if not isinstance(agent["automatic_invocation_allowed"], bool):
            raise ValueError("automatic invocation must be boolean")
        ids.add(agent["agent_id"])
        roles.add(agent["role"])
        if set(agent["allowed_capabilities"]) & set(agent["forbidden_capabilities"]):
            raise ValueError("capability cannot be both allowed and forbidden")
    if ids != {"rhen.research.v1", "rhen.coordinator.v1", "rhen.verifier.v1"} or len(roles) != 3:
        raise ValueError("initial agent identity drift")
    return document
