# BTC intraday design and execution repair

The October 6 runtime runs `RHEN-BTC-DIRECT-003` in
`btc_direct_live_signal`; autonomous live broker writes remain disabled.
The original bounded direct candidate grid is exhausted: its eight measured
development runs produced 13–18 trades each, with negative BASE net
expectancy. These results do not authorize a live release. The current GRAEN
V3 research workflow remains the research authority; this patch creates no
new research scheduler, promotion path, service, or assignment authority.

`RHEN-BTC-DAY-004` is a fixed, unvalidated operational hypothesis observed
through the existing market-data adapter. It uses completed, contiguous
15-minute BTC bars, 8/21/64 EMA alignment and a prior four-hour breakout.
It rejects breakout extensions above ATR and requires the observed six-hour
range to cover a 1.8% gross target. The proposed stop is 1.25% and maximum
hold is six hours; a completed-bar trend reversal is also an exit condition.
Observed movement is a budget, not measured expectancy.

The quote check uses the ask, quoted quantity, freshness and spread, with
floors of 0.5% round-trip taker fees and 0.1% slippage. A quote must leave at
least a 0.3% net target margin. Alpaca's initial crypto tier currently charges
0.25% taker fees per side; marketable limits also incur taker fees.
Source: https://docs.alpaca.markets/docs/crypto-fees

`BTC_DAY_PREVIEW_ENABLED=true` enables read-only observation inside the
existing crypto monitor. The snapshot records observation time, action,
reason, parameter fingerprint, assumptions and blockers in health and
Command's crypto lane. It assumes a flat hypothetical account and never
submits orders. `preview_only` blocks engine execution and order-ID creation
even if someone injects the design into an otherwise authorized engine.
The live strategy identity, risk settings and the separate paper canary
assignment remain unchanged.

Before any executable candidate is released, the existing GRAEN/VELUM path
must independently measure chronological development, validation, holdout,
cost/delay stresses and candidate-specific forward paper. Operational unit
tests are not promotion evidence. The design has not passed those stages and
cannot replace the existing durable paper assignment.

Execution repairs recognize Alpaca's compact crypto symbols in ownership,
position/risk checks and Command projections while preserving equity symbols.
Paper exits now wait for an already working exit, reread broker positions
and open orders after protective cancellation, and use the remaining available
quantity. A stop fill during cancellation is treated as flat; pending
cancellation blocks another sell. The simulated buy/protect/restart/exit test
uses fee-reduced position quantity and existing execution code. This is
mechanical verification, not a historical or forward profitability result.
