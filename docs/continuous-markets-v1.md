# RHEN Continuous Markets v1

Status: implemented behind explicit feature and execution gates.

## Objective

Run RHEN continuously without forcing trades. The market router always asks which
lane is actually tradable:

- U.S. equities: Alpaca 24/5 window.
- Crypto: existing RHEN 24/7 lane.
- Regular equities: existing primary equity engine, unchanged.

This makes RHEN effectively continuous across the week while preserving separate
market microstructure and execution rules.

## Session ownership

All times are America/New_York.

| Session | Approximate window | Owner | Entry order |
| --- | --- | --- | --- |
| Overnight | 20:00-04:00 before a published trading day | Extended Equity | DAY limit + extended_hours |
| Premarket | 04:00-regular open | Extended Equity | DAY limit + extended_hours |
| Regular | published open-close | Existing Equity Engine | Existing market-entry path |
| After-hours | published close-20:00 | Extended Equity | DAY limit + extended_hours |
| Weekend | Fri 20:00-Sun 20:00 | Crypto only | Existing crypto adapter |

The session classifier uses Alpaca's published trading calendar instead of
hard-coding weekdays. Overnight is opened only when the next published regular
session is the next calendar day, which closes holiday and weekend gaps
conservatively.

## Safety invariants

1. The regular-session strategy is not modified.
2. The extended lane never submits an extended-hours market or stop order.
3. Extended positions use software-monitored stop, target, and max-hold exits,
   expressed as marketable DAY limit orders.
4. The lane flattens before the regular-session handoff.
5. Friday positions enter a dedicated flatten window before the 20:00 weekend
   close.
6. A residual extended position at the regular open is emergency-flattened by
   the extended lane and is never silently adopted by the regular strategy.
7. Entries share the existing account-level cash, gross exposure, per-position
   exposure, stop-risk, daily-loss, reconciliation, pause, and entries-enabled
   gates.
8. Live extended execution requires the existing RHEN live authorization plus
   `I_ACKNOWLEDGE_EXTENDED_EQUITY_LIVE=YES`.
9. The lane may observe and qualify opportunities while broker writes remain
   gated.
10. No session is required to trade. Zero qualifying opportunities means zero
    orders.

## Data path

The extended lane intentionally avoids treating delayed overnight historical
trades as live evidence.

Each cycle requests:

1. the freshest one-minute bar;
2. the freshest quote;
3. session-specific feed selection.

During the overnight session the lane uses Alpaca's `overnight` latest
bar/quote feed and builds an in-memory rolling tape. A cold start therefore
warms for at least `slow_window + 1` distinct observations before a signal can
qualify. Premarket and after-hours use the configured extended feed (IEX by
default).

A session/feed transition clears the rolling tape so observations from different
microstructure regimes are never blended into one signal.

## Universe

The base extended universe is a bounded set of liquid stocks and ETFs configured
by `EXTENDED_EQUITY_SYMBOLS`. The engine also folds in the primary dynamic
equity universe when available.

Every refresh rechecks broker metadata:

- active;
- tradable;
- fractionable;
- supported exchange;
- `overnight_tradable` during the overnight session;
- not overnight halted.

This avoids a second expensive market-wide ranking service while still allowing
the daytime dynamic scanner to feed discovered symbols into later sessions.

## Signal

The initial strategy family is extended rolling momentum.

A candidate requires:

- enough fresh one-minute observations;
- fresh quote and bar;
- spread under the session limit;
- minimum price;
- fast mean above slow mean;
- latest close rising;
- positive rolling momentum above threshold;
- close above rolling VWAP;
- bounded VWAP extension;
- configured market confirmations.

The reference price is the current ask. Entries add only the configured small
limit buffer; exits use the current bid minus the configured small limit buffer.

The overnight spread ceiling is separate from premarket/after-hours because
overnight liquidity is structurally thinner.

## Position lifecycle

An extended position is owned by this lane only when the most recent filled RHEN
extended buy has not been followed by a filled RHEN extended sell. Ownership can
survive midnight.

Exit precedence:

1. regular-session emergency handoff;
2. premarket handoff flatten window;
3. Friday weekend flatten window;
4. software stop;
5. profit target;
6. maximum hold time.

Pending extended entry limits are canceled after a short TTL so stale signals
cannot fill much later. Pending exits are repriced toward the current bid while
the exit condition remains active.

## Evidence and Command

Every extended order uses a distinct RHEN client-order ID containing `-ext-`.
The canonical evidence path records:

- market lane;
- equity session;
- strategy version;
- data feed;
- limit order type;
- DAY time in force;
- extended-hours flag;
- decision quote;
- strategy checks and sizing;
- broker order result.

The RHEN Command payload exposes an `extended_equity` object with session,
universe, scanner, authorization state, latest decision, errors, orders, and
cache state.

## Activation states

The code ships with both flags false:

```
EXTENDED_EQUITY_LANE_ENABLED=false
EXTENDED_EQUITY_EXECUTION_ENABLED=false
I_ACKNOWLEDGE_EXTENDED_EQUITY_LIVE=NO
```

Recommended promotion sequence:

1. Deploy code with the lane disabled.
2. Enable the lane with execution disabled and verify session/data/scan telemetry.
3. Run paper execution and collect fill/slippage/spread evidence.
4. Review stop/target/handoff behavior and session-level performance.
5. Only then authorize live extended execution explicitly.

This prevents a code deployment from becoming an unreviewed live-money change.

## Continuous-week behavior

From Sunday 20:00 ET through Friday 20:00 ET, RHEN can observe both the equity
session router and crypto lane. From Friday 20:00 through Sunday 20:00, crypto is
the only continuously tradable lane.

"Continuous" means continuous opportunity detection and risk management, not
continuous order generation.
