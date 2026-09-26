# Strategy 005 exit-surface audit

Status: **OFFLINE RESEARCH ONLY**

After three Strategy 004 entry filters failed unseen data, Strategy 005 checks
whether simple stop/target/hold geometry could be hiding raw edge in the
underlying rolling-momentum/VWAP BUY opportunities.

The surface tests:

- stops: 0.20%, 0.25%, 0.30%, 0.35%, 0.40%, 0.50%;
- targets: 0.20%, 0.25%, 0.30%, 0.40%, 0.50%, 0.60%;
- maximum holds: 5, 10, 15 minutes;
- 5 bps assumed full spread;
- 2 bps slippage per side;
- conservative stop-first ordering when a one-minute bar touches both barriers.

This analysis is intentionally opportunity-level. It keeps repeated adjacent
signals and ignores portfolio-capacity effects so it answers one narrow
question:

> Does any simple exit geometry turn the existing qualified signal family into
> positive raw expectancy consistently across opened development periods?

The initial connector-side audit across Aug 17–21, Sep 8–11, and Sep 14–18
found **no tested stop/target/hold cell with positive mean return in all three
periods**.

That result must now be reproducible through the repository runner before it is
treated as a permanent research fact. If reproduced, it means the next work
should not be another stop/target optimization or global entry threshold.
Instead, ANEVUM should test a structurally different signal family.

No production setting or capital limit changes from this audit.
