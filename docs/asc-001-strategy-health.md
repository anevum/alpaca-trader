# ASC-001 Strategy Health

Status: SHADOW / READ-ONLY

ASC-001 is the first implementation slice of IREN Adaptive Strategy Control.

It computes a multidimensional strategy-health snapshot from canonical RHEN
reports plus optional NOSTRA and parameter-pressure inputs.

## Authority boundary

ASC-001 has no authority to:

- place or cancel broker orders;
- mutate the live strategy;
- mutate risk or sizing;
- change Railway variables;
- promote a challenger;
- change production configuration.

Its output is diagnostic research evidence only.

## Health dimensions

The v1 snapshot exposes:

- edge
- regime
- calibration
- execution
- distribution
- parameter pressure
- challenger pressure
- evidence integrity

Each dimension reports a status, reason codes, source metrics, and an
observation count when meaningful.

Dimension states are:

- HEALTHY
- WATCH
- DEGRADED
- BLOCKED
- COLLECTING

## Control states

ASC-001 derives one supervisory state:

- NORMAL
- ADAPT
- RESEARCH
- DEFENSIVE

These are recommendations about research posture. In v1 they are not execution
instructions.

Evidence-integrity failure takes precedence and produces DEFENSIVE.

Execution degradation produces DEFENSIVE.

Material edge, regime, calibration, distribution, or persistent parameter
pressure degradation produces RESEARCH.

Bounded watch conditions in edge/regime/distribution/parameter pressure may
produce ADAPT.

Immature evidence remains COLLECTING and does not force a strategy change.

## Champion/challenger behavior

ADS-002 v2 challenger evidence is consumed only as research evidence.
A mature high-confidence challenger can create RESEARCH pressure, but ASC-001
still reports promotion_authorized=false.

The existing ADS-002 minimum evidence gates and human-review requirement remain
authoritative.

## Integration

The RHEN Research Agent status endpoint includes the ASC-001 snapshot under:

`adaptive_strategy_control`

This keeps ASC observable without introducing a dependency from the live trader
to the research system.

## Next build

ASC-002 will add the initial NOSTRA market-state/regime representation. Until
then, regime, calibration, and distribution dimensions remain COLLECTING when
NOSTRA inputs are absent.
