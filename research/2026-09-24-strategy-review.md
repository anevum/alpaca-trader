# Strategy review — 2026-09-24

## Objective

Evaluate whether the current live rolling-momentum/VWAP configuration should be made more aggressive in an attempt to grow an approximately $89.28 account toward $150 by 2026-09-30.

## Constraint

There is no trading configuration that can guarantee a market return. Reaching $150 from $89.28 through trading alone requires $60.72 of profit, or about 68.0%. That is approximately 10.9% compounded per session over five sessions or 13.9% over four full sessions.

The production objective should therefore be positive expectancy and capital preservation, not forcing the account to hit a fixed calendar-dollar target.

## Data and method

Recent Alpaca IEX one-minute bars were reviewed for:

SPY, QQQ, SMH, NVDA, AMD, META, AAPL, MSFT, AMZN, TSLA, AVGO.

Sessions used: 2026-09-17 through 2026-09-24. The first four sessions were treated as a training sample and 2026-09-23/24 as a small out-of-sample check.

The simulation approximated the production rolling strategy: completed one-minute bars, fast/slow close averages, momentum threshold, session VWAP edge, confirmation checks, one position at a time, stop/target/time exit, same-symbol lockout, and daily entry cap. Bar data cannot reproduce exact market-order fills, spreads, or intra-minute execution, so these results are directional rather than broker-account P&L.

## Key results

Current-like high-frequency profile:
- Training: 48 trades, 54.2% winners, summed trade return about +2.64%, profit factor about 1.55.
- Test: 22 trades, 27.3% winners, summed trade return about -0.98%, profit factor about 0.71.
- Interpretation: the profile worked during the stronger trend sample but deteriorated sharply over the last two sessions.

Strict-trend candidate:
- 3/10 fast/slow structure.
- 0.08% minimum short-term momentum.
- 0.03% minimum VWAP edge.
- All three SPY/QQQ/SMH confirmations required.
- 0.40% stop / 0.70% target.
- 30-minute maximum hold.
- 10-minute re-entry cooldown.
- 09:45–12:00 entry window.
- Maximum four entries per day.
- Liquid large-cap scan universe only.

Approximate results:
- Training: 6 trades, 66.7% winners, summed trade return about +0.91%, profit factor about 2.14.
- Test: 3 trades, 66.7% winners, summed trade return about +0.34%, profit factor about 1.84.

The sample is much too small to establish statistical significance. Its value is that the strict configuration did not require increased trade frequency or risk to improve the recent out-of-sample behavior.

## Decision

Do not increase frequency, leverage, or position size merely to chase $150 by month-end.

Stage Strict-trend V2 and keep $20 notional until live results provide a larger sample. Remove thin symbols such as XSD from the production scan universe. Treat no-trade periods as a feature: if SPY, QQQ, and SMH do not all confirm, the bot should stand down.

## Next validation gate

Before increasing notional, collect at least 20 completed live trades under one unchanged profile and evaluate:
- realized P&L,
- win rate,
- average win and average loss,
- profit factor,
- maximum drawdown,
- slippage versus signal reference price,
- results by symbol,
- results by entry hour,
- stop / target / time-exit frequency.

Do not scale because of one winning day. Scale only if the completed sample remains positive after execution friction.
