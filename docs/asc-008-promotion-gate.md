# ASC-008 Promotion Gate Contract

Status: HARD-GATED / NO DEPLOYMENT AUTHORITY

ASC-008 defines the last boundary between successful adaptive research and a
possible production change.

It intentionally does not deploy anything.

## Required gates

A bounded parameter proposal must satisfy all of the following before it is
eligible to be shown for proposal-specific human authorization:

- parameter is in the explicitly allowed adaptive policy;
- proposal still has zero execution authority;
- automatic application remains disabled;
- proposal source strategy exactly matches the active validation lineage;
- evidence integrity is HEALTHY;
- ASC-007 fixed-vs-adaptive shadow validation passed;
- ASC-007 includes at least 10 independent sessions;
- ASC-007 includes at least 100 complete candidates;
- ASC-007 includes at least 30 differential decisions;
- ASC-007 forward coverage is at least 95 percent;
- canonical research evidence includes at least 10 independent sessions;
- at least 100 eligible candidates;
- at least 30 directly attributed closed trades;
- direct attribution coverage is at least 99.5 percent;
- 15-minute forward coverage is at least 95 percent;
- GRAEN frozen validation passed;
- GRAEN walk-forward validation passed;
- GRAEN selection-bias control passed;
- GRAEN dependence control passed;
- GRAEN multiple-testing control passed.

## Human authorization

General trading authorization is not sufficient.

Authorization must name the exact:

- proposal ID;
- source strategy version;
- parameter;
- proposed value;
- authorization reference;
- authorizing human;
- authorization timestamp.

A mismatch invalidates the authorization.

## Activation contract

When all research gates and the exact human authorization pass, ASC-008 may
construct an immutable activation contract containing the exact change and
rollback value.

The contract still states:

- deployment requires a separate operator action;
- no production mutation has occurred;
- no broker call has occurred;
- no Railway change has occurred;
- ASC itself has no execution authority.

Thus ASC-008 can prove that a change is ready to be deliberately activated,
but it cannot activate the change.
