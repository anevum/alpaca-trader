# Package revision 003 - Visual Intelligence integration

Canonical package id: `ANEVUM.RHEN.PACKAGE.2026-10-06.003.V4-4-VISUAL-INTELLIGENCE`

This revision supersedes the prior RHEN 4.4 real-time/extended package for implementation handoff.

## Added

- truthful real-time Visual Intelligence Layer;
- live candlestick and overlay contracts;
- 24-symbol scanner visual contract;
- execution/fill/position overlays;
- account/equity/drawdown/exposure visualization;
- NOSTRA forecast path and uncertainty-band contract;
- feed-health/system-health visuals;
- VELUM replay visual contract;
- visual provenance schema;
- visual acceptance tests;
- bounded series/ring-buffer and incremental update requirements.

## Preserved

- RHEN 4.3 as champion and rollback anchor until 4.4 gates pass;
- event-driven market/broker ingestion;
- free-resource Basic mode;
- independent extended-session promotion;
- Adaptive Policy Control validation gates;
- no crypto;
- no options broker writes;
- no short equities;
- no leverage expansion;
- no LLM/model call in live order path.

## Locked visual principle

Every dynamic visual must be attributable to OBSERVED, DERIVED, FORECAST, or OPERATIONAL state. Missing market data remains missing. Forecasts are explicit model outputs with expiry and uncertainty. Visual smoothness must never create synthetic market evidence.
