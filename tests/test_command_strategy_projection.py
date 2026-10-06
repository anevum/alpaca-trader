from pathlib import Path


def test_command_strategy_projection_preserves_evidence_readiness():
    source = Path("app/main.py").read_text()

    assert 'research_payload.get("evidence_readiness")' in source
    assert '"evidence_readiness": evidence_readiness' in source
