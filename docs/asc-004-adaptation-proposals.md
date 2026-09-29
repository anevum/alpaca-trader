# ASC-004 Adaptation Proposal Engine

Status: SHADOW / PROPOSAL ONLY

ASC-004 converts validated research evidence into reproducible bounded parameter
proposals.

It does not apply those proposals.

## Initial adaptive policy

Only explicitly listed parameters may enter the proposal engine.

Initial research policy includes:

- min_momentum_pct
- min_vwap_edge_pct
- min_confirmations
- max_vwap_extension_pct

These bounds are research defaults for the proposal system and are not
production authorization.

Safety/risk parameters such as stop limits, portfolio loss limits, capital
allocation and live-trading authorization are intentionally absent.

## Proposal behavior

Every proposal records:

- source strategy version;
- parameter;
- old value;
- requested value;
- bounded proposed value;
- daily or weekly change budget;
- adaptive range;
- evidence;
- confidence;
- rollback value;
- whether range/budget clipping occurred;
- deterministic proposal ID.

Every proposal sets:

- authorization_required = true
- automatic_application_authorized = false
- execution_authority = false
- live_configuration_changed = false

## Counterfactual input

ASC-004 may consume counterfactual alternatives only when upstream evidence
explicitly reports acceptable selection-bias, dependence and validity states.

A high apparent improvement with failed validity controls is ignored.
