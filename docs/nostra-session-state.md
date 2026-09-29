# NOSTRA Session State Projection

Status: SHADOW / POST-EVENT READ ONLY

This module operationalizes ASC-002 against RHEN's canonical decision telemetry.

Every five minutes it selects the last recorded RHEN scan cycle in that bucket
and derives a market-state snapshot using only features that existed at the
decision time.

Inputs include candidate breadth, five-minute returns, trend spread, VWAP edge,
dispersion, relative volume, short-horizon realized volatility, spread, and
the strategy's persisted benchmark regime-confirmation returns.

The current RHEN strategy persists a five-minute benchmark regime window. It
does not persist a 15-minute benchmark return. NOSTRA leaves the 15-minute
field missing rather than reconstructing it with future data.

The result is a post-event five-minute regime timeline suitable for ASC-006
transition research.

No additional market calls occur and the timeline has no execution authority.
