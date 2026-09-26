# Corpus integrity diagnosis — 2026-09-26

Status: **RAW-BAR 80% RULE FALSIFIED AS A COMPLETENESS METRIC**

## Scope

This diagnosis uses development window `dev-01` only: 2026-01-05 through
2026-01-23 on Alpaca IEX one-minute bars.

Validation, holdout and quarantine windows were not opened.

A single false negative is sufficient to falsify the old completeness
definition. The question is not whether every future window passes. The
question is whether raw bar count versus the densest symbol measures transport
completeness. It does not.

## Reproduction of the old failure

Alpaca's trading calendar contains 14 regular trading sessions in `dev-01`.

The old rule used the densest raw symbol in the window as the denominator.
QQQ returned 5,975 raw IEX minute bars, so the old 80% threshold was 4,780
bars.

Eight of the 36 frozen candidates fell below that threshold:

| Symbol | Raw IEX bars | Old ratio vs QQQ | Expected sessions represented |
| --- | ---: | ---: | ---: |
| CAT | 3,806 | 63.7% | 14 / 14 |
| DIA | 4,161 | 69.6% | 14 / 14 |
| COST | 4,188 | 70.1% | 14 / 14 |
| LLY | 4,237 | 70.9% | 14 / 14 |
| BA | 4,428 | 74.1% | 14 / 14 |
| XRT | 4,446 | 74.4% | 14 / 14 |
| XLC | 4,468 | 74.8% | 14 / 14 |
| XLY | 4,578 | 76.6% | 14 / 14 |

The old rule therefore retained 28/36 candidates, or 77.8%, and caused the
shared-panel integrity gate to fail.

Every rejected symbol nevertheless had regular-session data on all 14 expected
trading days. Their histories began on the first development session and
extended through the final development session.

The raw-count rule was therefore rejecting intact histories because IEX emits
different one-minute bar densities for different symbols. It was measuring IEX
trade-print density, not historical-session completeness.

## Replacement rule

Symbol-level corpus completeness now requires:

1. the historical fetch batch to finish pagination with no page token left; and
2. the symbol to contain regular-session data on 100% of the expected Alpaca
   trading-calendar sessions in that window.

This is not a relaxation. A symbol with 13/14 sessions is incomplete even if it
has more raw bars than another symbol. A sparse IEX symbol with 14/14 sessions
is complete for transport/session integrity.

The old ratio is retained as `iex_bar_density_ratio` for sampling-quality
diagnostics only. It cannot qualify or disqualify a symbol.

The separate 80% shared-panel gate remains unchanged. If genuine missing
sessions remove enough symbols that fewer than 80% of the frozen candidate
panel survives across the relevant windows, the corpus still fails.

## Consequence

The corpus is no longer allowed to pass merely because bar counts are close to
one another, and it is no longer allowed to fail merely because IEX activity
differs by symbol.

Transport integrity and sampling density are now separate concepts.
