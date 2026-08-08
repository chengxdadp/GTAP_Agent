# Gold outputs

Create one immutable directory per approved case:

```text
gold/<case_id>/
  reference_plan.json
  run_record.json
  scenario_context.json
  scenario_source.cmf
  run_summary.txt
  target_values.json
  artifacts/
```

Do not promote an Agent run to gold merely because it solved. The experiment must first match the hidden expert reference plan, and all solver warnings must be retained and adjudicated. `target_values.json` must be extracted directly from solver outputs and contain the exact source file, variable/header, dimensions, value, and unit for every target declared by the case.

Licensed or third-party GTAP data should not be copied here unless its redistribution terms have been checked. A replication package may instead record checksums and installation instructions.
