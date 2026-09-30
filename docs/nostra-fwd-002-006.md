# NOSTRA FWD-002 through FWD-006

Status: foundation implementation
Program: FORWARD / NOSTRA
Forecast methodology: FORECAST-001

This build establishes the evidence spine required before NOSTRA can become a live forecasting input. It deliberately does not change RHEN entries, exits, sizing, risk, broker behavior, or strategy thresholds.

## FWD-002 — Prediction ledger

Every NOSTRA record receives a content-derived deterministic identifier. The NostraLedger writes through RHEN's existing critical ingest path rather than the ordinary asynchronous telemetry queue. If durable ingest is unavailable, the ledger fails closed and reports that the record was not persisted.

Database forecast records are append-only. Corrections must create a new record and reference the superseded identifier.

## FWD-003 — Time-safe snapshots

A snapshot records only information available at its as-of timestamp. The contract contains raw features, normalized features, market state, data quality, source information, code version, and provenance. Realized outcomes are intentionally absent.

Snapshot and forecast timestamps must be timezone-aware. A forecast cannot claim to have been generated before the snapshot it consumes.

## FWD-004 — Baselines

The initial baseline suite contains:

- zero expected return;
- uniform direction probabilities;
- Laplace-smoothed empirical direction base rates derived only from supplied prior observations;
- volatility persistence from the most recently realized volatility.

These are comparison models, not trading signals.

## FWD-005 — Provenance

Snapshots, forecasts, outcomes, and scores carry versioned provenance. Evidence records can include code SHA, feature version, model version, calibration version, run identity, strategy version, and source data metadata.

Stable IDs are SHA-256 fingerprints of canonical serialized record content.

## FWD-006 — Scoring

Direction forecasts support multiclass Brier score and log loss. Return forecasts support absolute and squared error. Both paths can report baseline-relative skill:

Skill = 1 - model_loss / baseline_loss

Positive skill means lower loss than the named baseline. Negative skill means the forecast underperformed the baseline. A zero-loss baseline leaves skill undefined rather than fabricating a value.

## Authority boundary

Every foundation record is explicitly research-only and has execution_authority=false. The database enforces this invariant. NOSTRA may become a validated input to RHEN later, but this evidence layer cannot submit an order or authorize a strategy change.

## Existing ANEVUM integration

This foundation extends rather than replaces:

- NOSTRA regime state and transition research;
- NOSTRA transition calibration;
- RHEN candidate prediction evidence;
- candidate-forward-v2 outcomes;
- crypto forward evidence;
- GRAEN validation;
- VELUM replay.

Existing transition research can be migrated to the immutable forecast ledger once the runtime forecast producer is activated. No historical record should be rewritten to pretend it was persisted before its outcome.
