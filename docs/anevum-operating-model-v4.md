# ANEVUM Operating Model V4

Status: canonical design for research control, strategy change, and Command.

## Purpose

ANEVUM separates deterministic automation from work that requires new reasoning.

The runtime is expected to be highly autonomous at repetitive, measurable work. It is
not allowed to present repeated deterministic sweeps as new strategic intelligence.

## Authority model

### Automatic

RHEN / IREN / GRAEN / VELUM may automatically:

- collect market, execution, reconciliation, and telemetry evidence;
- maintain the eligible market catalog and bounded discovery funnels;
- scan live markets using already-authorized strategy versions;
- manage already-open positions inside existing risk policy;
- execute a frozen research hypothesis through its declared development stage;
- advance a passed frozen candidate through validation and holdout;
- run VELUM replay/simulation required by the frozen methodology;
- persist artifacts, outcomes, rejections, provenance, and rollback evidence;
- mark failed or exhausted work truthfully.

### Protected

The runtime must stop and request a deliberate operator/model Work pass before it may:

- invent a new hypothesis family after a bounded program is exhausted;
- materially change research methodology because the current family failed;
- patch production strategy code from new research;
- select a candidate for protected production release;
- expand broker-write authority;
- change portfolio risk, capital allocation, or account-level safety;
- enable a new asset-class execution path.

These boundaries are reported through `research_control.v1`.

## Research lifecycle

```
OBSERVE
  -> EVIDENCE_READY
  -> WORK_RESEARCH
  -> HYPOTHESIS_FROZEN
  -> DEVELOPMENT
  -> VALIDATION
  -> HOLDOUT
  -> VELUM_REPLAY
  -> MICRO_LIVE
  -> REVIEW
  -> PROMOTE | REJECT | ROLLBACK
```

A rejected development candidate may advance to the next hypothesis only while that
next hypothesis belongs to the already-frozen bounded program. Exhaustion ends that
authority.

`ADAPTIVE_PROGRAM_EXHAUSTED` therefore maps to:

- mode: `RESEARCH_REVIEW_REQUIRED`
- review kind: `NEW_HYPOTHESIS_FAMILY`
- next action: `MODEL_HYPOTHESIS_GENERATION_REQUIRED`
- autonomous dispatch: stopped

A replay-passed candidate awaiting strategy change maps to
`RELEASE_REVIEW_REQUIRED`.

## Live testing

Micro-live execution is evidence, not proof of edge.

Use live capital to measure broker and market behavior that replay cannot fully
reproduce: fills, spread crossing, order replacement, partial fills, latency,
reconciliation, and exit mechanics.

Do not retune a strategy from a small number of live wins/losses unless the frozen
methodology explicitly defines that decision.

Capital scaling is a separate decision from strategy development.

## Market discovery

RHEN uses a hierarchical market funnel.

### 1. Eligible catalog

Start from Alpaca's actual active/tradable/fractionable U.S. equity catalog and the
configured exchange policy.

### 2. Market-wide seeds

Use inexpensive market-wide signals such as Alpaca most-active and mover screeners
to find where activity is occurring before requesting detailed history.

### 3. Candidate pool

Apply daily liquidity, price, volume, dollar-volume, and usable-volatility gates to
the bounded seed set. If screeners are unavailable, the proven full-market daily
ranking remains a fallback.

### 4. Active universe

Rank the bounded candidate pool with current intraday evidence. Only the configured
active set receives continuous strategy evaluation.

### 5. Execution watch

Risk, spread, correlation, capital capacity, session rules, and strategy
qualification determine which active symbols may become orders.

This design allows RHEN to search a broad Alpaca market without pretending the
runtime can consume detailed real-time history for every asset continuously.

## Asset-class scope

- Regular U.S. equities and ETFs: live long-only execution authority.
- Extended / overnight U.S. equities: the same long-only asset authority in a
  separate 24/5 execution regime; broker writes remain behind explicit session
  authorization and Alpaca-compatible order mechanics.
- Crypto / BTC: removed from active runtime, scheduling, research dispatch,
  configuration authority, Command, and strategy handoffs. Historical crypto
  evidence may remain read-only for provenance only.
- Options: read-only research/intelligence for bounded top-underlying analysis.
  Options orders have no execution authority.
- Short equities: no execution authority.
- Leverage expansion: no execution authority.

Adding or restoring any asset class or broker-write path is a protected change.

## Command V4

Command is organized by operator question rather than subsystem branding.

### Operate

What is trading now?

Shows broker account truth, positions, orders, regular and extended-equity lanes,
active universe, exposure and live performance.

### Discover

What is the system looking at and testing?

Shows the market discovery funnel, active market surfaces, bounded research,
simulations/replays, and evidence-indexed run charts.

### Review

What needs judgment?

Shows `research_control.v1`, the autonomy contract, candidate -> validation ->
release state, daily/weekly evidence, and the IREN Work/Codex handoff.

### System

Is the machinery healthy?

Shows canonical IREN state, runtime topology, dependencies, incidents, work state,
operations terminal, and the single raw event drawer.

Subsystem names remain identities and owners. They are not separate navigation
destinations unless they provide a genuinely distinct operator task.

## No cosmetic activity

Command must never infer substantive research from process liveness alone.

A healthy GRAEN process with no bounded experiment is waiting.

An exhausted program is review-required.

A VELUM process without an eligible replay is waiting.

NOSTRA must expose actual forecast/calibration work before Command labels it active.

A repeated scheduler heartbeat is not research progress.

## Release rule

A strategy change is releasable only when its evidence chain is attributable and
reversible:

```
observation
  -> hypothesis
  -> frozen methodology
  -> GRAEN result
  -> VELUM / NOSTRA evidence when applicable
  -> versioned RHEN strategy change
  -> micro-live evidence
  -> protected release review
```

No component may silently overwrite a working production strategy or broaden live
authority to create apparent progress.
