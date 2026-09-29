# ASC-003 Control State Machine

Status: SHADOW / READ-ONLY

ASC-003 turns ASC strategy-health recommendations into a deterministic
supervisory state with hysteresis.

States:

- NORMAL
- ADAPT
- RESEARCH
- DEFENSIVE

## Transition principles

- DEFENSIVE escalation is immediate.
- DEFENSIVE cannot jump directly back to NORMAL. Recovery passes through
  RESEARCH after repeated clean observations.
- RESEARCH escalation is immediate.
- RESEARCH recovery is slower than escalation.
- ADAPT requires repeated watch evidence before NORMAL enters ADAPT.
- ADAPT requires repeated clean evidence before returning to NORMAL.

This prevents one noisy observation from causing posture oscillation.

## Authority

ASC-003 remains supervisory only.

It cannot:

- modify live parameters;
- alter strategy selection;
- change risk or sizing;
- call the broker;
- promote a challenger;
- deploy a production change.

The state machine becomes an input to future proposal generation, not an
execution command.
