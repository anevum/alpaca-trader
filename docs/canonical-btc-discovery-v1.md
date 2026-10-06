# Canonical BTC discovery and forward paper

This extends backend main 9e502b6 and frontend main 6fd293d. It does not replace the unified RHEN routing or Command v3 cutover.

RHEN Core's existing /data/rhen-core.db is the sole durable assignment authority. The separate btc-canary-001-paper service does not open or write that database. It uses the authenticated RHEN control path and the existing ingest credential. No additional Railway service is required.

## Lifecycle and evidence

The immutable grammar contains eight BTC direct parameter candidates. IDs are derived from the methodology and complete parameter payload. Development lasts 270 days, validation 120 days, and holdout 120 days. The initial UTC end date and all half-open boundaries freeze in RHEN Core before data is requested. Each stage has 35 days of input-only warmup. Only development ranks candidates; exactly one development finalist opens validation and holdout. Failure is terminal for that finalist. Search exhaustion is explicit rather than recycling confirmatory evidence.

Each stage uses nine scenarios: LOW/BASE/HIGH costs crossed with next-open, one-extra-hour, and two-extra-hour entry delays. Fees are 25/35/50 bps per side, spread is 10/20/40 bps, and slippage is 5/10/20 bps per side. These are frozen conservative assumptions, not a claim about the account's current fee tier. Zero-cost evidence cannot pass. Every scenario needs at least 20 closed trades, 10 independent entry dates, positive net expectancy, profit factor >=1.2, and drawdown <=15%. Validation and holdout expectancy must retain at least 50% of the preceding stage's expectancy in every scenario.

VELUM independently fetches and replays all three frozen windows using the existing broker-isolated ContinuousReplayEngine BTC adapter. It checks dataset and full result fingerprints and repeats the gates. This is independent engineering verification of the same historical evidence, not an additional statistical holdout. Interrupted confirmatory stages burn their opened evidence and reject the finalist. Interrupted development can resume without opening later stages.

## Paper handoff and lineage

The existing scheduler registry owns graen.btc.discovery every five minutes. In the unified deployment where the full scheduler was previously disabled, only this new BTC workflow is activated; equity reporting clocks remain unchanged. Obsolete GRAEN BTC autoruns, legacy BTC forward-shadow loops, and duplicate crypto replay schedules are disabled in the unified runtime. Historical handlers and evidence remain available.

Canary variables:

- BTC_DISCOVERY_PAPER_SELECTION=true
- BTC_DISCOVERY_CORE_URL=http://alpaca-trader.railway.internal:8080/v1/btc-discovery

GET /v1/internal/crypto-paper-assignment is an authenticated alias of GET /v1/btc-discovery/assignment. Both require x-anevum-scheduler-token using the existing RHEN ingest credential. The response carries the full parameter fingerprint, immutable chronological contract, lifecycle history, gate evidence, independent VELUM receipts, and approval fingerprint. The canary independently verifies the complete proof before loading a candidate. Invalid or unavailable assignments close new entries while existing protection remains available.

A new assignment activates only when paper crypto positions and BTC open orders are absent. Open positions retain their current active candidate and expose PENDING_STRATEGY_CHANGE. RHEN Core retains the active candidate identity and parameters for canary restart restoration. Activation acknowledgment is durable before candidate execution.

Candidate order IDs preserve anevum-crypto- and contain a compact candidate fingerprint tag, with maximum length 48. Both broker-derived scorecards and durable forward evidence filter by that exact candidate tag. Old baseline and other-candidate orders never enter a new candidate's statistics. Fill IDs deduplicate observations and partial exits do not inflate episode counts.

Forward paper requires 30 closed episodes, 30 independent trading dates, and 30 elapsed days, fresh error-free observations, positive expectancy after conservative HIGH friction, profit factor >=1.2, and drawdown <=15%. Execution failures or drawdown violations close candidate entries. Success stops at ELIGIBLE_FOR_REVIEW and closes new experimental entries; it never becomes LIVE. The historical delay stress remains part of the required assignment proof.

RHEN-BTC-DIRECT-003 remains the live BTC strategy and btc_direct_live_signal remains signal-only. Candidate state cannot change live selection, account mode, sizing, risk configuration, equity strategy, or broker-write authority. The default BTC strategy is tested against a frozen original-v3 regression oracle.

Command shows actual stage, bounded progress, candidate identity/fingerprint, measured scenario metrics, VELUM evidence, rejections, and candidate-isolated forward paper progress. A healthy heartbeat does not imply active research. A deployment with no passing candidate truthfully shows no eligible paper assignment.

## Provider-gap handling

Historical BTC research remains fail-closed and never interpolates or forward-fills prices. RHEN first attempts bounded reconstruction from observed source minute bars. If Alpaca has no source observations for exactly one interior hourly interval, the research corpus may retain that timestamp as an explicit provider gap under `isolated_provider_gap_segment_reset_v1`.

A provider gap is a hard replay boundary: no position or delayed entry may cross it, incomplete episodes are excluded rather than assigned an inferred exit, and strategy state must rebuild a full 35-day (840-hour) warmup from contiguous observed bars before scoring resumes. More than one missing hour, or a missing boundary bar, still fails closed. The gap timestamp and policy are included in the dataset fingerprint and replay assumptions. This affects research/VELUM evidence only; it cannot alter the live BTC strategy or broker-write authority.
