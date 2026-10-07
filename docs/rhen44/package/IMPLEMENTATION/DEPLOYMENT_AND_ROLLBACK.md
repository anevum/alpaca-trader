# Deployment and rollback

## Preconditions

- final RHEN 4.3 baseline recorded;
- US regular session closed for any trader restart that could change production code;
- no position requiring unsafe interruption;
- reconciliation safe;
- CI green;
- current production commit/deployment recorded;
- no unrelated incident or staged Railway work.

## Stage 1 — code deployed disabled

Deploy controller/NOSTRA/governor code with:

```text
ADAPTIVE_POLICY_ENABLED=false
NOSTRA_RUNTIME_REGIME_ENABLED=false
CAPITAL_GOVERNOR_ENABLED=false
```

Verify exact 4.3 behavior.

## Stage 2 — NOSTRA + shadow

Enable runtime observation only:

```text
NOSTRA_RUNTIME_REGIME_ENABLED=true
ADAPTIVE_POLICY_ENABLED=true
ADAPTIVE_POLICY_MODE=shadow
CAPITAL_GOVERNOR_ENABLED=true
```

Governor outputs are telemetry only while mode is shadow.

Verify:
- no order differences attributable to adaptive code;
- stable storage/CPU/API use;
- Command visibility;
- correct regime timestamps;
- correct baseline/effective/hard-limit display.

## Stage 3 — active conservative library

After validation of runtime mechanics, activate only approved profiles that do not increase risk above baseline behavior.

Recommended initial live set:

- BASELINE_LOCKED
- DEFENSIVE
- NORMAL

Keep assertive/long-hold profiles shadow-only until their research lineage passes.

## Stage 4 — profile promotion

Add FAST_SCALP/TREND_EXTEND/ASSERTIVE_TREND only through a versioned policy-library release and ASC/GRAEN activation contract.

## Rollback order

Fastest rollback requires no code removal:

1. `ADAPTIVE_POLICY_MODE=shadow`
2. if needed `ADAPTIVE_POLICY_ENABLED=false`
3. if needed `NOSTRA_RUNTIME_REGIME_ENABLED=false`
4. redeploy the frozen final 4.3 commit if code-level regression remains.

Risk-reducing exits and reconciliation must continue during adaptive rollback.

## Rollback triggers

Immediate fallback/rollback on:

- any risk invariant violation;
- unexplained order difference in SHADOW;
- duplicate orders;
- reconciliation regression;
- stale-data entry;
- policy-library fingerprint mismatch;
- invalid profile effective value;
- controller causing material scan latency/API errors;
- unbounded telemetry/storage growth;
- Command reporting ACTIVE when backend is not active or vice versa.

## Data retention

Retain failed adaptive observations and transition evidence for debugging. Never use failed/incompatible lineage evidence to justify later promotion without an explicit new methodology decision.
