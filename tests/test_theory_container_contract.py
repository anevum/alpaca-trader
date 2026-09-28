from pathlib import Path


def test_research_agent_image_includes_canonical_theory_registry():
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
    assert "COPY research/theory_registry.json ./research/theory_registry.json" in dockerfile
