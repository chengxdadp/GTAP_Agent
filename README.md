# GTAP Agent Automation Workspace

GTAP Agent organizes the GTAP 10A 2014 database, GTAPAgg aggregation, observed-data calibration, the 2024 baseline update, policy scenarios, RunGTAP/GEMPACK solving, and structured result reading into a reusable and auditable local workflow.

The recommended workflow is to solve the original 2014 database forward to a 2024 pre-policy baseline and register it as the project's **default 2024 baseline**. Routine policy analysis then starts from that 2024 baseline and uses a standard policy closure.

## Major Upgrades

- **Project-local portable runtime**: RunGTAP, GEMPACK conversion tools, GTAPAgg, and the archived GTAP 10A data are stored under the project directory. Default execution no longer depends on hard-coded paths such as `C:\GP`.
- **Consistent path resolution**: Numbered scripts prefer project-relative paths under `runtime\`, `asset\`, `config\`, and `result\`. Environment variables remain available as explicit overrides.
- **Clear baseline terminology**: The earlier freeze/baseline terminology is now consistently expressed as the **default 2024 baseline**. The original database year is 2014; the default policy-analysis baseline year is 2024.
- **Separate baseline and policy closures**: The baseline update uses `Swap avareg(REG) = qgdp(REG)` to infer implicit TFP. Policy scenarios restore the standard policy closure so that `qgdp` responds endogenously to policy.
- **Agent-controlled aggregation**: The default 10-by-10 aggregation remains available, while a separate tool can create custom regional and sectoral aggregations without overwriting the default model.
- **Structured policy modifications**: Bilateral import tariffs, regional population, factor endowment, and productivity shocks are supported, with auditable resolved JSON and CSV outputs.
- **Targeted result reading**: The Agent can inspect solve status, GDP, welfare, trade, sector output, HAR tables, and `.sol` variables instead of relying only on console logs.
- **English demonstration interface and prompts**: The web UI, quick scenarios, Agent system prompt, tool descriptions, dynamic aggregation context, user-visible errors, and default Agent responses are in English.
- **Separated backend responsibilities**: `web\server.py` handles only HTTP, static files, and streaming transport. Agent orchestration, tool schemas, sessions, and model requests are contained in `web\agent.py`.

## Language and Mapping Conventions

- The web interface, Agent prompts, tool descriptions, documentation, and default Agent responses use English.
- Country, region, sector, and commodity mappings use canonical English names, ISO/GTAP codes, and active aggregate names.
- If a user writes in another language, the LLM normalizes the request to canonical English identifiers before calling a tool. The backend does not depend on language-specific alias tables.

Typical mappings under the default aggregation include:

```text
China -> chn / CHN -> EastAsia
United States -> usa / USA -> NAMerica
soybeans -> osd -> GrainsCrops
```

After a custom aggregation, the new mapping is authoritative. The Agent must not continue assuming that `EastAsia`, `NAMerica`, or `GrainsCrops` still exists.

## Project-Local Runtime

Default locations:

| Component | Project-local path |
| --- | --- |
| RunGTAP and models | `runtime\rungtap` |
| GEMPACK conversion tools | `runtime\gempack` |
| GTAPAgg | `runtime\gtapagg` |
| GTAP 10A database archive | `asset\GTAP10A_GTAP-APT_2014.PKG` |
| Default aggregated model | `runtime\rungtap\gtap2015_10x10` |
| Default 2024 database | `asset\basedata_2024.har` |
| Default 2024 tariff rates | `asset\baserate_2024.har` |
| Default baseline metadata | `asset\default_baseline.json` |

Verify the bundled runtime:

```powershell
python scripts\00_verify_local_runtime.py
```

A successful check reports `ok: true` for these five groups:

- RunGTAP programs
- Aggregated model
- Default model templates
- GEMPACK conversion tools
- GTAPAgg

The following environment variables are optional, explicit overrides:

- `GTAP_RUNGTAP_DIR` or `RUNGTAP_DIR`
- `GEMPACK_DIR`
- `GTAPAGG_DIR`
- `GTAPAGG_PROJECT_DIR`
- `GTAPAGG_MAPPING`
- `GTAPAGG_INPUT_DIR`
- `GTAP_PKG_FILE`

When no override is set, scripts resolve the runtime entirely from this project. They do not modify the system `PATH` or require changes to an existing `C:\GP` installation.

## Project Structure

```text
GTAPAgent\
  asset\                         # Source archive, default 2024 baseline, metadata
  config\aggregations\          # Custom GTAPAgg mappings
  runtime\
    rungtap\                     # RunGTAP, local models, solve workspace
    gempack\                     # har2txt, har2csv, and related tools
    gtapagg\                     # GTAPAgg and GTAP10A aggregation project
  scripts\
    00_verify_local_runtime.py
    01_aggregate_gtap10a_2014_to_10x10.py
    02_prepare_2015_economy_shocks.py
    03_run_rungtap_scenario.py
    04_fetch_observed_calibration_data.py
    05_build_2024_baseline_update.py
    06_apply_policy_shock_modifications.py
    07_read_gtap_results.py
    gtap_runtime.py              # Runtime and path resolution
    gtap_aggregation.py          # Custom aggregation construction and validation
    gtap_observed_data.py        # Data retrieval, caching, and mapping utilities
  result\                        # Logs, CMFs, solve results, and summaries
  tests\                         # Aggregation unit tests
  web\
    server.py                    # Lightweight HTTP and NDJSON server
    agent.py                     # Agent loop, tools, and model client
    index.html
    app.js
    styles.css
```

Numbered filenames preserve the main execution order. Shared path, aggregation, and data logic is placed in reusable modules without changing the established workflow.

## Standard Workflow

### 1. Verify the Runtime

```powershell
python scripts\00_verify_local_runtime.py
```

### 2. Prepare the Default Aggregated Model

Run this only when the default model is missing or must be rebuilt:

```powershell
python scripts\01_aggregate_gtap10a_2014_to_10x10.py
```

### 3. Fetch and Standardize Observed Data

For routine preparation, the full WITS tariff download can be skipped:

```powershell
python scripts\04_fetch_observed_calibration_data.py --skip-wits
```

### 4. Build and Register the Default 2024 Baseline

First generate the 2014-to-2024 baseline-update CMF:

```powershell
python scripts\05_build_2024_baseline_update.py --write-all-cmfs
```

Then solve and register it as the project default:

```powershell
python scripts\03_run_rungtap_scenario.py `
  --cmf result\05_baseline_update\cmf\baseline_update_2014_to_2024.cmf `
  --result-dir result\03_run_baseline_2024 `
  --set-as-default-baseline
```

Key outputs:

```text
asset\basedata_2024.har
asset\baserate_2024.har
asset\default_baseline.json
```

### 5. Build a Policy Scenario

This example sets China's import tariff on soybeans from the United States to 20 percent on the 2024 baseline:

```powershell
$spec = '{"scenario_name":"China US soybean tariff 20","modifications":[{"type":"bilateral_import_tariff","importer":"China","exporter":"United States","commodity":"soybeans","tariff_percent":20,"rate_mode":"target_rate"}]}'
python scripts\06_apply_policy_shock_modifications.py --spec-json $spec
```

Select the newest policy CMF and run it:

```powershell
$policyCmf = Get-ChildItem result\06_policy_modifications\cmf\policy_2024__*.cmf |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1 -ExpandProperty FullName

python scripts\03_run_rungtap_scenario.py `
  --cmf $policyCmf `
  --result-dir result\03_run_policy_soybean
```

### 6. Read the Results

```powershell
python scripts\07_read_gtap_results.py --result-dir result\03_run_policy_soybean
```

Targeted query example:

```powershell
python scripts\07_read_gtap_results.py `
  --result-dir result\03_run_policy_soybean `
  --view solution `
  --variable qxs `
  --exporter NAMerica `
  --importer EastAsia `
  --sector GrainsCrops
```

## Default Baseline and Policy Closure

The baseline update and policy simulation are separate stages.

### 2014-to-2024 Baseline Update

- Uses the standard GTAP multiregion closure.
- Adds `Swap avareg(REG) = qgdp(REG);`.
- Shocks `qgdp(REG)` using observed real GDP growth.
- Solves `avareg(REG)` endogenously as an implicit regional TFP adjustment.
- With `--set-as-default-baseline`, registers the successfully solved database and tariff rates as the project default 2024 baseline.

### 2024 Policy Simulation

- Uses `base_year=2024` by default.
- Starts from `asset\basedata_2024.har` and `asset\baserate_2024.har`.
- Uses the standard policy closure with endogenous `qgdp`.
- Does not retain the `avareg=qgdp` swap.
- Does not repeat baseline macro shocks.
- Applies only the policy modifications requested by the user.

If the default 2024 baseline is missing, the Agent does not silently run the full baseline workflow. It reports the missing baseline and asks the user for confirmation first.

`--base-year 2014` is an explicit compatibility path. It appends policy statements to the 2014-to-2024 baseline-update CMF and is not the standard policy workflow.

## Custom Regional and Sectoral Aggregation

The default `aggregate_gtap_model` tool takes no arguments and always rebuilds the default 10-by-10 model. Use `aggregate_custom_gtap_model`, or script 01 with structured JSON, when the aggregation must change.

Example: split China from the default `EastAsia` aggregate:

```json
{
  "aggregation_name": "china_split",
  "region_groups": [
    {
      "name": "China",
      "description": "Mainland China",
      "members": ["chn"]
    }
  ]
}
```

CLI example:

```powershell
$aggregation = '{"aggregation_name":"china_split","region_groups":[{"name":"China","description":"Mainland China","members":["chn"]}]}'
python scripts\01_aggregate_gtap10a_2014_to_10x10.py `
  --custom-spec-json $aggregation `
  --mapping-output config\aggregations\china_split.txt `
  --model-name gtap2015_china_split
```

Rules:

- `region_groups` and `sector_groups` list only original GTAP members that should move.
- Unlisted members retain their default assignments.
- Prefer original GTAP codes or exact English names, such as `chn`, `usa`, and `osd`.
- Aggregate names are limited to 12 characters and descriptions to 30 characters.
- The default `gtap2015_10x10` model is protected; custom aggregations use independent model directories.
- Existing mappings and models are not replaced unless `--overwrite` is explicit.

Key outputs:

```text
config\aggregations\china_split.txt
runtime\rungtap\gtap2015_china_split\aggregation_mapping.txt
runtime\rungtap\gtap2015_china_split\aggregation_metadata.json
```

Changing the aggregation changes the model dimensions. The old 2024 baseline is therefore incompatible with the new model. Preparation must be repeated in this order:

1. Run `04_fetch_observed_calibration_data.py --mapping-file ...` with the new mapping.
2. Build a baseline-update CMF using the new `model_name`.
3. Solve the baseline using the same `model_name`.
4. Register it as the new default 2024 baseline only after user confirmation.

## Agent Tools

| Tool | Purpose | Routine use |
| --- | --- | --- |
| `aggregate_gtap_model` | Rebuild the default 10-by-10 model | No; one-time setup |
| `aggregate_custom_gtap_model` | Build an independent custom mapping and model | When the user explicitly changes aggregation |
| `fetch_observed_data` | Fetch and standardize GDP, population, and tariffs | No; setup or data refresh |
| `build_2024_baseline_update` | Generate a pre-policy baseline-update CMF | Baseline preparation |
| `modify_shock_cmf` | Generate a structured policy CMF | Yes |
| `run_gtap_scenario` | Solve through RunGTAP/GEMPACK | When the user requests a run |
| `read_gtap_results` | Read and interpret structured results | When the user requests interpretation |

The Agent does not expose an arbitrary shell tool. Every tool maps to a registered project script, and user-provided paths are restricted to the project workspace.

## Web Agent

The API key is loaded in this order:

1. `DEEPSEEK_API_KEY` environment variable
2. Local `scripts\key.txt` file

Start the server:

```powershell
python web\server.py --host 127.0.0.1 --port 8765
```

Open:

```text
http://127.0.0.1:8765/
```

The web interface, quick scenarios, and Agent responses are in English. The `Execution Trace` displays tool names, arguments, status, exit codes, duration, log tails, and summary files.

Opening `web\index.html` directly provides only a static preview. Model API calls and local Python tools require the local server.

## Data Sources

- GDP: World Bank API, `NY.GDP.MKTP.KD`
- Population: UN WPP API or local file, with World Bank `SP.POP.TOTL` as fallback
- Tariffs: WITS SDMX API, `AHS-WGHTD-AVRG`

A full WITS download proceeds incrementally by reporter-year and can be slow. Routine demonstrations may use `--skip-wits` and cached data. Run the full download when tariff calibration must be refreshed.

## Result Directories

| Directory | Contents |
| --- | --- |
| `result\01_aggregation` | Aggregation logs, summary, and wholejob output |
| `result\02_shocks` | Legacy 2015 smoke-test scenario |
| `result\03_run*` | Collected RunGTAP/GEMPACK solve outputs |
| `result\04_observed_data` | Raw cache, standardized data, and manifest |
| `result\05_baseline_update` | Baseline targets, CMFs, and summary |
| `result\06_policy_modifications` | Policy CMFs, resolved JSON/CSV, and summary |
| `result\07_result_reads` | Latest structured result read |

## Testing and Validation

Run unit tests:

```powershell
python -m unittest discover -s tests -v
```

Check Python syntax:

```powershell
python -m py_compile web\agent.py web\server.py
Get-ChildItem scripts\*.py | ForEach-Object { python -m py_compile $_.FullName }
```

Current automated tests cover custom aggregation: splitting a region, splitting a sector, rejecting duplicate member assignments, and rejecting unsafe names. Run `00_verify_local_runtime.py` before any production scenario.

## Known Limitations

- The 2024 baseline update is an approximate historical calibration, not a full recursive-dynamic projection or formal historical decomposition.
- Current WITS tariff calibration is an all-products weighted average; a complete product-classification-to-GTAP-sector mapping is not yet implemented.
- The UN WPP API may require a token. Population data falls back to the World Bank when neither a token nor a local WPP file is available.
- Country and commodity detail is limited by the active aggregation. The Agent must disclose the actual aggregation used in its interpretation.
- Formal policy research still requires expert review of the closure, shock definition, tariff conversion, data sources, and interpretation.
