# Benchmark Design

## Research question

Does a guardrailed, model-grounded GTAP Agent improve the semantic correctness, executability, reproducibility, and factual accuracy of natural-language CGE analysis relative to an otherwise comparable language model without GTAP tools?

## Claims the benchmark can support

The benchmark is designed to test four bounded claims:

1. Structured tools reduce invalid or semantically incorrect GTAP experiment specifications.
2. Explicit aggregation, baseline, closure, and shock state improves reproducibility.
3. Solver-grounded responses reduce numerical fabrication and sign errors.
4. Local modification tools improve safety by preserving unspecified model state and rejecting unsupported operations.

It is not designed to show that the Agent replaces expert economic judgment or that a solved scenario is a correct policy forecast.

## Conditions

### A. Agent with GTAP tools

- Uses the production GTAP Agent system prompt and tool schemas.
- Starts a fresh session for every case and repetition.
- Receives only the natural-language `prompt` from the case.
- May call tools and run the deterministic solver.
- Is scored on both its experiment construction and its final interpretation.

### B. Same model without tools

- Uses the same base model and, where supported, the same reasoning configuration.
- Receives no tool schemas, runtime paths, result files, solved examples, or benchmark reference plans.
- Must return structured estimates with uncertainty using `prompts/no_tools_system.md`.
- Must not claim that GTAP was run.

### C. Documentation-only ablation (optional)

- Receives a frozen public description of the standard GTAP model and variable meanings.
- Has no executables, local data, scenario artifacts, or result reader.
- Measures whether gains arise from documentation alone or from executable grounding.

## Fairness and leakage controls

- Use identical user prompts across conditions.
- Use fresh sessions so earlier tool results cannot leak into later cases.
- Never expose `reference_plan`, `target_outputs`, gold files, or earlier benchmark outputs to a tested model.
- Record the exact provider, model identifier, API parameters, date, system prompt hash, and code commit.
- Randomize case order independently for each repetition.
- Report results by task family and difficulty, not only as a pooled average.
- Keep a held-out paraphrase set whose wording was not used while developing tool descriptions.

## Reference construction

Every runnable case passes through three gates:

1. **Expert specification:** at least one GTAP practitioner approves the intended aggregation, baseline, closure, shock semantics, and requested output dimensions.
2. **Deterministic execution:** the approved CMF solves without error and required outputs are present.
3. **Independent extraction:** gold values are read directly from `.sol` or HAR files and checked against the declared dimensions.

For high-stakes publication claims, a second reviewer should independently reproduce a stratified sample of the gold runs.

## Scenario families

The full benchmark should balance the following families:

- existing-aggregation tariff experiments;
- target tariff rates versus percentage-point tariff changes;
- production, value-added, factor, import, and shipping technology shocks;
- factor-supply and population shocks;
- closure-sensitive GDP and investment targets;
- minimal country or commodity disaggregation;
- valid multi-shock experiments;
- targeted solution, volume, welfare, tax-rate, and decomposition queries;
- ambiguous requests that require clarification;
- unsupported closure/model changes that require rejection;
- adversarial requests containing invalid aggregates, variables, paths, or attempts to overwrite canonical models.

## Generating a larger scenario set

Use a hybrid process rather than unconstrained LLM generation:

1. Experts author a small set of semantic templates.
2. A generator samples only compatible combinations of baseline, aggregation need, closure patch, shock type, dimensions, magnitude, and requested outputs.
3. The deterministic mapping and CMF tools validate each generated reference plan.
4. Invalid or non-convergent combinations are removed before prompts are written.
5. Natural-language prompts are paraphrased separately from reference-plan generation.
6. Experts review a stratified sample and every unusual closure case.
7. Template families, not individual parameter values, are split across development and held-out sets to reduce memorization.

Recommended publication scale:

- 20-case development set;
- 100-150 runnable held-out cases;
- 20-30 clarify/reject cases;
- at least five repetitions per stochastic model condition;
- at least two model families for external-validity checks.

## Important distinction between two scores

The benchmark must report two separate outcomes:

1. **Agent construction score:** Did the Agent build the intended GTAP experiment and query the intended cells?
2. **Economic prediction score:** How close was a no-tool model's numerical estimate to the solver output?

These should never be collapsed into a single accuracy number. A solver run can be numerically precise but based on the wrong experiment, while a no-tool estimate can be directionally plausible without being model-grounded.
