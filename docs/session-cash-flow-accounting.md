# Session cash-flow accounting

Candidate implementation. The optional manifest defaults to empty; no trading or deployment is activated by this change.

The daily-loss reference must distinguish market P&L from external deposits and withdrawals. The candidate uses:

```
reference equity = raw broker last_equity + signed net external cash flow
trading day P&L = raw broker equity - reference equity
```

Deposits are positive and withdrawals negative. The daily loss threshold is unchanged. Percentage-based sizing uses the same reference so withdrawn capital does not remain in the exposure budget. Raw broker balances remain intact; new status and reconciliation fields retain the reference, adjusted P&L, and evidence metadata.

`SESSION_CASH_FLOW_ADJUSTMENT` is an optional JSON string with exactly six string fields:

| Field | Meaning |
| --- | --- |
| `session_date` | New York calendar date in YYYY-MM-DD form. |
| `effective_at` | Timezone-aware instant after the recorded flows affected the account. |
| `net_external_cash_flow` | Signed aggregate external flow since the broker prior-close baseline. |
| `expected_last_equity` | Exact broker prior-close equity against which the flows were reconciled. |
| `run_id` | Matching RHEN run identifier. |
| `evidence_ref` | Durable reference to the operator-reviewed transfer evidence. |

The manifest is a manual reconciliation bridge. It does not discover or independently verify transfers. It must not contain lifetime flow totals or be inferred solely from a balance decline. Subsequent transfers require renewed reconciliation. Fees, dividends, interest, manual trades, and market P&L must not be silently classified as owner capital movement.

A manifest automatically expires at the New York date boundary. During its active session, wrong run, mismatched prior close, invalid broker equity, and a not-yet-effective manifest block new entries. Protective exit validation does not use this gate. Repeated annotation is idempotent.

An operator must review any intended live use and record the actual activation boundary separately from prior observations. Empty configuration retains the existing guard. Historical records are not rewritten. This patch does not address missing telemetry, automatically relax entry filters, or guarantee any trades.

Tests use synthetic data and mocked broker reads only. They cover withdrawal-only balance changes, real losses at the configured limit, deposits hiding losses, reduced exposure capacity, date rollover, malformed manifests, mismatches, protective exits, and consistent runtime/Command accounting.

Alpaca's primary reference distinguishes non-trade cash deposits/withdrawals from fills and documents signed net amounts: https://docs.alpaca.markets/us/docs/account-activities .
