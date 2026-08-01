# Project-local GTAP runtime

This directory contains the Windows runtime used by the numbered Python scripts.

- `rungtap\`: RunGTAP command-line programs, the `gtap2015_10x10` model, and `model_templates\` files restored after re-aggregation.
- `gempack\`: HAR conversion utilities used by policy generation and result reading.
- `gtapagg\`: GTAPAgg2, the public GTAP 10 license, the mapping project, and 2014 inputs.

Run `python scripts\00_verify_local_runtime.py` from the project root to verify required files.
The scripts use these project-local paths by default. Environment variables remain available as explicit overrides.

Custom aggregations are written to separate `rungtap\gtap2015_*` model directories. Dimension-specific optional `ALTPAR`/`PEELAS` preprocessing is used only when a matching model template exists; the core GTAP solve does not require those optional template files.

Do not publish private `licen.gem` or private GTAPAgg license files. The bundled RunGTAP runtime uses its own distributed small-model runtime files, and GTAPAgg uses the public GTAP 10 archive license.
