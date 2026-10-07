# Research, validation, and promotion

## Two loops

### Fast runtime loop

Runs without GPT/Work credits:

- NOSTRA state calculation;
- approved profile selection;
- capital throttle;
- deterministic policy application;
- logging and safety fallbacks.

### Slow improvement loop

Uses existing GRAEN/Research Agent/VELUM and may optionally use a Work/Codex research pass:

- analyze realized trades, candidate outcomes, MFE/MAE, cost/slippage and regime-conditioned expectancy;
- identify profile weaknesses or new hypotheses;
- freeze the proposed change before evaluation;
- test in replay/counterfactuals;
- run no-lookahead fixed-versus-adaptive shadow;
- use untouched holdout;
- promote only with attributable evidence and rollback values.

## Existing gates preserved

The current ASC-007 minimums remain the default floor unless a separately reviewed methodology change replaces them:

- >=10 independent evaluation sessions;
- >=100 complete candidates;
- >=30 differential decisions;
- >=95% forward coverage;
- >=60% positive non-zero session deltas;
- exact two-sided sign-test p <= 0.10;
- mean adaptive-minus-fixed candidate utility >= 0.5 bp after frozen costs.

The current GRAEN holdout floor remains:

- method frozen before holdout;
- quarantine not accessed during development;
- >=5 independent holdout sessions;
- >=50 complete holdout candidates;
- >=95% coverage;
- explicit PASSED status.

## 4.4 promotion object

Extend ASC-008 from single-parameter activation contracts to support `policy_profile_release` contracts.

A profile-release contract must contain:

- profile ID/version;
- source 4.3/4.4 strategy version;
- NOSTRA methodology version/fingerprint;
- complete profile values;
- hard-envelope assumptions;
- shadow lineage ID and result;
- VELUM replay/validation artifact IDs;
- holdout artifact ID;
- GRAEN validation result;
- rollback profile/version;
- authorization reference;
- activation timestamp;
- policy-library fingerprint.

## Optional Work research worker

A model-driven research pass is useful but not required for runtime operation.

Recommended cadence:

- daily after-close only when there is meaningful new evidence or a specific review item;
- weekly broader synthesis for strategy-family/profile development;
- ad hoc after a material regime-performance failure, not continuously every few minutes.

The model worker may:

- search current external research;
- compare new research with RHEN evidence;
- propose hypotheses and falsification tests;
- write bounded candidate profile changes;
- generate implementation/research packages.

It may not:

- submit orders;
- change Railway variables;
- widen hard risk limits;
- merge/deploy a protected release without operator authority;
- reinterpret an under-sampled result as a pass.

## External research evidence contract

Any model-research artifact intended to influence development must record:

- source URLs/citations and retrieval date;
- claim supported by each source;
- relationship to current RHEN evidence;
- exact proposed change;
- expected mechanism;
- failure/falsification criteria;
- whether the claim is literature, market convention, vendor documentation, or RHEN-specific observation;
- no automatic promotion flag.
