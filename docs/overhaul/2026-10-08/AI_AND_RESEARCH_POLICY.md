# AI and Research Policy

## Principle

Use advanced models where language/reasoning adds leverage. Do not pay to keep them resident where mathematics, deterministic code, or an operator workflow is better.

## Production prohibition

The permanent RHEN production runtime must not depend on an LLM/API-model call for:

- market scanning;
- candidate ranking required for current live execution;
- position sizing;
- entry authorization;
- stop/target calculation;
- exits;
- reconciliation;
- risk circuit breakers;
- health;
- scheduler correctness;
- configuration identity;
- restart recovery.

If all model providers are unavailable, RHEN must continue its authorized deterministic behavior or fail safely according to existing execution rules.

## Permitted AI use

Advanced models may be used for:

- interpreting accumulated evidence;
- suggesting new hypotheses;
- literature/web research;
- designing bounded experiments;
- reviewing unexpected strategy behavior;
- explaining multi-factor failures;
- proposing code changes;
- generating operator-facing research summaries.

## Preferred execution surface

Default:

- ChatGPT Work or an explicit operator session.

Why:

- it uses intelligence already being paid for;
- it avoids a continuously billed model worker;
- it keeps research reasoning outside the broker-write runtime;
- it preserves a human promotion gate;
- it makes evidence and proposed changes reviewable before code is modified.

## Evidence package contract

Every AI-assisted research review should receive a canonical, bounded package containing at minimum:

- release/strategy identity;
- observation window;
- trade count/sample sufficiency;
- realized P&L/return metrics;
- expectancy/profit factor where meaningful;
- MFE/MAE;
- spread/slippage evidence when available;
- entry/exit reason distributions;
- rejection funnel;
- performance by symbol/time/regime;
- NOSTRA forecasts and matured outcomes;
- active parameter/config fingerprint;
- incidents/data-quality warnings;
- latest VELUM evidence;
- open experiment queue;
- explicit authority limits.

The package should be machine-readable JSON plus a concise Markdown summary.

## AI output contract

An AI recommendation is not a strategy update.

It must produce:

- observed problem;
- supporting evidence;
- hypothesis;
- single bounded change or small controlled set;
- expected mechanism;
- replay/validation plan;
- minimum evidence requirement;
- success metric;
- failure/rejection condition;
- risks/confounders;
- files/parameters likely affected.

The next action is experiment creation, not live mutation.

## API model fallback

If API automation is later reintroduced:

- use a separate ephemeral worker/job;
- do not provide Alpaca order credentials;
- enforce a hard per-run and daily budget;
- persist request/response provenance;
- require deterministic schema validation;
- require explicit operator promotion;
- shut down when the bounded task completes.

## Secrets target

After migration, the permanent RHEN service should not require an `OPENAI_API_KEY`.

Removal must occur only after all remaining production code paths that expect the key have been disabled/refactored and tested.
