# Scripts Technical Reference

The `scripts\` directory contains the reusable Python workflow behind GTAP Agent. Files `00` through `07` preserve the main execution order. Runtime discovery, aggregation, observed-data retrieval, and mapping logic are factored into shared modules.

All scripts use project-local `runtime\`, `asset\`, `config\`, and `result\` paths by default. Countries, regions, sectors, commodities, aggregation descriptions, and structured policy specifications use canonical English names or GTAP/ISO codes. The LLM normalizes natural-language requests before invoking these scripts.

## Execution Order

```text
00 verify runtime
  -> 01 aggregate model, when the model is missing or aggregation changes
  -> 04 fetch and standardize observed data, when data is missing or refreshed
  -> 05 build the 2014-to-2024 baseline update
  -> 03 solve and register the default 2024 baseline
  -> 06 build a 2024 policy CMF
  -> 03 solve the policy
  -> 07 read and summarize results
```

Script `02` is retained as a legacy 2015 smoke-test scenario and is not part of the standard policy workflow.

## 00 — Verify the Project-Local Runtime

Script: `00_verify_local_runtime.py`

Responsibilities:

- Resolve project-local RunGTAP, GEMPACK, and GTAPAgg paths.
- Check RunGTAP programs, the default aggregated model, model templates, GEMPACK conversion tools, and GTAPAgg data.
- Return a JSON report with group status and missing files.

Run:

```powershell
python scripts\00_verify_local_runtime.py
```

Default paths:

```text
runtime\rungtap
runtime\gempack
runtime\gtapagg
```

The script does not modify the system `PATH` and does not delete or replace a GEMPACK or GTAP installation elsewhere on the computer.

## 01 — Aggregate the Model

Script: `01_aggregate_gtap10a_2014_to_10x10.py`

Responsibilities:

- Discover the project-local GTAPAgg project and its `default.txt` mapping.
- Convert the GTAPAgg GUI mapping to FlexAgg input.
- Call `runagg.bat` to aggregate GTAP 10A 2014 data.
- Rebuild `runtime\rungtap\gtap2015_10x10` by default.
- Create independent custom mappings and models from structured JSON.
- Save aggregation logs, mapping copies, metadata, and summaries.

Default aggregation:

```powershell
python scripts\01_aggregate_gtap10a_2014_to_10x10.py
```

Custom aggregation:

```powershell
$spec = '{"aggregation_name":"china_split","region_groups":[{"name":"China","description":"Mainland China","members":["chn"]}]}'

python scripts\01_aggregate_gtap10a_2014_to_10x10.py `
  --custom-spec-json $spec `
  --mapping-output config\aggregations\china_split.txt `
  --model-name gtap2015_china_split
```

Arguments:

- `--mapping-file`: Base GTAPAgg GUI mapping; defaults to `default.txt`.
- `--model-name`: Output RunGTAP model directory.
- `--custom-spec-json`: JSON containing `region_groups` and/or `sector_groups`.
- `--mapping-output`: Custom mapping destination under `config\aggregations`.
- `--overwrite`: Explicitly allow replacement of a custom mapping and model with the same name.

Custom aggregation rules:

- Only original GTAP members listed in the JSON are moved.
- Unlisted members retain their base-mapping assignments.
- Prefer original GTAP codes or exact English names, such as `chn`, `usa`, and `osd`.
- A member cannot be assigned to multiple new groups.
- The default model name `gtap2015_10x10` is protected.
- Aggregate names, model names, and output paths are validated.

Key outputs:

```text
runtime\rungtap\<model_name>\basedata.har
runtime\rungtap\<model_name>\sets.har
runtime\rungtap\<model_name>\default.prm
runtime\rungtap\<model_name>\aggregation_mapping.txt
runtime\rungtap\<model_name>\aggregation_metadata.json
result\01_aggregation\aggregation_summary.txt
```

## 02 — Legacy 2015 Smoke Test

Script: `02_prepare_2015_economy_shocks.py`

Responsibilities:

- Generate a coarse, stylized 2015 scenario.
- Support teaching demonstrations, legacy compatibility, and quick RunGTAP call-chain checks.
- Remain separate from the default 2024 baseline and standard policy workflow.

Run:

```powershell
python scripts\02_prepare_2015_economy_shocks.py
```

Outputs:

```text
result\02_shocks\scenario_2015_shocks.csv
result\02_shocks\scenario_2015_gtap.cmf
```

## 03 — Run RunGTAP/GEMPACK

Script: `03_run_rungtap_scenario.py`

Responsibilities:

- Run a specified CMF through the project-local RunGTAP/GEMPACK runtime.
- Select a model directory and result directory.
- Collect solve files, logs, database views, and summaries under `result\`.
- Optionally register the default 2024 baseline after a successful baseline solve.

Run the baseline update:

```powershell
python scripts\03_run_rungtap_scenario.py `
  --cmf result\05_baseline_update\cmf\baseline_update_2014_to_2024.cmf `
  --result-dir result\03_run_baseline_2024 `
  --set-as-default-baseline
```

Arguments:

- `--cmf`: CMF path; defaults to the 2024 baseline-update CMF.
- `--model-name`: RunGTAP model directory; defaults to `gtap2015_10x10`.
- `--result-dir`: Collected result directory.
- `--set-as-default-baseline`: Reserved for a successfully solved 2014-to-2024 baseline update.

Default baseline registration writes:

```text
asset\basedata_2024.har
asset\baserate_2024.har
asset\default_baseline.json
```

Common collected files:

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

## 04 — Fetch Observed Calibration Data

Script: `04_fetch_observed_calibration_data.py`

Data sources:

- GDP: World Bank `NY.GDP.MKTP.KD`
- Population: UN WPP API or local file, with World Bank `SP.POP.TOTL` as fallback
- Tariffs: WITS `AHS-WGHTD-AVRG`

Routine run:

```powershell
python scripts\04_fetch_observed_calibration_data.py --skip-wits
```

Full or incremental WITS retrieval:

```powershell
python scripts\04_fetch_observed_calibration_data.py
```

Custom aggregation:

```powershell
python scripts\04_fetch_observed_calibration_data.py `
  --mapping-file config\aggregations\china_split.txt `
  --skip-wits
```

Arguments:

- `--start-year`: First year; defaults to 2014.
- `--end-year`: Last year; defaults to the latest available GDP year.
- `--refresh`: Ignore the raw-data cache.
- `--skip-wits`: Skip WITS retrieval.
- `--max-wits-calls`: Limit WITS calls for testing.
- `--wits-sleep`: Delay in seconds between WITS calls.
- `--un-token`: UN Data Portal bearer token.
- `--wpp-file`: Local WPP CSV or XLSX file.
- `--mapping-file`: GTAPAgg mapping used to build standardized region and sector tables.

Outputs:

```text
result\04_observed_data\raw\
result\04_observed_data\standardized\gtap_region_country_map.csv
result\04_observed_data\standardized\gtap_sector_map.csv
result\04_observed_data\standardized\world_bank_gdp_constant.csv
result\04_observed_data\standardized\population.csv
result\04_observed_data\standardized\wits_tariff_all_products.csv
result\04_observed_data\fetch_manifest.json
```

Raw API responses are cached by URL or reporter-year. Standardized outputs are regenerated on every run.

## 05 — Build the 2024 Baseline Update

Script: `05_build_2024_baseline_update.py`

Responsibilities:

- Build annual and cumulative baseline targets from standardized GDP, population, and optional tariff data.
- Generate pre-policy baseline-update CMFs for the latest common year, currently usually 2024.
- Optionally write cumulative CMFs for every available target year.
- Record the selected `model_name` for default or custom models.

Run:

```powershell
python scripts\05_build_2024_baseline_update.py --write-all-cmfs
```

Arguments:

- `--base-year`: Original GTAP database year; defaults to 2014.
- `--target-year`: Updated baseline year; defaults to the latest common GDP/population year.
- `--model-name`: Target RunGTAP model.
- `--include-tariffs`: Include broad WITS tariff updates.
- `--write-all-cmfs`: Write a cumulative CMF for each target year.

Closure change:

```text
Swap avareg(REG) = qgdp(REG);
```

- `pop(REG)`: Observed population change.
- `qgdp(REG)`: Observed real GDP target.
- `avareg(REG)`: Endogenously inferred implicit regional TFP after the swap.
- `qo(ENDW_COMM,REG)`: Remains in the standard closure and is no longer shocked using GDP as a proxy.

Because cumulative 2014-to-2024 shocks are large, generated CMFs use conservative stepping:

```text
Steps = 4 8 12;
subintervals = 10;
```

Outputs:

```text
result\05_baseline_update\region_macro_panel.csv
result\05_baseline_update\annual_baseline_targets.csv
result\05_baseline_update\cumulative_baseline_targets.csv
result\05_baseline_update\cmf\baseline_update_2014_to_YYYY.cmf
result\05_baseline_update\baseline_update_summary.txt
```

## 06 — Apply Policy Modifications

Script: `06_apply_policy_shock_modifications.py`

### Default 2024 Mode

- Defaults to `--base-year 2024`.
- Requires `asset\basedata_2024.har`, `asset\baserate_2024.har`, and corresponding metadata.
- Creates a clean standard-policy-closure CMF from the default 2024 baseline.
- Keeps `qgdp` endogenous.
- Does not include the baseline-stage `avareg=qgdp` swap.
- Does not repeat baseline macro shocks.
- Reads 2024 `RTMS` values when converting tariff rates to `tms` tax-power changes.

If the default 2024 baseline is missing, the script returns an English error explaining the required preparation. It does not run the baseline workflow automatically.

### 2014 Compatibility Mode

Only an explicit `--base-year 2014` copies and extends an existing baseline-update CMF. This is a legacy escape hatch, not the standard policy workflow.

### Supported Modifications

| `type` | GTAP statement |
| --- | --- |
| `bilateral_import_tariff` | `tms(commodity, exporter, importer)` |
| `regional_population` | `pop(REG)` |
| `regional_endowment` | `qo(ENDW_COMM,REG)` |
| `regional_productivity` | `aoall(PROD_COMM,REG)` |

Input identifiers use English names or codes. The backend validates them against the active `gtap_region_country_map.csv`, `gtap_sector_map.csv`, and canonical English aliases.

Example:

```powershell
$spec = '{"scenario_name":"China US soybean tariff 20","modifications":[{"type":"bilateral_import_tariff","importer":"China","exporter":"United States","commodity":"soybeans","tariff_percent":20,"rate_mode":"target_rate"}]}'
python scripts\06_apply_policy_shock_modifications.py --spec-json $spec
```

Rate modes:

- `target_rate`: Set the target ad valorem tariff rate.
- `rate_change`: Increase or decrease the baseline ad valorem rate by percentage points.
- `power_change`: Apply a direct GTAP tax-power percentage shock.

Outputs:

```text
result\06_policy_modifications\cmf\policy_2024__*.cmf
result\06_policy_modifications\cmf\*.resolved.json
result\06_policy_modifications\cmf\*.resolved.csv
result\06_policy_modifications\policy_modification_summary.txt
```

## 07 — Read GTAP Results

Script: `07_read_gtap_results.py`

Responsibilities:

- Locate the newest `result\03_run*` directory or read an explicit directory.
- Summarize flags, missing files, log errors, warnings, and solve accuracy.
- Read pre-run CMF shocks and resolved policy specifications.
- Use project-local `har2txt` and `har2csv` to convert `.sol`, HAR, and view files.
- Return GDP, `avareg`, EV, trade balance, terms of trade, sector output, and bilateral trade.
- Filter by variable, header, region, sector, exporter, and importer.

Default summary:

```powershell
python scripts\07_read_gtap_results.py
```

Targeted query:

```powershell
python scripts\07_read_gtap_results.py `
  --result-dir result\03_run_policy_soybean `
  --view solution `
  --variable qxs `
  --exporter NAMerica `
  --importer EastAsia `
  --sector GrainsCrops
```

Views:

- `default`
- `solution`
- `volume`
- `updated_data`
- `base_data`
- `compare_data`
- `welfare`
- `log`
- `cmf`
- `files`

Output:

```text
stdout JSON
result\07_result_reads\latest_result_read.json
```

## Shared Modules

### `gtap_runtime.py`

- Resolves project-local RunGTAP, GEMPACK, GTAPAgg, and asset paths.
- Applies explicit environment-variable overrides.
- Provides consistent portable-path behavior to numbered scripts.

### `gtap_aggregation.py`

- Parses GTAPAgg mapping sections.
- Builds regional and sectoral overrides from the default mapping.
- Validates members, names, duplicate assignments, and output metadata.
- Prevents a custom aggregation from accidentally overwriting the default model.

### `gtap_observed_data.py`

- URL and API caching.
- CSV input/output.
- World Bank, UN WPP, and WITS request parsing.
- GTAPAgg mapping conversion to standardized regional and sectoral tables.
- Country-to-active-aggregate conversion.

## Tests

```powershell
python -m unittest discover -s tests -v
```

Current automated coverage includes:

- Splitting `chn` from `EastAsia`.
- Splitting `osd` from `GrainsCrops`.
- Rejecting duplicate member assignments.
- Rejecting unsafe aggregate names.

## Known Limitations

- Full WITS retrieval is slow, and current tariff calibration remains an all-products broad average.
- The UN WPP API may require a token. Without a token or local file, population data falls back to the World Bank.
- A custom aggregation requires regeneration of the standardized mappings and 2024 baseline.
- The default 2024 baseline is a database-update stage for policy analysis, not a full recursive-dynamic projection or formal historical decomposition.
- Formal research requires expert review of the closure, shock definition, tariff conversion, and aggregation disclosure.
