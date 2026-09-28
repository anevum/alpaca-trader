# ADS-002 — Shadow Learning + Attribution Integrity

Status: ACTIVE DESIGN / RESEARCH-ONLY  
Owner: RHEN Research  
Authority: IREN // CORE / Devon Akins  
Methodology family: ADS  
Canonical version: ADS-002-v1  
Date frozen: 2026-09-28  
Production execution changes allowed by this phase: NONE

## 1. Purpose

ADS-002 converts the ADS-001 decomposition into a durable, auditable, research-only shadow system.

The system must answer one question before any adaptive trading change is considered:

> Can RHEN reliably attribute candidate decisions through execution and outcomes, compute decomposed Attention / Qualification / Timing / Exit / Confidence measurements without future-data leakage, and demonstrate repeatable cross-session structure?

ADS-002 does not change live qualification, risk, sizing, order routing, exits, capital allocation, broker behavior, or protected validation methodology. No ADS-002 score may be read by the live execution path.

## 2. Current verified state on 2026-09-28

The production ledger is partially healthy and partially broken.

Verified session facts:
- 9,300 candidate-evaluation rows were stored for the session.
- 0 of those candidate rows were marked qualified.
- 0 candidate rows carried a signal_id.
- 8 live entry signals were created.
- All 8 signals link directly to an order intent.
- All 8 intents link directly to an entry order.
- All 8 entry orders link directly to a fill.
- 8 positions were opened and later closed.
- All 8 closed positions have realized_return.
- 6 of 8 positions currently have MFE/MAE populated.
- The eight signal cycle keys do not resolve to matching decision-cycle rows/candidates.

Interpretation:
- Signal -> intent -> order -> fill is intact.
- Candidate -> signal attribution is not intact for today's executed entries.
- Today's 9,300 candidate rows do not contain the feature/reference-price completeness required for A/Q/T validation.
- Therefore 2026-09-28 may be used as an attribution-integrity incident and execution-outcome sample, but must not be treated as valid evidence for candidate-score predictive performance.

This is a hard evidence-integrity rule, not a judgment about strategy quality.

## 3. Locked invariants

1. The live strategy remains authoritative until a later explicitly approved promotion phase.
2. ADS-002 is shadow-only.
3. A, Q, and T use only information that existed at the decision timestamp.
4. X is post-trade outcome analysis and is forbidden from any entry-time feature set.
5. C measures trust in the research evidence; it is not a trade desirability score.
6. No historical link is fabricated.
7. Ambiguous historical attribution remains AMBIGUOUS or UNLINKED.
8. Versioned methodology is immutable after evidence collection begins.
9. Existing canonical telemetry is extended; no competing shadow ledger is created.
10. ADS-002 can reach at most FROZEN_VALIDATION_READY. It cannot authorize live promotion.

## 4. Canonical identity chain

For every executable entry, the intended chain is:

candidate
  -> signal
  -> order_intent
  -> broker_order
  -> fill
  -> position
  -> exit intent/order/fill
  -> realized outcome

Required durable identifiers:

- run_id
- scan_cycle_id
- correlation_id
- candidate_id
- candidate_key
- signal_id
- intent_id
- client_order_id
- broker_order_id
- fill_id
- position_id
- exit_id
- exit broker_order_id
- exit fill_id

### 4.1 Candidate key

Future identity contract:

candidate_key = "{run_id}:{correlation_id}:{SYMBOL}"

The exact candidate_key must be generated once from the live decision context and copied into:
- candidate evaluation
- signal payload
- entry intent payload
- research attribution record

The candidate key is an identity field, not a fuzzy matching key.

### 4.2 Required race-safe linking behavior

Because an entry intent can be durably persisted before the later decision-cycle projection arrives, database linking must work in either insertion order.

Rules:
- signal arrives first: retain candidate_key in signal payload; link when candidate arrives.
- candidate arrives first: link when signal arrives.
- both present: assert one-to-one consistency.
- multiple candidates for one candidate_key: mark AMBIGUOUS and raise a research integrity blocker.
- candidate_key mismatch across an existing direct chain: never overwrite silently.

No time-nearest heuristic may create a DIRECT link.

## 5. Attribution states

Every research row receives exactly one state:

DIRECT_COMPLETE
- candidate, signal, intent, entry order, fill, and position are linked by durable IDs.
- for a closed trade, exit lineage is also linked.

DIRECT_PARTIAL
- candidate -> signal is direct, but downstream lineage is incomplete because the order did not fill, remained open, was canceled, or telemetry is legitimately incomplete.

HISTORICAL_PROVISIONAL
- an older row can be associated only by a deterministic historical rule that is not part of the future identity contract.
- provisional rows may be used for diagnostics but not frozen validation.

AMBIGUOUS
- more than one valid-looking linkage exists.

UNLINKED
- no supportable candidate linkage exists.

For promotion-readiness calculations, only DIRECT_COMPLETE and context-appropriate DIRECT_PARTIAL rows count as canonical attribution.

## 6. Research-eligible candidate definition

A candidate is research_eligible only if all of the following are true:

- regular-session candidate;
- candidate_key present;
- observed_at present;
- decision_reference_price > 0;
- required A/Q/T source features available or reproducibly reconstructable from timestamp-safe bars/quotes;
- data_quality_state = available;
- no future bars are used;
- methodology_version is known;
- strategy_version_id is known;
- source feed and bar interval are known.

Rejected candidates are intentionally retained. The research corpus must not contain only executed winners/losers.

## 7. Shadow score vector

Canonical vector:

ADS = (A, Q, T, X, C)

All scores are on [0, 100].

A, Q, and T are pre-decision descriptive scores.
X is post-exit descriptive.
C is evidence confidence for the current research state.

The score methodology version is:

ads-shadow-v1

No fitted coefficients are allowed in v1. The coefficients below are frozen heuristic decompositions whose purpose is to create stable research observables, not to claim optimality.

### 7.1 Shared helper

clamp01(x) = min(1, max(0, x))

momentum_scale = max(4 * live_min_momentum_pct, 0.002)
vwap_scale = max(live_target_pct, 0.0025)

All source parameters are captured from the exact strategy configuration associated with the candidate.

### 7.2 A — Attention

Meaning:
How unusually active or salient the opportunity is, independent of whether it is a good entry.

Inputs:
- relative_volume_ratio
- absolute momentum
- trend persistence strength

Components:

a_rvol = clamp01(relative_volume_ratio / 2)

a_velocity = clamp01(abs(momentum_pct) / momentum_scale)

a_persistence = clamp01(2 * abs(trend_persistence - 0.5))

A = 100 * (
    0.45 * a_rvol
  + 0.35 * a_velocity
  + 0.20 * a_persistence
)

Attention does not imply direction or desirability.

### 7.3 Q — Qualification

Meaning:
How strongly the observed setup satisfies the intended structural long-entry thesis.

Inputs:
- positive momentum
- positive VWAP edge
- independent confirmation ratio
- regime confirmation ratio

Components:

q_momentum = clamp01(max(momentum_pct, 0) / momentum_scale)

q_vwap = clamp01(max(vwap_edge_pct, 0) / vwap_scale)

If independent confirmation symbols are configured:

q_confirm = clamp01(fresh_confirmation_passes / independent_confirmation_count)

Otherwise:

q_confirm = 0.5

If regime confirmations are configured:

q_regime = clamp01(regime_confirmation_passes / regime_confirmation_count)

Otherwise:

q_regime = 0.5

Q = 100 * (
    0.35 * q_momentum
  + 0.25 * q_vwap
  + 0.25 * q_confirm
  + 0.15 * q_regime
)

Q intentionally excludes fill outcome, realized return, MFE, MAE, and post-entry information.

### 7.4 T — Timing

Meaning:
How clean the immediate entry conditions are, given the setup.

Components:

t_spread = 1 - clamp01(spread_pct / max_spread_pct)

t_bar_freshness = 1 - clamp01(bar_age_seconds / max_bar_age_seconds)

If quote_age_ms is present:

t_quote_freshness =
    1 - clamp01(quote_age_ms / (1000 * max_bar_age_seconds))

Otherwise:
- t_quote_freshness = t_bar_freshness
- score_details.quote_age_imputed = true

If max_vwap_extension_pct > 0:

t_headroom =
    1 - clamp01(max(vwap_edge_pct, 0) / max_vwap_extension_pct)

Otherwise:

t_headroom = 0.5

T = 100 * (
    0.35 * t_spread
  + 0.25 * t_bar_freshness
  + 0.15 * t_quote_freshness
  + 0.25 * t_headroom
)

Timing is intentionally separated from structural qualification so the research can distinguish "good setup, bad entry moment" from "bad setup, clean execution."

### 7.5 X — Exit health

X is undefined until a position is closed.

Let:
- R = realized_return
- MFE = max_favorable_excursion
- MAE = max_adverse_excursion
- eps = 0.000001

mfe = max(MFE, 0)
mae = min(MAE, 0)

x_location =
    clamp01((R - mae) / max(mfe - mae, eps))

If mfe > 0:

x_capture = clamp01(R / mfe)

Else if R >= 0:

x_capture = 1

Else:

x_capture = 0

x_loss_containment =
    1 - clamp01(abs(min(R, 0)) / max(abs(mae), eps))

X = 100 * (
    0.45 * x_location
  + 0.35 * x_capture
  + 0.20 * x_loss_containment
)

Additional exit diagnostics are recorded but are not folded into X v1:
- stop_touched
- target_touched
- time_to_mfe_ms
- time_to_mae_ms
- holding_duration_ms
- exit_reason
- giveback_from_mfe
- entry slippage

If MFE or MAE is unavailable, X is NULL with a reason code. Do not impute excursion data.

### 7.6 C — Confidence

C measures whether RHEN should trust the current ADS research evidence.

It is session/research-state confidence, not a per-trade attractiveness score.

Components:

c_attribution
= direct, non-ambiguous attribution coverage

c_completeness
= coverage of required timestamp-safe features and eligible forward outcomes

c_sample
= geometric mean of:
  min(independent_sessions / 10, 1)
  min(research_eligible_candidates / 100, 1)
  min(closed_direct_trades / 30, 1)

c_stability
= 0 until at least 2 independent sessions exist.
Thereafter:
  0.60 * sign_consistency
+ 0.40 * effect_persistence

where:
- sign_consistency = max(fraction of session-level primary Q effects positive,
                         fraction negative)
- effect_persistence = clamp01(median(abs(session_level_spearman_Q_15m)) / 0.20)

c_regime
= min(distinct_regime_buckets / 3, 1)

C = 100 * (
    0.30 * c_attribution
  + 0.25 * c_completeness
  + 0.20 * c_sample
  + 0.15 * c_stability
  + 0.10 * c_regime
)

Hard cap:
Until all three minimum sample requirements are met
(10 sessions, 100 eligible candidates, 30 closed direct trades),
C <= 49.

C never changes live execution.

## 8. Research-only composite benchmark

ADS-002 is decomposed by design, but a fixed benchmark composite is useful for testing whether decomposition adds information relative to the legacy single quality score.

S_pre = 0.20*A + 0.50*Q + 0.30*T

S_pre is research-only.
It is not a production score and must not be read by the live execution path.

## 9. Forward outcomes

ADS-002 expands the current forward-outcome set.

Canonical v2 horizons:
- 1 minute
- 3 minutes
- 5 minutes
- 10 minutes
- 15 minutes
- 30 minutes
- 60 minutes

Methodology version:
candidate-forward-v2

Do not rewrite candidate-forward-v1 rows.

For every eligible horizon store:
- reference_price
- forward_price
- forward_return
- max_favorable_return
- max_adverse_return
- observation_end_at
- status
- reason code when incomplete
- provider
- bar interval
- methodology version

No horizon may cross regular-session close. Missing exact terminal data remains explicitly incomplete.

Primary inferential horizon:
15 minutes

The other horizons are secondary/exploratory and remain subject to multiplicity control.

## 10. Canonical persistence design

Do not create a second trading ledger.

Add two private research tables.

### 10.1 private.trading_ads_attribution

Purpose:
Audit candidate-to-execution identity without mutating ambiguous historical rows.

Required columns:
- attribution_id bigint identity primary key
- candidate_id bigint nullable FK
- candidate_key text nullable
- signal_id uuid nullable FK
- intent_id uuid nullable FK
- entry_order_id text nullable FK
- entry_fill_id bigint nullable FK
- position_id uuid nullable FK
- exit_id uuid nullable FK
- exit_order_id text nullable
- exit_fill_id bigint nullable
- attribution_state text not null
- link_method text not null
- link_confidence numeric not null
- reason_codes text[] not null
- methodology_version text not null
- session date not null
- computed_at timestamptz not null
- details jsonb not null

Uniqueness:
(candidate_key, methodology_version) where candidate_key is not null

Allowed states:
DIRECT_COMPLETE, DIRECT_PARTIAL, HISTORICAL_PROVISIONAL, AMBIGUOUS, UNLINKED

### 10.2 private.trading_ads_shadow_scores

Required columns:
- score_id bigint identity primary key
- candidate_id bigint not null FK
- candidate_key text not null
- run_id uuid not null
- strategy_version_id text not null
- session date not null
- symbol text not null
- observed_at timestamptz not null
- attention_score numeric
- qualification_score numeric
- timing_score numeric
- exit_health_score numeric
- confidence_score numeric
- pretrade_composite numeric
- legacy_quality_score numeric
- feature_vector jsonb not null
- score_components jsonb not null
- source_completeness jsonb not null
- attribution_state text not null
- methodology_version text not null
- forward_methodology_version text
- computed_at timestamptz not null

Uniqueness:
(candidate_id, methodology_version)

Security:
- private schema only
- RLS enabled as defense in depth
- revoke anon/authenticated
- research service role only
- no public feed dependency
- no live execution query dependency

## 11. Historical backfill policy

Historical repair has three levels.

Level 1 — direct repair:
Use existing durable IDs only.
Safe to persist as DIRECT_*.

Level 2 — deterministic provisional:
A unique historical relationship may be recorded as HISTORICAL_PROVISIONAL if all predefined conditions match.
It must never overwrite a direct canonical link.

Level 3 — no support:
Leave UNLINKED.

For 2026-09-28 executed entries:
- signal -> intent -> order -> fill is direct.
- position linkage is direct through entry order / position identity.
- candidate linkage is presently unsupported because the execution cycle keys have no corresponding decision-cycle candidate rows.
- these eight entries must remain UNLINKED at the candidate boundary unless later evidence supplies the missing direct identity.
- do not fabricate nearest-time candidate matches.

## 12. Daily post-close pipeline

Order of operations is fixed.

1. Session boundary validation
   - confirm official trading session and close
   - identify shortened session if applicable

2. Ledger reconciliation
   - broker orders
   - fills
   - positions
   - exits

3. Attribution audit
   - compute candidate chain states
   - reject ambiguous chains
   - produce direct-link coverage

4. Candidate feature reconstruction
   - timestamp-safe bars only
   - timestamp-safe quotes only
   - no partial/future bar leakage

5. A/Q/T computation
   - ads-shadow-v1
   - deterministic
   - idempotent

6. Forward outcome enrichment
   - candidate-forward-v2
   - seven horizons

7. X computation
   - closed direct positions only
   - no excursion imputation

8. Session analysis
   - distributions
   - rank associations
   - quartile separation
   - interaction diagnostics
   - execution/slippage diagnostics

9. Confidence update
   - compute C
   - apply hard sample cap

10. Canonical daily report integration
   - add an ads002 section to the existing daily report
   - do not create a competing daily-report source of truth

11. Research Agent review
   - deterministic integrity gates first
   - semantic/model review only if deterministic readiness allows it

12. Slack notification
   - notify only on meaningful completion, failure, blocker, readiness-state change, or decision needed
   - no per-candidate or per-model chatter

## 13. Daily ADS-002 report contract

The existing canonical daily report receives:

ads002: {
  methodology_version,
  attribution: {
    total_candidates,
    research_eligible_candidates,
    direct_complete,
    direct_partial,
    historical_provisional,
    ambiguous,
    unlinked,
    direct_coverage,
    blocker_reason_codes
  },
  score_coverage: {
    A,
    Q,
    T,
    X,
    C
  },
  distributions: {
    A,
    Q,
    T,
    X
  },
  forward_outcomes: {
    primary_horizon_minutes: 15,
    horizons: [...]
  },
  associations: {
    pooled_descriptive,
    per_session,
    quartile_spreads,
    q_t_interaction
  },
  execution: {
    direct_trades,
    entry_slippage,
    decision_to_submit_ms,
    submit_to_ack_ms,
    ack_to_fill_ms
  },
  confidence: {
    score,
    components,
    hard_cap_active,
    missing_requirements
  },
  readiness: {
    state,
    reason_codes
  }
}

## 14. Statistical methodology

ADS-002 must not promote based on one pooled correlation.

### 14.1 Primary analysis

Primary variable:
Q

Primary outcome:
15-minute forward_return

Primary effect:
session-level Spearman rank correlation

Report:
- each session's rho
- median rho across independent sessions
- sign consistency
- block-bootstrap confidence interval across sessions
- top-quartile minus bottom-quartile forward-return spread

### 14.2 Secondary analysis

- A vs each forward horizon
- T vs each forward horizon
- T vs entry slippage for executed direct trades
- Q x T 2D rank grid
- S_pre vs legacy quality score
- X vs realized return / MFE / MAE diagnostics

Secondary horizon claims use multiplicity correction consistent with the existing MATH-001 multiplicity/dependence work.

### 14.3 Dependence handling

Candidate observations from the same scan/session are not treated as independent.

Minimum procedure:
- session is the primary resampling block;
- preserve within-session candidate dependence;
- report per-session effects before pooled effects;
- use dependence-aware bootstrap/aggregation;
- do not multiply apparent sample size by treating repeated scans of the same symbol as independent evidence.

### 14.4 Regime buckets

At minimum classify each session into reproducible buckets using timestamp-safe market context:
- directional / trending
- range / mixed
- elevated volatility

The exact regime classifier must be versioned before it is used for a promotion gate.

## 15. ADS-002 hypotheses

H1 — Decomposition:
A fixed A/Q/T decomposition contains more stable forward-outcome information than the legacy single quality score.

H2 — Qualification stability:
Q is more cross-session stable than T.

H3 — Timing regime sensitivity:
T varies materially by regime and should not be treated as universally stable without regime evidence.

H4 — Attribution integrity:
Direct identity repair materially increases the fraction of evidence that can be used without heuristic linkage.

H5 — Confidence usefulness:
Low C corresponds to measurably less stable or less complete research evidence.

## 16. Readiness states

RESEARCH_ONLY
Default.

SHADOW_DATA_VALID
Requires all of:
- no active evidence-integrity blocker;
- ambiguous attribution count = 0 for new post-fix executable signals;
- direct attribution coverage >= 99.5% for new executable signals over the rolling evaluation window;
- A/Q/T compute coverage >= 99% of research-eligible candidates;
- eligible forward-outcome coverage >= 95%;
- methodology versions fully populated.

SHADOW_STABLE
Requires SHADOW_DATA_VALID plus:
- >= 10 independent sessions;
- >= 100 research-eligible candidates;
- >= 30 closed DIRECT_COMPLETE trades;
- primary Q effect has the same sign in >= 70% of evaluated sessions;
- primary top-vs-bottom quartile spread has the same sign in >= 70% of evaluated sessions;
- no single session accounts for > 25% of closed direct trades;
- confidence C >= 60.

FROZEN_VALIDATION_READY
Requires SHADOW_STABLE plus:
- frozen feature definitions;
- frozen horizons;
- frozen primary metric;
- frozen cost assumptions;
- frozen regime classifier;
- multiplicity/dependence methodology recorded;
- no unresolved telemetry blocker;
- confidence C >= 70;
- validation window defined but not inspected.

ADS-002 cannot enter LIVE_PROMOTED.

Any live promotion belongs to ADS-003 or later and requires explicit authorization.

## 17. H1 comparison gate

The decomposition is considered to have earned a frozen validation experiment only if, on the development corpus:

- median per-session Spearman(S_pre, 15m return)
  > median per-session Spearman(legacy_score, 15m return);

- the session-block bootstrap interval for the difference is above zero;

- the sign of the improvement is positive in >= 70% of sessions;

- the result is not dependent on a single symbol, session, or regime bucket;

- the effect survives the recorded cost-stress scenario.

Failure does not trigger automatic redesign. It records a null/negative research result.

## 18. Leakage controls

Forbidden in A/Q/T:
- future bars
- fills
- realized P&L
- exit reason
- MFE/MAE
- later session outcomes
- revised features computed with bars after observed_at

Required:
- exact observed_at
- exact bar timestamp
- quote timestamp where available
- reconstruction cutoff
- methodology version

X may use post-trade data because X is explicitly an outcome diagnostic.

C may use accumulated historical evidence but must never feed same-event A/Q/T or live execution.

## 19. Idempotency

Every ADS-002 operation must be replay-safe.

Recommended stable keys:

attribution:
ads002:attribution:{candidate_key}:{methodology_version}

shadow score:
ads002:score:{candidate_id}:{methodology_version}

forward outcome:
existing candidate/horizon/methodology uniqueness

daily report:
existing canonical report identity plus ads002 methodology fingerprint

A repeated post-close run must update the same research rows, not create duplicates.

## 20. Failure behavior

Fail closed for research claims, fail open for live execution.

Examples:
- missing candidate identity: live unaffected; research row UNLINKED.
- ambiguous link: live unaffected; ADS readiness BLOCKED.
- forward data unavailable: live unaffected; horizon marked incomplete.
- score input missing: live unaffected; score NULL with reason code.
- research model unavailable: deterministic outputs still persist; no semantic conclusion.
- ADS persistence unavailable: live execution must never depend on it.

## 21. Observability

Track:
- attribution coverage
- ambiguous link count
- score compute coverage
- forward outcome completeness
- session report completion
- confidence C
- readiness state
- methodology fingerprint
- source commit
- deployment ID

Slack should receive one compact post-close ADS summary when meaningful:
- completed normally;
- blocker appeared/cleared;
- readiness state changed;
- explicit human decision is needed.

## 22. Implementation sequence

Phase A — identity repair
1. Carry candidate_key into entry signal/intent telemetry before broker submission.
2. Make candidate<->signal linking race-safe in both insertion orders.
3. Add attribution audit table.
4. Add integrity tests.
5. Verify new rows in production research telemetry without changing execution.

Phase B — shadow scoring
1. Add deterministic ads-shadow-v1 scorer.
2. Add score persistence table.
3. Compute A/Q/T post-event.
4. Compute X after position close.
5. Add C at session/report level.
6. Add unit tests for bounds, missing-data handling, and leakage.

Phase C — forward outcomes
1. Add candidate-forward-v2 horizons 1/3/5/10/15/30/60.
2. Preserve v1 rows.
3. Add completeness and no-cross-close tests.

Phase D — reporting
1. Extend canonical daily report with ads002 block.
2. Add per-session primary/secondary metrics.
3. Add confidence/readiness state.
4. Surface only meaningful Slack state changes.

Phase E — evidence accumulation
1. Run automatically after each close.
2. Freeze v1 methodology.
3. Accumulate independent sessions.
4. Do not tune coefficients session-by-session.
5. Revisit only through a versioned ADS-002-v2 proposal.

## 23. Required tests

Identity:
- signal-first then candidate
- candidate-first then signal
- duplicate event replay
- candidate-key collision
- ambiguous historical link
- no nearest-time auto-link

Scoring:
- every component bounded [0,100]
- A is direction-neutral
- Q contains no post-event fields
- T handles missing quote age explicitly
- X remains NULL before close
- X remains NULL without excursion data
- C hard cap holds before minimum samples

Forward outcomes:
- exact 1/3/5/10/15/30/60 horizons
- no regular-session close bridging
- no future/partial bar usage
- explicit incomplete states

Reporting:
- deterministic idempotent output
- primary horizon fixed at 15
- missing ADS inputs produce limitation/blocker codes instead of fabricated zeros

Safety:
- ADS module imports no broker client
- no execution configuration read/write path consumes ADS scores
- no risk/sizing mutation
- no automatic experiment stage opening
- no automatic live promotion

## 24. Database migration requirements

The implementation migration must:
- be additive;
- create only private research objects;
- enable RLS;
- revoke anon/authenticated;
- create foreign-key indexes;
- use security invoker for read functions;
- contain no live strategy/risk/execution updates;
- be safe to re-run where practical.

Before production application:
- run database advisors;
- verify no relevant PostgreSQL 15.19 / 17.11 breaking-change exposure for the objects changed;
- verify table grants and RLS.

## 25. Definition of done for ADS-002 build

The build itself is complete when:
- identity contract exists in code and database;
- new live telemetry can link candidate -> signal -> intent -> order -> fill -> position without fuzzy matching;
- A/Q/T shadow rows persist idempotently;
- X finalizes for closed directly attributed positions;
- candidate-forward-v2 is produced;
- daily reports contain ads002;
- C/readiness is deterministic;
- tests pass;
- live execution behavior is unchanged;
- production verification confirms shadow-only behavior.

The research phase is not scientifically complete until evidence thresholds for SHADOW_STABLE are met.

## 26. Current next action

The first implementation task is not score tuning.

It is:

> Repair future candidate identity by carrying candidate_key through the entry-intent path and making database linkage race-safe.

Only after that repair is verified should ADS-002 begin counting sessions toward SHADOW_DATA_VALID or SHADOW_STABLE.
