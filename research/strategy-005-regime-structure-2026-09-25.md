# Strategy 005 — regime-conditioned entry research

Status: **OFFLINE RESEARCH ONLY — NO LIVE CHANGE**

## Why Strategy 005 exists

Strategy 004 tested three materially different global entry filters:

- Candidate A increased VWAP separation and confirmation requirements;
- Candidate B required multi-bar persistence;
- Candidate C rejected mature momentum and high trend persistence.

All three failed unseen data. The Candidate C parity audit also confirmed that
the rejection survives correction to the production QQQ/SMH confirmation set.

The next question is therefore conditional:

> Does the same entry structure behave differently when the broad market is
> observably rising, mixed, or falling?

Strategy 005 does not begin by inventing another threshold. It first measures
feature/outcome relationships inside coarse market regimes.

## Confirmation set versus regime references

These roles are intentionally separate.

**Entry confirmations remain the production set:**
- QQQ
- SMH

**Read-only regime references are:**
- SPY
- QQQ
- SMH

Adding SPY as a regime reference does not make SPY an entry confirmation and
does not alter live strategy eligibility.

## Predeclared regime definition

At each candidate decision time, only already-completed reference bars are
visible.

For each SPY/QQQ/SMH reference:

- constructive = current close above session VWAP **and** positive five-bar
  close-to-close return;
- weak = current close below session VWAP **and** negative five-bar return;
- neutral = neither condition.

The broad-market label is then:

- **broad_up** when at least two references are constructive;
- **broad_down** when at least two references are weak;
- **mixed** otherwise.

There is no fitted return-magnitude threshold. The definition is intentionally
coarse and interpretable.

## Phase 1 output

For every quality-eligible production BUY opportunity, the research report now
records:

- broad-market regime at decision time;
- regime breadth score;
- per-reference VWAP state and five-bar return;
- 5- and 15-minute target-before-stop and stop-before-target rates;
- MFE, MAE, and horizon close return by regime;
- within-regime quartile behavior for the existing decision-time entry features.

The feature interaction screen is descriptive only. It cannot authorize a
trade, create Candidate D, merge PR #28, or change Railway variables.

## Anti-overfit rules

1. Candidate C's opened holdout is not reused as fresh evidence.
2. Already-viewed historical windows may be used for diagnosis and hypothesis
   formation, but not called pristine holdouts.
3. Strategy 005 must freeze one interpretable regime/entry interaction before
   any new holdout or forward-shadow evaluation.
4. Candidate complexity stays minimal: one regime condition plus one entry
   structure is preferred over compound threshold searches.
5. Failure means reject the hypothesis, not retune against the failed holdout.
6. Capital scaling remains blocked until a strategy passes historical evidence
   and separate forward shadow validation.

## Engineering boundary

This branch:
- adds no broker-order code;
- changes no production entry parameters;
- changes no position sizing;
- changes no exposure limit;
- changes no Railway variable;
- does not merge scalable-capital PR #28.

The immediate goal is evidence: determine whether the negative aggregate
expectancy is hiding a regime-specific structure worth freezing for a new
candidate.
