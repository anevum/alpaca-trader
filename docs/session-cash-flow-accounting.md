# Session cash-flow accounting

RHEN automatically reconciles owner cash deposits and withdrawals so external funding does not appear as trading performance.

The daily-loss reference is:

```
reference equity = raw broker last_equity + signed net external cash flow
trading day P&L = raw broker equity - reference equity
```

Deposits are positive and withdrawals negative. The daily loss threshold is unchanged. Percentage-based sizing uses the same adjusted reference, so deposited capital becomes usable capital without being counted as profit and withdrawn capital is removed from the exposure budget. Raw Alpaca balances remain intact.

## Automatic detection

Each account refresh reads Alpaca's `TRANS` account-activity feed for the current New York session and accepts only:

- `CSD` — cash deposit, positive `net_amount`
- `CSW` — cash withdrawal, negative `net_amount`

RHEN aggregates those records and binds the result to the broker's `last_equity` baseline. Dividends, interest, fees, fills, journals, corporate actions, and other activity types are never silently classified as owner funding.

The generated evidence reference contains the session date, activity count, and a hash of the Alpaca activity IDs. Raw activity IDs are not exposed in the public performance surface.

If the transfer endpoint is unavailable, returns malformed evidence, or conflicts with a same-session manual manifest, RHEN fails closed for new entries by setting `cash_flow_error`. Protective exits remain allowed.

## Manual fallback

`SESSION_CASH_FLOW_ADJUSTMENT` remains available as an operator-reviewed fallback. It is optional JSON with exactly six string fields:

| Field | Meaning |
| --- | --- |
| `session_date` | New York calendar date in YYYY-MM-DD form. |
| `effective_at` | Timezone-aware instant after the recorded flows affected the account. |
| `net_external_cash_flow` | Signed aggregate external flow since the broker prior-close baseline. |
| `expected_last_equity` | Exact broker prior-close equity against which the flows were reconciled. |
| `run_id` | Matching RHEN run identifier. |
| `evidence_ref` | Durable reference to the operator-reviewed transfer evidence. |

When both manual and automatic evidence exist for the same session, the amounts, prior-close baseline, and run ID must agree exactly. RHEN uses the broker-derived evidence once they agree, preventing double counting. A mismatch blocks new entries.

A manual manifest expires at the New York date boundary. Wrong run, mismatched prior close, invalid broker equity, and not-yet-effective manual evidence also block new entries.

## Telemetry and reporting

`cash_flow_accounting`, `risk_reference_equity`, adjusted `day_pnl`, and adjusted drawdown are persisted in reconciliation snapshots and surfaced through Command. The broker's raw `equity` and `last_equity` are preserved for audit.

Alpaca documents `TRANS` as cash transactions and identifies `CSD` as cash deposit (+) and `CSW` as cash withdrawal (-). The non-trade activity `net_amount` is the signed cash impact.

Tests use synthetic activity data and mocked broker reads. They cover automatic deposits, automatic withdrawals, aggregation, ignored non-transfer activities, manual/automatic agreement and disagreement, endpoint failure, daily-loss behavior, exposure sizing, date rollover, protective exits, and Command/reconciliation consistency.
