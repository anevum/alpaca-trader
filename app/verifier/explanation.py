"""A model-facing rendering that has no authority to edit deterministic findings."""
from __future__ import annotations

from typing import Any, Mapping

from .models import VerificationResult


def explanation_context(result: VerificationResult, interpretation: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Only free text is accepted downstream; verdict and severities stay sealed."""
    interpretation = interpretation or {}
    note = interpretation.get("note", "")
    if not isinstance(note, str):
        note = ""
    return {"verification": result.as_dict(), "optional_explanation": note[:1000]}
