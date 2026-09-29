# ASC Parameter Pressure

Status: SHADOW / READ-ONLY

Parameter pressure measures whether validated ASC-005 counterfactual preferences
repeatedly push an adaptive parameter toward the edge of its approved envelope.

Only validity-passed rolling counterfactual selections count.

For each parameter the monitor records:

- valid observations;
- lower-bound hits;
- upper-bound hits;
- boundary fraction;
- dominant boundary;
- recent validated requested values.

Fewer than five valid observations remains COLLECTING.

At five or more valid observations:

- boundary fraction below 0.30 -> HEALTHY
- 0.30 through 0.59 -> WATCH
- 0.60 or greater -> DEGRADED

A DEGRADED result does not expand the allowed parameter range. It tells IREN
that the current architecture may deserve structural research.
