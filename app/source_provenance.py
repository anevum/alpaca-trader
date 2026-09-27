from __future__ import annotations

import os
import re
from collections.abc import Mapping


_FULL_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")


def resolve_source_commit(environ: Mapping[str, str] | None = None) -> str:
    """Return an explicit, validated source commit without inventing provenance.

    Railway's deployment-provided commit is authoritative when present. The
    RDR21-specific value remains available for controlled non-Railway runs, but
    there is deliberately no hard-coded historical fallback.
    """

    values = os.environ if environ is None else environ
    for variable in ("RAILWAY_GIT_COMMIT_SHA", "RDR21_SOURCE_COMMIT"):
        raw = values.get(variable)
        if raw is None or not raw.strip():
            continue
        commit = raw.strip().lower()
        if not _FULL_GIT_SHA.fullmatch(commit):
            raise ValueError(f"{variable} must be a full 40-character Git commit SHA")
        return commit

    raise RuntimeError(
        "source commit is unavailable; set RAILWAY_GIT_COMMIT_SHA "
        "or explicitly set RDR21_SOURCE_COMMIT"
    )
