# ASC-009 Strategy-Family Routing

Status: RESEARCH ONLY

ASC-009 is the interface for eventually choosing among independently validated
strategy families under NOSTRA regime context.

The router does not place orders and is not connected to RHEN execution.

## Required family evidence

A family is research-eligible only when:

- it has explicit evidence for the current NOSTRA regime;
- the regime-specific evidence meets its minimum sample requirements;
- confidence is at least the configured research floor;
- the family has reached a protected validation state such as
  FROZEN_VALIDATION, HOLDOUT_COMPLETE, or CHALLENGER_CANDIDATE;
- expected utility is positive after the research cost model.

If no family satisfies those conditions, the router selects NO_TRADE.

## Why NO_TRADE is first class

ASC must never force the current market into the nearest strategy family.
Unknown or unsupported conditions should reduce activity and increase research,
not weaken evidence gates.

## Production boundary

The selected research family is diagnostic output only.

ASC-009 has no:

- broker authority;
- live strategy-switching authority;
- risk or sizing authority;
- deployment authority;
- automatic promotion authority.

A future production router would require separate validation, promotion, human
authorization, deployment controls, rollback, and monitoring.
