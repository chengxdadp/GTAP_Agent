# GTAP Agent Benchmark

This directory defines a reproducible benchmark for evaluating whether a tool-grounded language-model agent can construct, solve, and interpret GTAP experiments more reliably than the same language model without GTAP tools.

The benchmark is about model-use reliability, not whether a language model can memorize plausible economic numbers. Solver outputs, rather than the Agent's prose, are the numerical reference.

## Experimental conditions

The required comparison uses the same model family and the same user prompt under two conditions:

1. `agent_tools`: the model receives the GTAP Agent system prompt and registered tools. It may modify an existing aggregation locally, patch an approved closure, construct shocks, run GTAP, and query results.
2. `no_tools`: the model receives no GTAP tools, no result files, and no previously solved scenarios. It must either estimate the requested outcomes with uncertainty, ask for necessary clarification, or state that a request cannot be quantified reliably.

An optional third condition, `documentation_only`, gives the model static GTAP documentation but no executable tools. It separates the value of documentation from the value of model-grounded execution.

## Directory layout

```text
benchmark/
  README.md
  design.md
  rubric.md
  config.json
  prompts/
    no_tools_system.md
  schemas/
    case.schema.json
    prediction.schema.json
    run_record.schema.json
  scenarios/
    pilot.jsonl
  gold/
    README.md
  runs/
    README.md
  validate.py
```

Only the `prompt` field from a case is sent to the evaluated model. The `reference_plan`, `target_outputs`, and rubric fields are hidden evaluation metadata and must never be added to the model context.

## Unit of evaluation

One benchmark case contains:

- a natural research request written without tool names or GTAP variable codes;
- the expected behavioral outcome: `run`, `clarify`, or `reject`;
- a hidden expert reference plan for aggregation, baseline, closure, shocks, and result queries;
- the numerical outputs that must be extracted from a successful solver run;
- tags used for stratified reporting.

Each model-condition-case combination is a run. Runs must be immutable and must retain the exact prompt, model identifier, code commit, system-prompt hash, tool calls, raw response, artifacts, latency, token usage, and parsed predictions.

## Benchmark workflow

1. Validate the case file:

   ```powershell
   python benchmark\validate.py
   ```

   Run one complete pilot repetition and store the immutable log in SQLite:

   ```powershell
   python benchmark\run.py --repetitions 1 --database benchmark\log.sqlite
   ```

   The database contains normalized batch, case, run, API-call, event, and tool-call tables. Request payloads never contain the API key. Solver artifacts remain at the paths returned by the GTAP tools and those paths are recorded in the run row.

2. Have a GTAP expert review each hidden `reference_plan` before any model evaluation.
3. Run the `agent_tools` condition in fresh sessions. Successful cases produce solver artifacts under a case-specific gold directory.
4. Extract numerical truth from `.sol` and HAR outputs, not from the Agent's narrative.
5. Run the `no_tools` condition with the same user prompts and the system prompt in `prompts/no_tools_system.md`.
6. Repeat stochastic conditions using the repetition count in `config.json`.
7. Score setup correctness, execution, numerical accuracy, uncertainty calibration, result grounding, and appropriate clarification or rejection.

## Gold-standard rule

The tool-enabled Agent is not automatically the gold standard. Its proposed aggregation, closure, and shocks are first compared with the expert reference plan. Only a semantically correct, successfully solved, expert-approved run may be promoted to `gold/<case_id>/`. Solver warnings must be preserved and classified; unresolved set, coefficient, closure, or numerical warnings disqualify a candidate gold run even when the return code is zero.

The current pilot is intentionally small. It covers ordinary policy runs, minimal aggregation changes, closure-sensitive targets, multi-shock experiments, ambiguous baselines, and unsupported model changes. A publication benchmark should expand this into a balanced held-out suite using the generation strategy in `design.md`.
