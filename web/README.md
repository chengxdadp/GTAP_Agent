# GTAP Agent Web App

The `web\` directory provides the local browser interface for GTAP Agent. It does not replace the numbered scripts. Instead, it registers those scripts as a limited set of traceable function tools and streams Agent responses, tool arguments, execution status, and result summaries to the browser.

The interface and all Agent-facing text are in English.

## Architecture

```text
Browser
  -> server.py                 HTTP, static files, JSON/NDJSON
     -> agent.py               System prompt, sessions, LLM, tool loop
        -> scripts\*.py        Controlled GTAP workflow entry points
           -> runtime\         RunGTAP, GEMPACK, GTAPAgg
           -> result\          Logs, CMFs, HAR, SOL, and summaries
```

File responsibilities:

| File | Responsibility |
| --- | --- |
| `index.html` | English page structure, quick scenarios, and input form |
| `app.js` | Streaming events, Markdown rendering, status, and Execution Trace |
| `styles.css` | Two-pane layout and responsive styling |
| `server.py` | Lightweight HTTP service, static files, and `/api/*` routes |
| `agent.py` | English system prompt, tool schemas, sessions, OpenRouter requests, and tool execution |

`server.py` remains intentionally small. Agent orchestration and GTAP workflow logic do not live in the HTTP handler.

## Starting the App

The OpenRouter API key is loaded in this order:

1. `OPENROUTER_API_KEY` environment variable
2. The second line of `scripts\key.txt`

The default endpoint is `https://openrouter.ai/api/v1` and the default model is
`openai/gpt-5.6-luna`. Override them with `OPENROUTER_BASE_URL` and
`OPENROUTER_MODEL` when needed.

From the project root:

```powershell
python web\server.py --host 127.0.0.1 --port 8765
```

Open:

```text
http://127.0.0.1:8765/
```

Opening `index.html` directly provides a static preview only. Static mode cannot call the model API, Agent tools, or local Python scripts.

## English Interface

The current page includes:

- The `GTAP Agent Console` chat pane
- English quick scenarios:
  - `Set 2024 Default Baseline`
  - `Soybean Tariff`
  - `U.S. 10% Global Tariff`
  - `Interpret Latest Results`
- English placeholder text, status labels, errors, and welcome message
- The `Execution Trace` tool pane

The `app.js` URL includes a version marker so that a normal page refresh does not retain the old localized script from browser cache.

## Language and Mapping Strategy

- The system prompt, tool descriptions, dynamic GTAP aggregation context, and default Agent responses use English.
- Canonical English names and codes from `gtap_region_country_map.csv` and `gtap_sector_map.csv` are injected into the system prompt dynamically.
- The LLM converts natural-language requests into tool-validated English identifiers.
- Tool arguments should use ISO/GTAP codes, exact English names, or active aggregate names, such as `chn`, `United States`, `osd`, and `EastAsia`.
- The backend mapping layer does not depend on language-specific country, commodity, or rate-mode aliases.
- After custom aggregation, the Agent must use the new regional and sectoral mapping instead of assuming the default 10-by-10 names.

## Agent Workflow Rules

`BASE_SYSTEM_PROMPT` distinguishes preparation from explicit scenario analysis.

### One-Time Preparation

- `aggregate_gtap_model`
- `aggregate_custom_gtap_model`
- `fetch_observed_data`

### Optional Historical Baseline Preparation

- `build_2024_baseline_update`
- `run_gtap_scenario(set_as_default_baseline=true)`

### Routine Policy Analysis

- Select `baseline_id=original_2014` or `baseline_id=registered_2024` explicitly.
- `modify_gtap_closure`, only if the standard policy closure needs a non-empty documented local swap. Do not call it for the unchanged standard closure.
- `modify_shock_cmf`; with no `base_cmf`, it starts directly from the unchanged standard policy closure.
- `run_gtap_scenario`
- `read_gtap_results`, only for targeted follow-up queries not contained in the automatic run report.

General routing examples:

```text
existing aggregation + standard closure + shocks
  -> modify_shock_cmf(no base_cmf, all shocks in one call)
  -> run_gtap_scenario(exact output_cmf)

existing aggregation + supported closure patch + shocks
  -> modify_gtap_closure(non-empty modifications)
  -> modify_shock_cmf(base_cmf=exact returned output_cmf)
  -> run_gtap_scenario(exact output_cmf)
```

Before changing aggregation, the Agent checks baseline compatibility. `original_2014` is model-local. A newly aggregated model cannot silently reuse `registered_2024`; compatible baseline preparation and registration require explicit authorization. A custom-model name collision does not authorize `overwrite=true`.

If `asset\basedata_2024.har` is missing, the Agent must explain that the default 2024 baseline has not been prepared and request confirmation. It must not silently start the full baseline workflow.

If a user asks only for a CMF, the Agent does not solve it automatically. A successful `run_gtap_scenario` automatically returns the solve/result description, so a separate result-read call is unnecessary unless the user requests deeper variables or dimensions.

## Registered Tools

Tool schemas are defined in `agent.py`. No arbitrary shell execution is exposed.

### `aggregate_gtap_model`

- Takes no arguments.
- Rebuilds `gtap2015_10x10` using the default mapping.
- Is a one-time preparation tool and should not run during routine policy conversations or requests to keep the current aggregation.

### `aggregate_custom_gtap_model`

- Arguments: `aggregation_name`, `model_name`, `region_groups`, `sector_groups`, and `overwrite`.
- Starts from the default mapping and moves only explicitly listed original GTAP members.
- Leaves unlisted members in their default assignments.
- Writes an independent mapping and RunGTAP model without overwriting the default 10-by-10 model.

Example:

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

### `fetch_observed_data`

- Arguments: `start_year`, `end_year`, `refresh`, `skip_wits`, `max_wits_calls`, `wits_sleep`, `wpp_file`, and `aggregation_mapping`.
- Fetches and standardizes World Bank GDP, population, and WITS tariff data.
- A custom model must pass its corresponding `aggregation_mapping`.

### `build_2024_baseline_update`

- Arguments: `base_year`, `target_year`, `include_tariffs`, `write_all_cmfs`, and `model_name`.
- Generates pre-policy baseline-update CMFs.
- Uses the standard GTAP closure plus `Swap avareg(REG)=qgdp(REG)` so that `avareg` is inferred as implicit TFP.

### `modify_gtap_closure`

- Arguments: `baseline_id`, `scenario_name`, `model_name`, and `modifications`.
- Starts from the standard policy closure and permits only local `gdp_target_tfp` or `fixed_regional_investment` swaps for selected regions.
- Requires at least one modification. Standard/normal/unchanged policy closure scenarios skip this tool.
- Rejects arbitrary closure text and writes a CMF plus a machine-readable scenario manifest.

### `modify_shock_cmf`

- Arguments: explicit `baseline_id`, optional `base_cmf`, `output_cmf`, `scenario_name`, `model_name`, and `modifications`.
- `original_2014` uses the selected model's original data; `registered_2024` uses the registered asset baseline. No baseline is inferred.
- Does not overwrite the source CMF. It writes a timestamped policy CMF and resolved JSON/CSV outputs.
- With `base_cmf` omitted, directly creates the scenario from the selected baseline and standard policy closure.
- Multiple compatible shocks belong in one call. Each GTAP variable code has its own parameter-schema branch, so unrelated dimensions are not accepted: for example, `atd` exposes only `importer`, while `aoall` exposes only `sector` and `region`.
- Shock codes must match the requested dimensional scope exactly: worldwide sector (`aosec`), region-wide (`aoreg`), and sector-by-region (`aoall`) are distinct. International shipping technology uses only `atf`, `ats`, or `atd`; the `af*` family describes intermediate-input technology. Positive technology shocks denote improvements.
- The tool description gives the Agent the whitelisted GTAP codes, their economic meanings, valid value modes, and exact required dimensions. These include trade/tax instruments, population and factor supplies, output/value-added/intermediate/factor technologies, import and shipping technologies, and closure-sensitive GDP/investment targets.

Example:

```json
{
  "scenario_name": "China US soybean tariff 20",
  "baseline_id": "registered_2024",
  "modifications": [
    {
      "type": "gtap_variable",
      "code": "tms",
      "importer": "China",
      "exporter": "United States",
      "commodity": "soybeans",
      "value": 20,
      "value_mode": "target_rate"
    }
  ]
}
```

Under the default 10-by-10 mapping, this resolves to a statement similar to:

```text
Shock tms("GrainsCrops","NAmerica","EastAsia") = 2.825983;
```

`target_rate` sets the target ad valorem tariff. The tool reads baseline `RTMS` and converts the target to a percentage change in the GTAP `tms` tax power. `rate_change` applies an ad valorem percentage-point change, while `power_change` directly applies a tax-power shock.

### `run_gtap_scenario`

- Arguments: required `cmf`, optional `model_name`, `result_dir`, `report_top`, and historical-only `set_as_default_baseline`.
- Calls script 03 and the project-local RunGTAP/GEMPACK runtime.
- Infers the model from CMF context, creates a timestamped result directory when omitted, and automatically returns scenario context, solve status, accuracy, warnings, output descriptions, and default economic indicators.
- `set_as_default_baseline=true` is reserved for a successfully solved baseline-update CMF and must not be used for a policy run.

### `read_gtap_results`

- Arguments include `result_dir`, source `view`, variables, headers, semantic filters, raw `dimensions`, limits, and sorting.
- Parameter-queries solution, volume, exact scenario baseline, updated data, tax rates, welfare decomposition, logs, CMFs, and file inventories.
- It does not compare runs. The Agent can query earlier and current runs separately.

## HTTP API

### `POST /api/chat`

Non-streaming JSON endpoint retained for compatibility and debugging.

Request:

```json
{
  "session_id": "optional-session-id",
  "message": "Read the latest results and summarize GDP and welfare changes."
}
```

### `POST /api/chat_stream`

The default frontend endpoint. It returns newline-delimited JSON (NDJSON).

Event types:

- `session`
- `reasoning_delta`
- `assistant_delta`
- `tool_start`
- `tool_end`
- `assistant`
- `error`
- `done`

The page can display Agent text and tool progress incrementally instead of waiting for the entire workflow to finish.

`New Task` discards the current server-side Agent session, creates a fresh session ID, and clears both the visible conversation and Execution Trace. It is disabled while a request is running so an in-progress GTAP tool call cannot be orphaned by a UI reset. `Clear` in the Execution Trace remains display-only and does not reset Agent context.

## Reasoning and Tool Calls

OpenRouter returns structured `reasoning_details` separately from final `content`.

- The frontend renders `reasoning_delta` in a collapsible `Agent reasoning` block.
- The backend preserves every `reasoning_details` block in its original order and passes it back unmodified on subsequent requests, including tool-call continuations.
- Tool-message history is repaired before a new user turn so that incomplete tool-call sequences do not cause model-service errors.
- RunGTAP uses a shared local work directory, so web tool execution is serialized with a lock to prevent concurrent requests from overwriting one another.

## Security Boundaries

- The API key is never sent to the browser.
- The backend does not expose arbitrary command execution.
- Only registered project scripts can be called.
- User-provided paths are checked against the workspace boundary.
- `modify_shock_cmf` does not overwrite its source CMF.
- Custom aggregation refuses to overwrite an existing mapping or model by default.
- `set_as_default_baseline` must be explicit and writes the default baseline only after a successful solve.

## Frontend Rendering

The lightweight Markdown renderer supports:

- Headings, paragraphs, and lists
- Fenced code blocks and inline code
- Bold, italic, and links
- Simple pipe tables

Tool stdout and stderr remain plain text in code blocks so that log output is not interpreted as Markdown.

## Validation

After starting the server:

```powershell
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8765/
```

UI acceptance checks:

- `<html lang="en">`
- Page title is `GTAP Agent Console`
- Welcome message, quick actions, placeholder, status, and Execution Trace contain no localized text
- An isolated test session receives an English response by default
