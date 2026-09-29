# ASC-009 Strategy Family Registry

Status: RESEARCH ONLY

ASC-009 separates strategy-family evolution from parameter adaptation.

The canonical current family is:

- family: rhen-long-momentum-v1
- strategy: rolling_momentum_vwap
- direction: LONG
- role: current production champion

The registry does not own the production strategy and cannot change it. It only
declares family identity and provides a stable interface for future independent
research families.

A research family has its own:

- family key;
- strategy name;
- direction;
- asset class;
- development/validation status;
- regime-specific evidence;
- hypothesis and lineage.

New families cannot obtain execution authority or automatic promotion through
the registry.

ASC-009's research router may rank validated families under NOSTRA regime
context, but defaults to NO_TRADE when no family has mature positive
regime-specific utility.

This is the mechanism that lets RHEN survive structural edge decay without
loosening the original momentum formula until it becomes an unrelated
strategy.
