from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Mapping

from .autonomy import standing_freeze_authorization, standing_stage_authorization


class AuthorizationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class FreezeAuthorization:
    decision_reference: str
    proposal_id: str
    proposal_revision: int
    proposal_hash: str
    authorized_by: str
    authorized_at: str


@dataclass(frozen=True, slots=True)
class StageAuthorization:
    decision_reference: str
    experiment_id: str
    experiment_key: str
    stage: str
    manifest_hash: str
    source_commit: str
    authorized_by: str
    authorized_at: str


def _nonempty(payload: Mapping[str, Any], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip():
        raise AuthorizationError(f"authorization field {name} is required")
    return value.strip()


def _timestamp(payload: Mapping[str, Any], name: str) -> str:
    value = _nonempty(payload, name)
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AuthorizationError(f"authorization field {name} is invalid") from exc
    return value


def _eligible_decisions(
    decisions: Iterable[Mapping[str, Any]],
    *,
    authorized_action: str,
) -> tuple[tuple[str, Mapping[str, Any]], ...]:
    eligible: list[tuple[str, Mapping[str, Any]]] = []
    for decision in decisions:
        if str(decision.get("status", "")).strip().casefold() != "final":
            continue
        if decision.get("superseded_by_decision_id") is not None:
            continue
        if str(decision.get("decision_type", "")).strip().casefold() != "research_authorization":
            continue
        evidence = decision.get("evidence")
        if not isinstance(evidence, Mapping):
            continue
        if evidence.get("revoked") is True:
            continue
        if evidence.get("authorized_action") != authorized_action:
            continue
        reference = str(
            decision.get("decision_key") or decision.get("decision_id") or ""
        ).strip()
        if reference:
            eligible.append((reference, evidence))
    return tuple(eligible)


def _one_exact(matches: list[Any], action: str) -> Any:
    if not matches:
        raise AuthorizationError(f"no exact final authorization for {action}")
    if len(matches) != 1:
        raise AuthorizationError(f"ambiguous final authorization for {action}")
    return matches[0]


def _explicit_override(
    decisions: Iterable[Mapping[str, Any]],
    *,
    authorized_action: str,
    identity: Mapping[str, Any],
) -> bool:
    """Return true only for a relevant explicit override.

    A final revocation with no identity fields is intentionally global. Otherwise
    an operator decision outranks the standing charter only when every identity
    field it supplies matches the current proposal/experiment. This prevents an
    old authorization for experiment A from disabling autonomous experiment B.
    """
    identity_keys = set(identity)
    for decision in decisions:
        if str(decision.get("status", "")).strip().casefold() != "final":
            continue
        if str(decision.get("decision_type", "")).strip().casefold() != "research_authorization":
            continue
        evidence = decision.get("evidence")
        if not isinstance(evidence, Mapping):
            continue
        if evidence.get("authorized_action") != authorized_action:
            continue

        supplied = {
            key: evidence.get(key)
            for key in identity_keys
            if evidence.get(key) not in (None, "")
        }
        if not supplied:
            return evidence.get("revoked") is True
        if all(str(supplied[key]) == str(identity.get(key)) for key in supplied):
            return True
    return False


def authorize_freeze(
    decisions: Iterable[Mapping[str, Any]],
    *,
    proposal_id: str,
    proposal_revision: int,
    proposal_hash: str,
    allow_standing_charter: bool = False,
) -> FreezeAuthorization:
    decision_rows = tuple(decisions)
    matches: list[FreezeAuthorization] = []
    for reference, payload in _eligible_decisions(
        decision_rows, authorized_action="freeze_methodology"
    ):
        try:
            revision = payload.get("proposal_revision")
            if not isinstance(revision, int) or isinstance(revision, bool):
                raise AuthorizationError(
                    "authorization field proposal_revision is required"
                )
            authorization = FreezeAuthorization(
                decision_reference=reference,
                proposal_id=_nonempty(payload, "proposal_id"),
                proposal_revision=revision,
                proposal_hash=_nonempty(payload, "proposal_hash"),
                authorized_by=_nonempty(payload, "authorized_by"),
                authorized_at=_timestamp(payload, "authorized_at"),
            )
        except AuthorizationError:
            continue
        if (
            authorization.proposal_id == proposal_id
            and authorization.proposal_revision == proposal_revision
            and authorization.proposal_hash == proposal_hash
        ):
            matches.append(authorization)
    if matches:
        return _one_exact(matches, "freeze_methodology")
    if allow_standing_charter and not _explicit_override(
        decision_rows,
        authorized_action="freeze_methodology",
        identity={
            "proposal_id": proposal_id,
            "proposal_revision": proposal_revision,
            "proposal_hash": proposal_hash,
        },
    ):
        payload = standing_freeze_authorization(
            proposal_id=proposal_id,
            proposal_revision=proposal_revision,
            proposal_hash=proposal_hash,
        )
        return FreezeAuthorization(
            decision_reference=str(payload["decision_reference"]),
            proposal_id=str(payload["proposal_id"]),
            proposal_revision=int(payload["proposal_revision"]),
            proposal_hash=str(payload["proposal_hash"]),
            authorized_by=str(payload["authorized_by"]),
            authorized_at=str(payload["authorized_at"]),
        )
    return _one_exact(matches, "freeze_methodology")


def authorize_stage(
    decisions: Iterable[Mapping[str, Any]],
    *,
    experiment_id: str,
    experiment_key: str,
    stage: str,
    manifest_hash: str,
    source_commit: str,
    allow_standing_charter: bool = False,
) -> StageAuthorization:
    expected_stage = stage.strip().casefold()
    if expected_stage not in {"development", "validation", "holdout"}:
        raise AuthorizationError(f"unsupported protected stage: {stage}")
    decision_rows = tuple(decisions)
    matches: list[StageAuthorization] = []
    for reference, payload in _eligible_decisions(
        decision_rows, authorized_action="open_stage"
    ):
        try:
            authorization = StageAuthorization(
                decision_reference=reference,
                experiment_id=_nonempty(payload, "experiment_id"),
                experiment_key=_nonempty(payload, "experiment_key"),
                stage=_nonempty(payload, "stage").casefold(),
                manifest_hash=_nonempty(payload, "manifest_hash"),
                source_commit=_nonempty(payload, "source_commit"),
                authorized_by=_nonempty(payload, "authorized_by"),
                authorized_at=_timestamp(payload, "authorized_at"),
            )
        except AuthorizationError:
            continue
        if (
            authorization.experiment_id == experiment_id
            and authorization.experiment_key == experiment_key
            and authorization.stage == expected_stage
            and authorization.manifest_hash == manifest_hash
            and authorization.source_commit == source_commit
        ):
            matches.append(authorization)
    if matches:
        return _one_exact(matches, f"open_stage:{expected_stage}")
    if allow_standing_charter and not _explicit_override(
        decision_rows,
        authorized_action="open_stage",
        identity={
            "experiment_id": experiment_id,
            "experiment_key": experiment_key,
            "stage": expected_stage,
            "manifest_hash": manifest_hash,
            "source_commit": source_commit,
        },
    ):
        payload = standing_stage_authorization(
            experiment_id=experiment_id,
            experiment_key=experiment_key,
            stage=expected_stage,
            manifest_hash=manifest_hash,
            source_commit=source_commit,
        )
        return StageAuthorization(
            decision_reference=str(payload["decision_reference"]),
            experiment_id=str(payload["experiment_id"]),
            experiment_key=str(payload["experiment_key"]),
            stage=str(payload["stage"]),
            manifest_hash=str(payload["manifest_hash"]),
            source_commit=str(payload["source_commit"]),
            authorized_by=str(payload["authorized_by"]),
            authorized_at=str(payload["authorized_at"]),
        )
    return _one_exact(matches, f"open_stage:{expected_stage}")


def transition_authorization(authorization: StageAuthorization) -> Any:
    from .state_machine import TransitionAuthorization

    return TransitionAuthorization(
        reference=authorization.decision_reference,
        experiment_id=authorization.experiment_id,
        experiment_key=authorization.experiment_key,
        stage=authorization.stage,
        manifest_hash=authorization.manifest_hash,
        source_commit=authorization.source_commit,
        authorized_by=authorization.authorized_by,
        authorized_at=authorization.authorized_at,
    )


def authorize_freeze_with_charter(
    decisions: Iterable[Mapping[str, Any]],
    *,
    proposal_id: str,
    proposal_revision: int,
    proposal_hash: str,
    allow_standing_charter: bool = False,
) -> FreezeAuthorization:
    """Resolve exact operator authorization, or the standing research charter.

    Ambiguous/revoked explicit authorizations never fall back to the charter.
    The charter grants research-stage authority only; it has no production,
    broker, spending, credential, or source-code authority.
    """
    try:
        return authorize_freeze(
            decisions,
            proposal_id=proposal_id,
            proposal_revision=proposal_revision,
            proposal_hash=proposal_hash,
        )
    except AuthorizationError as exc:
        if not allow_standing_charter or not str(exc).startswith(
            "no exact final authorization"
        ):
            raise

    from .autonomy import standing_freeze_authorization

    payload = standing_freeze_authorization(
        proposal_id=proposal_id,
        proposal_revision=proposal_revision,
        proposal_hash=proposal_hash,
    )
    return FreezeAuthorization(
        decision_reference=str(payload["decision_reference"]),
        proposal_id=str(payload["proposal_id"]),
        proposal_revision=int(payload["proposal_revision"]),
        proposal_hash=str(payload["proposal_hash"]),
        authorized_by=str(payload["authorized_by"]),
        authorized_at=str(payload["authorized_at"]),
    )


def authorize_stage_with_charter(
    decisions: Iterable[Mapping[str, Any]],
    *,
    experiment_id: str,
    experiment_key: str,
    stage: str,
    manifest_hash: str,
    source_commit: str,
    allow_standing_charter: bool = False,
) -> StageAuthorization:
    """Resolve exact stage authorization without weakening deterministic gates."""
    try:
        return authorize_stage(
            decisions,
            experiment_id=experiment_id,
            experiment_key=experiment_key,
            stage=stage,
            manifest_hash=manifest_hash,
            source_commit=source_commit,
        )
    except AuthorizationError as exc:
        if not allow_standing_charter or not str(exc).startswith(
            "no exact final authorization"
        ):
            raise

    from .autonomy import standing_stage_authorization

    payload = standing_stage_authorization(
        experiment_id=experiment_id,
        experiment_key=experiment_key,
        stage=stage,
        manifest_hash=manifest_hash,
        source_commit=source_commit,
    )
    return StageAuthorization(
        decision_reference=str(payload["decision_reference"]),
        experiment_id=str(payload["experiment_id"]),
        experiment_key=str(payload["experiment_key"]),
        stage=str(payload["stage"]),
        manifest_hash=str(payload["manifest_hash"]),
        source_commit=str(payload["source_commit"]),
        authorized_by=str(payload["authorized_by"]),
        authorized_at=str(payload["authorized_at"]),
    )
