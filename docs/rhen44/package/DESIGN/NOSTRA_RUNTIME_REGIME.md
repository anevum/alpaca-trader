# NOSTRA live regime layer

## Existing state

The repository already has `app/research_agent/nostra_regime.py` and `docs/asc-002-nostra-regime.md`. It produces a transparent point-in-time state vector and probabilities across:

- TREND_EXPANSION
- TREND_DECAY
- BROAD_ADVANCE
- BROAD_DECLINE
- ROTATION
- HIGH_VOLATILITY
- LOW_VOLATILITY
- CHOP
- OPENING_DISCOVERY
- MIDDAY_COMPRESSION
- LATE_SESSION_EXPANSION
- UNKNOWN

4.4 should reuse/factor this logic, not invent a second classifier.

## Production-safe implementation

Create a runtime wrapper under `app/nostra/` that:

- consumes only data timestamped <= observation time;
- builds the same canonical feature vector used by research;
- calls the pure deterministic classifier;
- persists a compact state observation;
- exposes state to the Adaptive Policy Controller;
- does not call the broker or mutate strategy/risk configuration;
- keeps methodology/version/fingerprint explicit.

Recommended module:

`app/nostra/runtime_regime.py`

## Required live features

Preserve current ASC-002 features and derive them from RHEN's already-fetched data where possible:

- SPY/QQQ/IWM 5-minute and 15-minute returns;
- candidate breadth above VWAP;
- candidate breadth positive over 5 minutes;
- median fast/slow trend spread;
- median VWAP edge;
- median absolute 5-minute movement;
- cross-sectional dispersion;
- median relative volume;
- eligible-candidate ratio;
- realized short-horizon volatility;
- average current spread;
- session segment.

Avoid extra API calls when the 4.3 discovery/opportunity engine already has the inputs.

## State quality

Every observation must include:

- `observed_at`;
- `feature_as_of`;
- `methodology_version`;
- `regime_probabilities`;
- `primary_regime`;
- `confidence`;
- `unknown_probability`;
- `market_familiarity`;
- missing/stale feature list;
- feature fingerprint.

## Calibration

NOSTRA live state may drive a promoted policy only when the classifier version is the same version validated by the profile lineage. A new classifier version starts a new validation lineage.

Do not let a retrained model silently replace the deterministic classifier. Learned models may become challengers after separate validation.
