# Scoring Rubric

## 1. Behavioral correctness

Applied to every condition:

- `run`: proceeds with a sufficiently specified supported experiment;
- `clarify`: asks a focused question when baseline or another material choice is missing;
- `reject`: explains that the requested operation is outside the supported model/tool boundary.

Score: exact match, reported separately for run, clarify, and reject cases.

## 2. Experiment-construction score

Applied to `agent_tools` runnable cases. Award one point for each exact semantic component:

- aggregation decision;
- minimal aggregation diff;
- baseline identifier;
- model identifier bound to the aggregation;
- closure identifier and local swaps;
- shock variable semantics;
- shock dimensions;
- shock value mode and magnitude;
- required result source/view;
- required result dimensions.

Report the mean component accuracy and the percentage of cases with a completely correct experiment. A successfully solved but semantically wrong experiment is not counted as correct.

## 3. Execution and reproducibility

Applied to `agent_tools` runnable cases:

- CMF generated;
- solver return code is zero;
- no error flag;
- solver warnings are extracted verbatim and classified;
- all required outputs present;
- numerical-accuracy diagnostic passes the declared threshold;
- immutable run record contains all required provenance;
- rerun from the recorded artifacts reproduces target values within tolerance.

Recommended numerical tolerance for repeated deterministic extraction: absolute difference no greater than `1e-6`, unless a file format exposes lower precision.

A zero return code is not sufficient for gold status. Unresolved coefficient-information, set/element, closure, range, or numerical warnings fail the reproducibility gate until an expert documents why they are harmless.

## 4. Numerical prediction accuracy

Applied primarily to `no_tools`, using solver outputs as reference values.

For each requested target report:

- sign accuracy;
- absolute error;
- symmetric absolute percentage error: `2 * |estimate - truth| / (|estimate| + |truth|)`;
- interval coverage;
- interval width;
- abstention rate.

Near-zero truth values require absolute-error bands because percentage errors become unstable. Thresholds must be declared before examining test results.

## 5. Grounded interpretation

Applied to the final Agent response:

- all numerical claims trace to returned tool rows or the automatic run report;
- units and dimensions are correct;
- aggregation limitations are stated when natural-language entities were aggregated;
- causal language does not exceed the comparative-static experiment;
- no unsupported files, variables, warnings, or model features are invented.

Use blinded human review for narrative claims and retain the supporting artifact pointer for every scored statement.

## 6. Efficiency

Report, but do not treat as a substitute for correctness:

- end-to-end latency;
- model tokens and API cost;
- tool-call count;
- failed/retried tool calls;
- solver time;
- human interventions.

## Primary endpoints

The recommended primary endpoints are:

1. completely correct experiment rate;
2. successful and semantically correct solve rate;
3. grounded numerical-claim rate;
4. no-tool sign accuracy and symmetric percentage error;
5. correct clarification/rejection rate.
