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
| `agent.py` | English system prompt, tool schemas, sessions, DeepSeek requests, and tool execution |

`server.py` remains intentionally small. Agent orchestration and GTAP workflow logic do not live in the HTTP handler.

## Starting the App

The API key is loaded in this order:

1. `DEEPSEEK_API_KEY` environment variable
2. `scripts\key.txt`

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

`BASE_SYSTEM_PROMPT` distinguishes three stages.

### One-Time Preparation

- `aggregate_gtap_model`
- `aggregate_custom_gtap_model`
- `fetch_observed_data`

### Default 2024 Baseline

- `build_2024_baseline_update`
- `run_gtap_scenario(set_as_default_baseline=true)`

### Routine Policy Analysis

- `modify_shock_cmf(base_year=2024)`
- `run_gtap_scenario`
- `read_gtap_results`

If `asset\basedata_2024.har` is missing, the Agent must explain that the default 2024 baseline has not been prepared and request confirmation. It must not silently start the full baseline workflow.

If a user asks only for a CMF, the Agent does not solve it automatically. It calls `run_gtap_scenario` only when a run is requested, and calls `read_gtap_results` only when interpretation is requested.

## Registered Tools

Tool schemas are defined in `agent.py`. No arbitrary shell execution is exposed.

### `aggregate_gtap_model`

- Takes no arguments.
- Rebuilds `gtap2015_10x10` using the default mapping.
- Is a one-time preparation tool and should not run during routine policy conversations.

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

### `modify_shock_cmf`

- Arguments: `base_year`, `base_cmf`, `output_cmf`, `scenario_name`, `model_name`, and `modifications`.
- Defaults to `base_year=2024` and creates a clean standard-policy-closure CMF from the project's default 2024 baseline.
- Does not overwrite the source CMF. It writes a timestamped policy CMF and resolved JSON/CSV outputs.
- Supports:
  - `bilateral_import_tariff`
  - `regional_population`
  - `regional_endowment`
  - `regional_productivity`

Example:

```json
{
  "scenario_name": "China US soybean tariff 20",
  "modifications": [
    {
      "type": "bilateral_import_tariff",
      "importer": "China",
      "exporter": "United States",
      "commodity": "soybeans",
      "tariff_percent": 20,
      "rate_mode": "target_rate"
    }
  ]
}
```

Under the default 10-by-10 mapping, this resolves to a statement similar to:

```text
Shock tms("GrainsCrops","NAMerica","EastAsia") = 2.825983;
```

`target_rate` sets the target ad valorem tariff. The tool reads baseline `RTMS` and converts the target to a percentage change in the GTAP `tms` tax power. `rate_change` applies an ad valorem percentage-point change, while `power_change` directly applies a tax-power shock.

### `run_gtap_scenario`

- Arguments: `cmf`, `model_name`, `result_dir`, and `set_as_default_baseline`.
- Calls script 03 and the project-local RunGTAP/GEMPACK runtime.
- `set_as_default_baseline=true` is reserved for a successfully solved baseline-update CMF and must not be used for a policy run.

### `read_gtap_results`

- Arguments: `result_dir`, `view`, `variables`, `headers`, `region`, `sector`, `exporter`, `importer`, `contains`, `max_rows`, `top`, `include_baseline`, and `sort_by_abs`.
- Reads broad summaries, `.sol`, HAR data, welfare, logs, CMFs, and file inventories.
- The default summary includes solve status, pre-run shocks, accuracy, GDP, `avareg`, EV, trade, and sector output.

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

## Reasoning and Tool Calls

The model service returns `reasoning_content` separately from final `content`.

- The frontend renders `reasoning_delta` in a collapsible `Agent reasoning` block.
- When an assistant message includes `tool_calls`, the backend preserves the required `reasoning_content` in subsequent requests within the same user turn.
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
