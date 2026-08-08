# Scripts Technical Reference

The `scripts/` directory is the executable workflow behind GTAP Agent. The scripts can be called directly from PowerShell or through the validated tools in `web/agent.py`.

The numeric filenames are stable stage identifiers, not a requirement to run every file in numeric order. Normal policy analysis reuses an existing model and baseline; aggregation, observed-data retrieval, and historical baseline construction are preparation operations.

## Workflow Shapes

### Routine scenario

```text
explicit model/aggregation + explicit baseline
  -> optional supported closure patch
  -> one structured shock specification
  -> one explicit CMF
  -> RunGTAP/GEMPACK solve
  -> automatic broad report in the Agent
  -> targeted result query only when needed
```

Entry points:

```text
modify_gtap_closure.py (optional)
  -> 06_apply_policy_shock_modifications.py
  -> 03_run_rungtap_scenario.py
  -> 07_read_gtap_results.py
```

For an unchanged standard closure, skip `modify_gtap_closure.py` and call script 06 without `--base-cmf`.

### Model/profile preparation

```text
00 verify runtime
  -> 01 build or change aggregation
  -> 04 prepare compatible observed-data mappings
  -> optionally 05 construct a historical baseline-update CMF
  -> 03 solve and explicitly register that compatible baseline
```

This flow runs only when model state is missing or intentionally changed. A custom aggregation changes dimensions, so dimension-bound data and registered baseline assets must be regenerated before reuse.

### Legacy smoke test

Script 02 is a one-off 2015 smoke test retained for compatibility. It is not an Agent scenario template and is not part of the normal or historical-baseline workflows.

## Agent Tool to Script Mapping

| Agent tool | Script/function | Important contract |
| --- | --- | --- |
| `aggregate_gtap_model` | Script 01 | Rebuild only when explicitly requested or missing |
| `aggregate_custom_gtap_model` | Script 01 | Move only specified original members; do not overwrite by default |
| `fetch_observed_data` | Script 04 | Use the mapping compatible with the selected model |
| `build_2024_baseline_update` | Script 05 | Optional bundled-profile preparation |
| `modify_gtap_closure` | `modify_gtap_closure.py` | Requires a non-empty approved patch |
| `modify_shock_cmf` | Script 06 | Requires an explicit baseline and typed GTAP variable shocks |
| `run_gtap_scenario` | Script 03, then script 07 default view | Exact CMF in; result directory and broad report out |
| `read_gtap_results` | Script 07 | Exact run directory and targeted filters |

All generated scenario artifacts preserve baseline, model, aggregation, closure, and shock context. Paths returned by one stage should be passed forward exactly rather than reconstructed from names or timestamps.

## 00 — Verify the Project-Local Runtime

Script: `00_verify_local_runtime.py`

Responsibilities:

- resolve project-local RunGTAP, GEMPACK, and GTAPAgg locations;
- check required executables, templates, model data, and aggregation inputs;
- return a grouped JSON report of available and missing files;
- avoid modifying the host `PATH` or external GTAP/GEMPACK installations.

```powershell
python scripts\00_verify_local_runtime.py
```

Default roots are `runtime/rungtap`, `runtime/gempack`, and `runtime/gtapagg`. See `runtime/README.md` for environment overrides.

## 01 — Build an Aggregated Model

Script: `01_aggregate_gtap10a_2014_to_10x10.py`

The bundled implementation uses GTAPAgg/FlexAgg to build RunGTAP model directories from the GTAP 10A source. It supports both the checked-in mapping and local customizations.

Default rebuild:

```powershell
python scripts\01_aggregate_gtap10a_2014_to_10x10.py
```

Local custom aggregation:

```powershell
$spec = '{"aggregation_name":"china_split","region_groups":[{"name":"China","description":"Mainland China","members":["chn"]}]}'

python scripts\01_aggregate_gtap10a_2014_to_10x10.py `
  --custom-spec-json $spec `
  --mapping-output config\aggregations\china_split.txt `
  --model-name gtap2015_china_split
```

Important arguments:

- `--mapping-file`: source GTAPAgg GUI mapping;
- `--model-name`: destination RunGTAP model directory;
- `--custom-spec-json`: regional and/or sectoral member moves;
- `--mapping-output`: reusable mapping under `config/aggregations`;
- `--overwrite`: explicit replacement of the exact custom model and mapping.

Rules:

- list only original GTAP members that must move;
- unlisted members keep their base-mapping assignments;
- use original codes or exact names such as `chn`, `usa`, and `osd`;
- reject duplicate assignment, unsafe names, invalid members, and accidental replacement;
- protect the bundled `gtap2015_10x10` model name.

Key outputs:

```text
runtime/rungtap/<model_name>/basedata.har
runtime/rungtap/<model_name>/sets.har
runtime/rungtap/<model_name>/default.prm
runtime/rungtap/<model_name>/aggregation_mapping.txt
runtime/rungtap/<model_name>/aggregation_metadata.json
result/01_aggregation/aggregation_summary.txt
```

## 02 — Legacy 2015 Smoke Test

Script: `02_prepare_2015_economy_shocks.py`

This script generates a coarse stylized scenario under `result/02_shocks`. It is useful only for teaching, legacy compatibility, or a quick execution-chain check:

```powershell
python scripts\02_prepare_2015_economy_shocks.py
```

Do not use it as the starting point for ordinary Agent scenarios, closure work, or registered-baseline analysis.

## 03 — Solve an Explicit CMF

Script: `03_run_rungtap_scenario.py`

Responsibilities:

- validate the requested CMF and model context;
- stage the project-local RunGTAP work directory;
- run SHOCKS, parameter preparation, GTAP, data-update, decomposition, and volume programs;
- collect CMFs, logs, `.sl4`/`.sol`, HAR outputs, and a run summary;
- reject missing required output or error flags;
- optionally register a successfully solved historical baseline.

Policy run:

```powershell
python scripts\03_run_rungtap_scenario.py `
  --cmf result\06_policy_modifications\cmf\policy_example.cmf `
  --result-dir result\03_run_policy_example
```

Arguments:

- `--cmf`: required scenario CMF;
- `--model-name`: optional override, normally inferred from embedded CMF context;
- `--result-dir`: required CLI collection directory;
- `--set-as-default-baseline`: preparation only; register a compatible solved historical update.

Common outputs include:

```text
GTAP.sl4
GTAP.sol
gdata.upd
newrate.har
newview.har
decomp.har
GTAPVol.har
run_summary.txt
```

The CLI script collects solve artifacts. The Agent's `run_gtap_scenario` handler additionally calls script 07's default view and returns the broad solve/result report in the same tool result. Consequently, the Agent does not call `read_gtap_results` again merely to describe a completed run.

Baseline registration copies solved model state to registered assets and writes `asset/default_baseline.json`. It is never used for a policy run and must be explicitly requested.

## 04 — Fetch and Standardize Observed Data

Script: `04_fetch_observed_calibration_data.py`

Supported sources:

- World Bank real GDP, `NY.GDP.MKTP.KD`;
- UN WPP population, with World Bank `SP.POP.TOTL` fallback;
- WITS applied tariffs, `AHS-WGHTD-AVRG`.

Routine cached run:

```powershell
python scripts\04_fetch_observed_calibration_data.py --skip-wits
```

For a custom aggregation, pass its exact mapping:

```powershell
python scripts\04_fetch_observed_calibration_data.py `
  --mapping-file config\aggregations\china_split.txt `
  --skip-wits
```

Important arguments include `--start-year`, `--end-year`, `--refresh`, `--skip-wits`, WITS rate-limit controls, `--un-token`, `--wpp-file`, and `--mapping-file`.

Raw responses are cached under `result/04_observed_data/raw`. Standardized outputs include the active GTAP region/country and sector maps, GDP, population, tariffs, and a fetch manifest.

## 05 — Optional Historical Baseline Update

Script: `05_build_2024_baseline_update.py`

This is a bundled-profile preparation recipe, not the definition of GTAP Agent. It constructs annual and cumulative macro targets and generates baseline-update CMFs from the source database year to a selected target year.

```powershell
python scripts\05_build_2024_baseline_update.py --write-all-cmfs
```

Important arguments:

- `--base-year`: source database year, bundled default 2014;
- `--target-year`: requested update year, normally latest common GDP/population year;
- `--model-name`: compatible RunGTAP model;
- `--include-tariffs`: include broad WITS updates;
- `--write-all-cmfs`: emit cumulative CMFs for all available years.

The recipe applies observed population and real-GDP targets and uses:

```text
Swap avareg(REG) = qgdp(REG);
```

`qgdp` becomes the target and `avareg` is inferred as an implicit regional productivity adjustment. This is a database-update assumption, not the normal policy closure. Factor endowments are not mechanically shocked using GDP as a proxy.

Outputs are written under `result/05_baseline_update`. Solving one of these CMFs does not register it unless script 03 is called with `--set-as-default-baseline`.

## Modify the GTAP Closure

Script: `modify_gtap_closure.py`

The script starts from a clean standard policy closure on an explicit baseline. It accepts only non-empty local patches:

- `gdp_target_tfp`: swap regional `avareg` with `qgdp`;
- `fixed_regional_investment`: swap regional `cgdslack` with `qcgds`.

Example:

```powershell
$closure = '{"scenario_name":"South Asia GDP target","baseline_id":"original_2014","modifications":[{"type":"gdp_target_tfp","region":"SouthAsia"}]}'
python scripts\modify_gtap_closure.py --spec-json $closure
```

The JSON response returns the exact `output_cmf`, manifest, resolved baseline, closure ID, patches, and swap lines. Pass that `output_cmf` as script 06's `--base-cmf`. An unchanged standard closure skips this script entirely.

Outputs are written under `result/05_closure_modifications/cmf`.

## 06 — Construct Structured Shocks

Script: `06_apply_policy_shock_modifications.py`

Required behavior:

- select `--baseline-id original_2014` or `registered_2024` explicitly;
- omit `--base-cmf` for the unchanged standard policy closure;
- otherwise pass only the exact compatible CMF returned by the closure or earlier shock stage;
- write a new output CMF rather than overwriting the source;
- resolve natural-language identifiers against the selected model's active mapping;
- preserve resolved shocks in JSON and CSV audit files.

The Agent-facing format uses `type=gtap_variable`, an explicit code, and only the dimensions defined for that code:

```powershell
$spec = '{"scenario_name":"China US soybean tariff 20","baseline_id":"registered_2024","modifications":[{"type":"gtap_variable","code":"tms","commodity":"soybeans","exporter":"United States","importer":"China","value":20,"value_mode":"target_rate"}]}'

python scripts\06_apply_policy_shock_modifications.py `
  --baseline-id registered_2024 `
  --spec-json $spec
```

The CLI retains a small set of semantic aliases such as `bilateral_import_tariff` for backward compatibility. The Agent tool schema deliberately exposes the explicit code-specific form so the model cannot fill unrelated dimensions.

### Variable catalog

| Code | Required dimensions | Meaning |
| --- | --- | --- |
| `tms` | commodity, exporter, importer | bilateral import-tax power |
| `tm` | commodity, importer | source-generic import-tax power |
| `txs` | commodity, exporter, importer | bilateral export-tax/subsidy power |
| `tx` | commodity, exporter | destination-generic export-tax/subsidy power |
| `to` | commodity, region | output/income-tax power |
| `tp` | region | uniform private-consumption-tax shift |
| `pop` | region | population |
| `qo` | factor, region | factor-endowment supply |
| `aosec` | sector | one sector's output technology worldwide |
| `aoreg` | region | region-wide output technology |
| `aoall` | sector, region | sector-region output technology |
| `avasec` | sector | one sector's value-added technology worldwide |
| `avareg` | region | region-wide value-added technology |
| `afcom` | commodity | commodity-specific intermediate-input technology worldwide |
| `afsec` | sector | sector-wide intermediate-input technology |
| `afreg` | region | regional intermediate-input technology |
| `afall` | commodity, sector, region | input-sector-region intermediate technology |
| `afecom` | factor | factor-specific primary-factor technology worldwide |
| `afesec` | sector | sector-wide primary-factor technology |
| `afereg` | region | regional primary-factor technology |
| `afeall` | factor, sector, region | factor-sector-region primary-factor technology |
| `ams` | commodity, exporter, importer | bilateral import-augmenting technology |
| `atf` | commodity | commodity-specific international-shipping technology |
| `ats` | exporter | origin-specific international-shipping technology |
| `atd` | importer | destination-specific international-shipping technology |
| `qgdp` | region | real-GDP target; requires `gdp_target_tfp` closure patch |
| `qcgds` | region | real-investment target; requires `fixed_regional_investment` closure patch |

Positive technology values denote improvements. International shipping uses only `atf`, `ats`, or `atd`; `af*` variables are intermediate-input technologies.

For `tms`, supported value modes are:

- `target_rate`: set the target ad valorem tariff after converting baseline `RTMS` to tax-power change;
- `rate_change`: change the ad valorem rate by percentage points;
- `power_change`: apply a direct tax-power percentage shock.

Other codes use `percent_change`.

Outputs:

```text
result/06_policy_modifications/cmf/<scenario>.cmf
result/06_policy_modifications/cmf/<scenario>.resolved.json
result/06_policy_modifications/cmf/<scenario>.resolved.csv
result/06_policy_modifications/policy_modification_summary.txt
```

## 07 — Read GTAP Results

Script: `07_read_gtap_results.py`

The result reader converts `.sol`, HAR, log, CMF, and catalog files into structured JSON. The CLI can default to the newest `result/03_run*` directory; the Agent passes the exact `result_dir` returned by the solve tool and never guesses the current run.

Broad CLI summary:

```powershell
python scripts\07_read_gtap_results.py --result-dir result\03_run_policy_example
```

Targeted solution query:

```powershell
python scripts\07_read_gtap_results.py `
  --result-dir result\03_run_policy_example `
  --view solution `
  --variable qxs `
  --exporter NAmerica `
  --importer EastAsia `
  --sector GrainsCrops
```

Raw dimension query:

```powershell
python scripts\07_read_gtap_results.py `
  --result-dir result\03_run_policy_example `
  --view updated_data `
  --header SMRY `
  --dimension REG=SouthAsia
```

| View | Primary source |
| --- | --- |
| `default` | solve status, context, shocks, warnings, catalog, and default indicators |
| `solution` | `GTAP.sol` variables and dimensions |
| `volume` | `GTAPVol.har` |
| `updated_data` | solved updated-data and view HAR files |
| `base_data` | exact scenario baseline recorded in CMF context |
| `tax_rates` | baseline/updated rate HAR data |
| `welfare` | equivalent variation and aggregate welfare results |
| `welfare_decomposition` | `decomp.har` components |
| `log` | solver and auxiliary-program logs |
| `cmf` | staged/generated CMFs and embedded context |
| `files` | result artifact inventory |

Filters include repeatable `--variable` and `--header`, semantic region/sector/exporter/importer selectors, `--contains`, repeatable raw `--dimension NAME=VALUE`, row limits, sorting, and optional baseline excerpts.

The tool reads one run at a time and has no comparison mode. To compare scenarios, query each exact run independently and compare the returned structured values in the calling layer.

The latest query is written to `result/07_result_reads/latest_result_read.json` in addition to stdout JSON.

## Shared Modules

### `gtap_runtime.py`

Resolves project-local RunGTAP, GEMPACK, GTAPAgg, executable, model, and asset paths, including explicit environment overrides.

### `gtap_aggregation.py`

Parses GTAPAgg mappings, applies local member moves, validates names and assignments, writes metadata, and protects existing models from accidental replacement.

### `gtap_observed_data.py`

Implements API/file caching, World Bank/UN WPP/WITS parsing, standardized tables, and conversion from countries or source sectors to active aggregates.

### `gtap_scenario.py`

Resolves explicit baselines and model-bound mappings, constructs standard scenario CMFs, embeds machine-readable context, and validates closure patches and aggregate selectors.

## Validation

```powershell
python scripts\00_verify_local_runtime.py
python -m unittest discover -s tests -v
Get-ChildItem scripts\*.py | ForEach-Object { python -m py_compile $_.FullName }
```

Automated tests cover aggregation safety, explicit baseline/model binding, closure-sensitive shocks, expanded variable dimensions, legacy coefficient-check removal, OpenRouter message preservation, Agent prompt/tool contracts, session reset, and Markdown/UI regressions.

## Known Limits

- Full WITS retrieval is slow and current tariff calibration is still a broad all-products average.
- UN WPP may require a token; population falls back to World Bank data when necessary.
- A new aggregation requires compatible mappings, calibration data, and any dimension-bound registered baseline.
- The bundled historical update is not a recursive-dynamic projection or formal historical decomposition.
- Formal research requires expert review of aggregation, baseline construction, closure, shock definition, tariff conversion, convergence, and interpretation.
