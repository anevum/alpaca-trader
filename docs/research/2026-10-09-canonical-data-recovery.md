# RHEN — canonical evidence recovery and prospective research handoff

**Classification:** Private founder trading research. **Date:** October 9, 2026. **State:** Partial historical evidence produced; full candidate population **not recovered** and **not certified**.

## What was actually produced

The owner's private evidence package (`rhen_partial_canonical_research_staging_2026-10-09_PRIVATE.json`, NOT committed to GitHub) stages 38 already-reconciled Alpaca closed trades across October 7–9, pre-entry IEX-derived features, original prices/quantities and the predeclared rule filters. Trade-day quantities are 19 / 7 / 12. Gross P/L is approximately −$0.09571 for the full sample. Historical unexecuted/rejected candidates, 15-minute measured outcomes for the entire decision population, exact as-of dynamic-universe membership and complete broker activity/order-ID lineage were **not** fetched. These cannot be replaced with plausible bar-derived synthetic candidates.

The local stress run includes the original observed exit prices and models **additional hypothetical price friction** at 1, 2 and 5 basis points *per side*. Executed Alpaca prices already incorporate actual execution quality; this stress is NOT a realized fee statement and cannot be interpreted as a full replay.

## Why the old Core database alone is insufficient

On `anevum/rhen/main` as inspected Oct 9, 2026:

* `app/rhen_core/store.py::compact_cycle()` persists all qualifying or non-rejected candidates but only **the first three rejected candidates** (sorted by symbol) from each decision cycle. It writes summary counts, but **not** all remaining rejected records to Core `candidates`.
* `app/rhen_core/store.py::ingest_events()` sheds all noncritical events under analytics-storage pressure. `decision_cycle` is not on its `CRITICAL_EVENT_TYPES` list, so during shedding even the sampled cycle may disappear.
* `app/rhen_core/store.py::prune()` retains ordinary candidate rows for only **three days under storage-warning conditions**, seven otherwise; noncritical cycle retention is similarly bounded. Records already discarded cannot be recovered from this copy.
* `app/rhen_core/store.py::_candidate_report_rows()` reads a recent bounded 10,000 candidates before session filtering. The older Foundation report reader itself caps some session projections at 5,000 candidates. Pagination/source-population proofs are absent.
* `app/rhen_core/store.py::_candidate_report_rows()` returns `forward_outcomes` as a *mapping* keyed by horizon and `scan_cycle.data_status='compact_core_v3'`. The draft candidate auditor previously expected an `outcomes` list with `data_status=ok`. This mismatch has been corrected on **draft** PR #466, with an explicit full-population attestation gate.

**Implication:** Reading from `/data/rhen-core.db` can recover surviving candidate rows and their recorded outcomes; it cannot create missing historical decisions or certify that all scan cycles reached Core. Check the separately mirrored original (pre-compaction) Foundation stream before declaring the historical corpus unrecoverable there.

## Operator retrieval — *no credentials shared with ChatGPT or GitHub*

1. From a trusted operator shell with authorized access to the RHEN private `/data` volume, make a **consistent SQLite backup** using SQLite's online Backup API to a secure, nonpublic destination **outside** the active database volume. Do not use raw `cp` of a live WAL database without coordinated snapshotting. Do not edit or restart the trading service just to obtain this backup. Prefer a supported maintenance/snapshot facility if available.
2. Transfer the backup only over the existing authenticated operator channel. Run the supplied exporter against the **copy**, never against the live writer:

```bash
python3 -m scripts.rhen_canonical_recovery_export \
  --db /secure/rhen-core-snapshot.db \
  --start 2026-10-07 --end 2026-10-09 \
  --output /secure/rhen-core-recovered-2026-10-07_to_09.json
```

3. This opens SQLite with `mode=ro` and `PRAGMA query_only=ON`; it writes only an explicit **new, private** JSON file with mode 0600, refuses to overwrite, queries bounded windows and marks missing candidate or outcome evidence rather than inventing values. It never imports a broker SDK, touches a trading endpoint, mutates Core or promotes a champion. Preserve its SHA-256 source fingerprint.
4. Independently check whether the `FOUNDATION_SHADOW_ENABLED` path was active and successfully delivered raw `decision_cycle` events **before** Core compaction. Presence of an environment-variable name is NOT proof it was enabled. Check its outbox delivery counts, errors and endpoint identity using authorized tooling. The Supabase connection available to this chat was refusing connections; **no authenticated Foundation query was performed here**.
5. If authorized to run **read-only** SQL against the original Foundation Postgres, start with the companion SQL file. It tests full candidate-array presence and count equality per `rhen.events` decision cycle. Use a read-only database role; never paste database URLs, API keys or event payloads into GitHub issues or an LLM chat.
6. To certify an *entire session*, also reconcile emitter/ingest cycle receipts (not only events that survived in a database), demonstrate no dropped/shedded cycles, preserve decision-time feature/context and an as-of universe, and attach candidate outcomes and complete Alpaca activity/order-ID pages. If anything is absent, use `AWAITING_COMPLETE_CANONICAL_EVIDENCE`.

## Follow-up research protocol

**Frozen challenger for future data only:** `market-regime-persistence-v1`. At entry evaluation, independently measure SPY, QQQ and SMH regime status over the **last three completed evaluation minutes**. Admit only if at least two of the three benchmarks are constructive in at least two of those three observation points. Feed/source identity must be declared; missing/stale confirmation bars are `UNKNOWN` and block *research eligibility*, never silently count as bearish. Keep every other control strategy/risk variable frozen. Retain the unfiltered production 4.3.2 policy as the reference; do not alter it.

The treatment was **proposed after inspecting October 7–9 trades**, so these three sessions are **development evidence, not an untouched holdout**. Future work must first acquire complete, as-of decision population; then freeze rules, use time-separated walk-forward validation and a previously unseen holdout, carry hypothetical costs, track all candidates not just executed fills, record skipped winners and trade replacement costs, test sell-stop and thesis-state parity and correct for the number of evaluated hypotheses. The prior same-entry, original-exit exercise is **not** an executable strategy result.

**Promotion:** Blocked. No automatic trading change. Require separate verified risk/owner release and exposure preflight before any `main` merge/deploy.

## Verification

Local `pytest -q test_rhen_canonical_recovery_export.py`: **7 passed**. These tests use synthetic temporary SQLite data and cannot prove the production source is complete. The current branch's GitHub CI must pass independently after the schema/provenance patch. Current Railway production remains the previous `main` deployment.