from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from openai import OpenAI


PROJECT_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_DIR / "scripts"
RESULT_DIR = PROJECT_DIR / "result"
AGGREGATION_CONFIG_DIR = PROJECT_DIR / "config" / "aggregations"
KEY_FILE = SCRIPTS_DIR / "key.txt"
STANDARDIZED_DIR = RESULT_DIR / "04_observed_data" / "standardized"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
from gtap_aggregation import normalize_label, parse_mapping_sections, split_member, split_target  # noqa: E402
from gtap_scenario import extract_cmf_context, read_default_baseline_metadata  # noqa: E402

OPENROUTER_BASE_URL = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "openai/gpt-5.6-luna")
MAX_TOOL_ROUNDS = 8

SESSIONS: dict[str, list[dict[str, Any]]] = {}
SYSTEM_PROMPT_CACHE: str | None = None
TOOL_EXECUTION_LOCK = threading.Lock()
_OPENROUTER_CLIENT: OpenAI | None = None


BASE_SYSTEM_PROMPT = """You are the agent for the GTAP automation workflow. Understand the user's objective, select the registered tools, inspect their outputs, and give concise evidence-based responses in English.

Core workflow:
- A GTAP experiment has an aggregation, an explicit input baseline, a closure, one or more shocks, a solve, and result queries.
- Never invent results, paths, aggregate mappings, GTAP codes, or dimensions. Use returned artifacts and structured output.
- File generation, solving, and follow-up result querying are separate. Run only when requested.

Decision protocol before the first tool call:
1. Classify the request as preparation, a routine scenario, a result-only query, clarification, or an unsupported model change.
2. Resolve the requested aggregation and explicit baseline. Words such as current, existing, retain, keep, or unchanged mean reuse the active aggregation; they never authorize an aggregation tool call.
3. Check baseline/model compatibility before any state-changing preparation. original_2014 is model-local and can be used with a newly aggregated model. registered_2024 is bound to the model recorded in baseline metadata; a new aggregation cannot reuse it without rebuilding and explicitly registering compatible baseline assets. If that work is forbidden or not authorized, ask one focused question before changing aggregation or writing files.
4. Decide whether the standard policy closure is unchanged or a documented patch is required. For the unchanged standard closure, do not call modify_gtap_closure: call modify_shock_cmf directly with the explicit baseline and omit base_cmf. Call modify_gtap_closure only for a non-empty supported patch, then pass its exact returned output_cmf as base_cmf.
5. Put all compatible shocks for the experiment in one modify_shock_cmf call. Use type=gtap_variable, select the whitelisted code whose schema matches the intended scope, and pass only the dimensions exposed by that code branch.
6. Pass the exact output_cmf returned by the preceding tool to run_gtap_scenario. Never guess, shorten, or reconstruct artifact paths.
7. Treat the automatic run report as the default result response. Use read_gtap_results only for exact requested cells or sources absent from that report, and pass the exact result_dir returned by the run tool.

Explicit baseline rule:
- original_2014 means the original basedata.har and baserate.har in the selected RunGTAP model.
- registered_2024 means asset/basedata_2024.har and asset/baserate_2024.har registered in asset/default_baseline.json.
- Never use the ambiguous word base as a tool value and never infer a baseline for closure, shock, or run operations. If the user's intended baseline is unclear, ask whether to use original_2014 or registered_2024 before calling a scenario tool.
- build_2024_baseline_update is an optional historical-preparation recipe, not the definition of GTAP Agent. Use it only when the user explicitly requests construction or refresh of the registered 2024 baseline.

Local modifications:
- Aggregation tools start from the active mapping in the context below. Move only the requested original regions/sectors; unspecified members and factor aggregation stay unchanged.
- aggregate_gtap_model is not a discovery, validation, closure, or CMF-generation tool. Call it only when the user explicitly asks to rebuild the bundled model or a required bundled model artifact is reported missing.
- Never set overwrite=true merely to recover from a name collision. Unless the user explicitly authorized replacement of that exact custom model, choose a new non-conflicting custom name and preserve the existing model.
- modify_gtap_closure starts from the standard policy closure and accepts only a non-empty list of its documented local swap types. An unchanged standard, normal, or ordinary policy closure needs no closure-tool call.
- modify_shock_cmf documents every whitelisted GTAP code, meaning, and dimension in its tool description. Select the narrowest code that matches the user's request. Do not invent codes or raw CMF statements. qgdp and qcgds require the corresponding closure patch CMF.
- Match a shock code's declared dimensions exactly to the economic scope. A sector shock in one region needs a sector-by-region code, not a worldwide sector code. International shipping/margin technology uses atf/ats/atd; af* is intermediate-input technology and afe* is primary-factor technology, never shipping. Positive technology-shock values mean improvements/augmentation.
- Always state the resolved active aggregates in the response.

General tool-routing patterns:
- Existing aggregation + standard closure + ordinary shocks: modify_shock_cmf(baseline_id, scenario_name, modifications) -> run_gtap_scenario(output_cmf). No aggregation call, no closure call, and no base_cmf.
- Existing aggregation + supported closure target: modify_gtap_closure(non-empty modifications) -> modify_shock_cmf(base_cmf=exact output_cmf, modifications) -> run_gtap_scenario(exact output_cmf).
- Requested local aggregation + original_2014: aggregate_custom_gtap_model(minimal member moves) -> use its model_name for closure/shock/run tools.
- Requested local aggregation + registered_2024: first establish authorization to prepare and register model-compatible baseline assets. If authorization is absent, stop before aggregation.

Solve and results:
- run_gtap_scenario requires an explicit CMF and automatically returns the structured solve report after success, including baseline, closure, shocks, status, accuracy, warnings, output catalog, and default economic indicators.
- Do not call read_gtap_results merely to describe a just-completed run. Call it only for targeted follow-up variables, headers, sources, or dimensions not already present in the automatic report.
- read_gtap_results queries one run at a time. If the user asks for a comparison, read each requested run separately and reason over the returned values; there is no comparison tool.
- If a tool fails, retry only when the correction is local, directly supported by the error, and preserves the user's requested scope. Do not call an unrelated preparation tool to recover from a closure, shock, run, or result-query error. A rejected empty closure modification means the closure tool was unnecessary; for a standard closure, proceed through modify_shock_cmf without base_cmf.

Preparation:
- aggregate_gtap_model, fetch_observed_data, build_2024_baseline_update, and baseline registration are occasional preparation operations, not routine policy steps.
- A custom aggregation requires compatible mappings and baseline assets before it can be used.

Response style:
- Lead with status and important model context, then give result paths or the next action.
- Do not use emoji.
"""


SHOCK_DIMENSION_PROPERTIES: dict[str, dict[str, Any]] = {
    "commodity": {"type": "string", "minLength": 1, "description": "Commodity or active aggregate sector."},
    "sector": {"type": "string", "minLength": 1, "description": "Producing sector or active aggregate sector."},
    "factor": {"type": "string", "minLength": 1, "description": "Endowment/factor: Land, UnSkLab, SkLab, Capital, or NatRes."},
    "region": {"type": "string", "minLength": 1, "description": "Country or active aggregate region."},
    "exporter": {"type": "string", "minLength": 1, "description": "Source/exporter country or active aggregate region."},
    "importer": {"type": "string", "minLength": 1, "description": "Destination/importer country or active aggregate region."},
}

SHOCK_TOOL_SPECS: dict[str, dict[str, Any]] = {
    "tms": {"dimensions": ["commodity", "exporter", "importer"], "description": "bilateral import tariff/tax power", "modes": ["target_rate", "rate_change", "power_change"]},
    "tm": {"dimensions": ["commodity", "importer"], "description": "source-generic import-tax power"},
    "txs": {"dimensions": ["commodity", "exporter", "importer"], "description": "bilateral export-tax/subsidy power"},
    "tx": {"dimensions": ["commodity", "exporter"], "description": "destination-generic export-tax/subsidy power"},
    "to": {"dimensions": ["commodity", "region"], "description": "commodity output/income-tax power"},
    "tp": {"dimensions": ["region"], "description": "uniform private-consumption-tax shift"},
    "pop": {"dimensions": ["region"], "description": "regional population"},
    "qo": {"dimensions": ["factor", "region"], "description": "regional factor-endowment supply"},
    "aosec": {"dimensions": ["sector"], "description": "output technology for one sector worldwide; positive is an improvement"},
    "aoreg": {"dimensions": ["region"], "description": "region-wide output technology; positive is an improvement"},
    "aoall": {"dimensions": ["sector", "region"], "description": "output technology for one sector in one region; positive is an improvement"},
    "avasec": {"dimensions": ["sector"], "description": "value-added technology for one sector worldwide; positive is an improvement"},
    "avareg": {"dimensions": ["region"], "description": "region-wide value-added technology; positive is an improvement"},
    "afcom": {"dimensions": ["commodity"], "description": "worldwide commodity-specific intermediate-input technology; positive is an improvement"},
    "afsec": {"dimensions": ["sector"], "description": "sector-wide intermediate-input technology; positive is an improvement"},
    "afreg": {"dimensions": ["region"], "description": "regional intermediate-input technology; not international shipping; positive is an improvement"},
    "afall": {"dimensions": ["commodity", "sector", "region"], "description": "commodity-sector-region intermediate-input technology; positive is an improvement"},
    "afecom": {"dimensions": ["factor"], "description": "worldwide factor-specific primary-factor technology; positive is an improvement"},
    "afesec": {"dimensions": ["sector"], "description": "sector-wide primary-factor technology; positive is an improvement"},
    "afereg": {"dimensions": ["region"], "description": "regional primary-factor technology; positive is an improvement"},
    "afeall": {"dimensions": ["factor", "sector", "region"], "description": "factor-sector-region primary-factor technology; positive is an improvement"},
    "ams": {"dimensions": ["commodity", "exporter", "importer"], "description": "bilateral import-augmenting technology; positive is an improvement"},
    "atf": {"dimensions": ["commodity"], "description": "commodity-specific international-shipping technology; positive is an improvement"},
    "ats": {"dimensions": ["exporter"], "description": "origin/exporter-specific international-shipping technology; positive is an improvement"},
    "atd": {"dimensions": ["importer"], "description": "destination/importer-specific international-shipping technology for all deliveries to that destination; positive is an improvement"},
    "qgdp": {"dimensions": ["region"], "description": "regional real-GDP target; requires gdp_target_tfp closure CMF"},
    "qcgds": {"dimensions": ["region"], "description": "regional real-investment target; requires fixed_regional_investment closure CMF"},
}


def shock_tool_variant(code: str, spec: dict[str, Any]) -> dict[str, Any]:
    dimensions = list(spec["dimensions"])
    modes = list(spec.get("modes") or ["percent_change"])
    properties: dict[str, Any] = {
        "type": {"type": "string", "enum": ["gtap_variable"], "description": "Structured whitelisted GTAP variable shock."},
        "code": {"type": "string", "enum": [code], "description": f"{code}: {spec['description']}."},
    }
    for dimension in dimensions:
        properties[dimension] = SHOCK_DIMENSION_PROPERTIES[dimension]
    properties.update(
        {
            "value": {
                "type": "number",
                "description": "Requested value. Positive technology values mean improvements. Tariff target/rate modes use percentage points.",
            },
            "value_mode": {
                "type": "string",
                "enum": modes,
                "description": "Use the mode allowed for this code; non-tariff shocks use percent_change.",
            },
            "note": {"type": "string", "minLength": 1, "description": "Optional audit note."},
        }
    )
    return {
        "type": "object",
        "title": f"{code}({','.join(dimensions)})",
        "description": spec["description"],
        "properties": properties,
        "required": ["type", "code", *dimensions, "value", "value_mode"],
        "additionalProperties": False,
    }


SHOCK_TOOL_VARIANTS = [shock_tool_variant(code, spec) for code, spec in SHOCK_TOOL_SPECS.items()]


TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "aggregate_gtap_model",
            "description": "Explicit preparation only: rebuild the bundled default model with its existing mapping. Call only when the user asks to rebuild it or a required bundled model artifact is reported missing. Never call for a request that says use/keep/retain the current or existing aggregation. This tool does not create a closure CMF, policy CMF, or scenario result and cannot repair a closure/shock error.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "aggregate_custom_gtap_model",
            "description": "State-changing preparation: apply only requested regional or sectoral member moves to the current mapping; all unlisted members and the factor aggregation remain unchanged. Use only when the requested analysis needs detail absent from the active aggregation. Before calling, verify baseline compatibility: original_2014 can use the new model, while registered_2024 requires separately prepared and explicitly registered model-compatible assets. A name collision is not permission to overwrite; choose a new name unless the user explicitly authorized replacement of that exact custom model.",
            "parameters": {
                "type": "object",
                "properties": {
                    "aggregation_name": {
                        "type": "string",
                        "description": "Short reusable name for this aggregation, e.g. china_us_split.",
                    },
                    "model_name": {
                        "type": "string",
                        "description": "Optional safe RunGTAP model directory name. Defaults to gtap2015_<aggregation_name>; gtap2015_10x10 is reserved.",
                    },
                    "region_groups": {
                        "type": "array",
                        "description": "Region overrides. Each group moves only the listed original GTAP regions; all unlisted regions retain the default mapping.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string", "description": "Aggregate region name, maximum 12 characters."},
                                "description": {"type": "string", "description": "Optional description, maximum 30 characters."},
                                "members": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "Original GTAP region codes or exact names, e.g. chn, usa, China.",
                                },
                            },
                            "required": ["name", "members"],
                            "additionalProperties": False,
                        },
                    },
                    "sector_groups": {
                        "type": "array",
                        "description": "Sector overrides. Each group moves only the listed original GTAP sectors; all unlisted sectors retain the default mapping.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string", "description": "Aggregate sector name, maximum 12 characters."},
                                "description": {"type": "string", "description": "Optional description, maximum 30 characters."},
                                "members": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "Original GTAP sector codes or exact names, e.g. osd, wht, Oil seeds.",
                                },
                            },
                            "required": ["name", "members"],
                            "additionalProperties": False,
                        },
                    },
                    "overwrite": {
                        "type": "boolean",
                        "description": "Destructive replacement of the exact existing custom mapping/model. Default false. Never switch this to true merely because a first call reports a name collision; explicit user authorization for replacement is required.",
                    },
                },
                "required": ["aggregation_name"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_observed_data",
            "description": "Run script 04 to incrementally fetch and standardize World Bank GDP, population, and WITS tariff data.",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_year": {"type": "integer", "description": "First year to fetch. Default 2014."},
                    "end_year": {"type": "integer", "description": "Last year to fetch. Defaults to latest available."},
                    "refresh": {"type": "boolean", "description": "Ignore cached raw responses."},
                    "skip_wits": {"type": "boolean", "description": "Skip WITS tariff calls."},
                    "max_wits_calls": {"type": "integer", "description": "Limit WITS calls for a smoke test."},
                    "wits_sleep": {"type": "number", "description": "Seconds to sleep between WITS calls."},
                    "wpp_file": {"type": "string", "description": "Optional local WPP CSV/XLSX file path."},
                    "aggregation_mapping": {
                        "type": "string",
                        "description": "Optional custom GTAPAgg mapping returned by aggregate_custom_gtap_model. It controls the standardized region/sector lookup tables.",
                    },
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "build_2024_baseline_update",
            "description": "Optional bundled historical-preparation recipe, not a routine GTAP scenario step: use observed macro data to build 2014-to-target-year baseline-update CMFs with regional avareg=qgdp swaps. Call only when the user explicitly asks to create or refresh the registered 2024 baseline.",
            "parameters": {
                "type": "object",
                "properties": {
                    "base_year": {"type": "integer", "description": "Original GTAP database year. Default 2014."},
                    "target_year": {"type": "integer", "description": "Updated baseline year. Defaults to latest common year, currently 2024 when data are available."},
                    "include_tariffs": {"type": "boolean", "description": "Include WITS tariff updates in the baseline CMF."},
                    "write_all_cmfs": {"type": "boolean", "description": "Write baseline-update CMF files for all available target years."},
                    "model_name": {
                        "type": "string",
                        "description": "RunGTAP model used by the generated baseline CMF. Use the model_name returned by a custom aggregation.",
                    },
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "modify_gtap_closure",
            "description": "Use only when the user requests a documented change to the standard multiregion policy closure. A non-empty modifications array is mandatory. DO NOT call for a standard, normal, ordinary, unchanged, or retained policy closure: modify_shock_cmf without base_cmf already starts from that closure. Supported patches: gdp_target_tfp swaps avareg(REG)=qgdp(REG); fixed_regional_investment swaps cgdslack(REG)=qcgds(REG). The tool returns a shock-free output_cmf; pass that exact path as base_cmf to modify_shock_cmf. It does not accept raw closure text or arbitrary lists.",
            "parameters": {
                "type": "object",
                "properties": {
                    "baseline_id": {
                        "type": "string",
                        "enum": ["original_2014", "registered_2024"],
                        "description": "Explicit model input: original_2014 uses the model's original GTAP database; registered_2024 uses asset/basedata_2024.har. Never infer this from the word base.",
                    },
                    "scenario_name": {"type": "string", "description": "Name for the new closure CMF."},
                    "model_name": {"type": "string", "description": "Optional model. registered_2024 must use the model recorded in baseline metadata."},
                    "modifications": {
                        "type": "array",
                        "minItems": 1,
                        "description": "One or more requested supported closure patches. Never pass an empty array; omit the entire closure-tool call when the standard closure is unchanged.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "type": {
                                    "type": "string",
                                    "enum": ["gdp_target_tfp", "fixed_regional_investment"],
                                    "description": "Approved local closure swap.",
                                },
                                "regions": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "Countries/codes/active aggregate regions to which the swap applies.",
                                },
                            },
                            "required": ["type", "regions"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["baseline_id", "modifications"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "modify_shock_cmf",
            "description": "Routine scenario entry point. When base_cmf is omitted, create a scenario directly from the explicit baseline and unchanged standard policy closure. Supply base_cmf only for the exact CMF returned by a supported closure patch or earlier shock modification. Put all compatible shocks in one call. Each modification uses type=gtap_variable and a code-specific schema branch that exposes only the dimensions that code actually accepts. Choose the narrowest branch matching the intended scope. In particular, atd(importer) is one destination-wide international-shipping improvement and must be one shock, not one shock per commodity; af* is intermediate-input technology, not shipping. Positive technology values mean improvements. qgdp requires a gdp_target_tfp base_cmf and qcgds requires a fixed_regional_investment base_cmf. Raw CMF statements and unlisted codes are rejected.",
            "parameters": {
                "type": "object",
                "properties": {
                    "baseline_id": {
                        "type": "string",
                        "enum": ["original_2014", "registered_2024"],
                        "description": "Required explicit scenario input. Routine policy work may use registered_2024; original database experiments use original_2014.",
                    },
                    "base_cmf": {
                        "type": "string",
                        "minLength": 1,
                        "pattern": "\\S",
                        "description": "Omit for the unchanged standard policy closure. Otherwise use only the exact output_cmf returned by modify_gtap_closure or an earlier shock modification; baseline and model must match. Never send an empty string or an invented path.",
                    },
                    "output_cmf": {
                        "type": "string",
                        "minLength": 1,
                        "pattern": "\\S",
                        "description": "Optional output CMF path under result/. If omitted, a timestamped policy CMF is created.",
                    },
                    "scenario_name": {
                        "type": "string",
                        "minLength": 1,
                        "pattern": "\\S",
                        "description": "Short scenario name for audit comments and output naming.",
                    },
                    "model_name": {
                        "type": "string",
                        "minLength": 1,
                        "pattern": "\\S",
                        "description": "Optional RunGTAP model. registered_2024 is bound to the model in baseline metadata.",
                    },
                    "modifications": {
                        "type": "array",
                        "minItems": 1,
                        "description": "All structured GTAP variable shocks for this experiment. Select one code-specific branch per shock; unrelated dimensions are not accepted.",
                        "items": {"oneOf": SHOCK_TOOL_VARIANTS},
                    },
                },
                "required": ["baseline_id", "scenario_name", "modifications"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_gtap_scenario",
            "description": "Solve one explicit CMF through RunGTAP/GEMPACK. Pass the exact output_cmf returned by the immediately preceding closure/shock/baseline tool; never reconstruct its path. The model is normally inferred from CMF context. A successful call returns the exact result_dir plus a structured report with baseline, closure, shocks, status, accuracy, warnings, output catalog, GDP, welfare, trade, sector, and volume summaries. Answer from that report first; use read_gtap_results only for explicitly requested cells or sources absent from it.",
            "parameters": {
                "type": "object",
                "properties": {
                    "cmf": {"type": "string", "description": "Exact output_cmf returned by a closure, shock, or baseline-generation tool."},
                    "model_name": {"type": "string", "description": "Optional model override; normally inferred from CMF context."},
                    "result_dir": {"type": "string", "description": "Optional unique output directory under result/. A timestamped directory is created when omitted."},
                    "report_top": {"type": "integer", "description": "Top rows included in the automatic result report. Default 8."},
                    "set_as_default_baseline": {
                        "type": "boolean",
                        "description": "Historical preparation only: register a successfully solved generated 2014-to-2024 baseline-update CMF. Never use for policy scenarios.",
                    },
                },
                "required": ["cmf"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_gtap_results",
            "description": "Focused follow-up only after run_gtap_scenario's automatic report. Do not repeat the default report or query values already returned there. Pass the exact result_dir returned by the run tool; never derive it from a scenario name or choose latest when the current run is known. Sources: solution, volume, welfare, welfare_decomposition, updated_data, base_data for the scenario's explicit baseline, tax_rates, log, cmf, and files. Query only requested variables/headers and needed dimensions. Rows preserve code, value, LongName, source, and dimensions. To compare runs, query each exact result_dir separately.",
            "parameters": {
                "type": "object",
                "properties": {
                    "result_dir": {
                        "type": "string",
                        "description": "Exact result_dir returned by run_gtap_scenario. Omit only for an explicit user request about the newest prior run; never guess or reconstruct the current run directory.",
                    },
                    "view": {
                        "type": "string",
                        "enum": [
                            "default",
                            "solution",
                            "volume",
                            "updated_data",
                            "base_data",
                            "tax_rates",
                            "welfare_decomposition",
                            "welfare",
                            "log",
                            "cmf",
                            "files",
                        ],
                        "description": "Select the result source. default repeats the broad report; other views perform focused queries.",
                    },
                    "variables": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "GTAP variable names to read, e.g. qgdp, avareg, EV, DTBAL, tot, qo, qxs, tms. The automatic run report includes available codes and LongNames.",
                    },
                    "headers": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional raw HAR headers to read when known, e.g. AG01, DQO, 0036.",
                    },
                    "region": {"type": "string", "description": "Aggregate region filter, e.g. EastAsia."},
                    "sector": {"type": "string", "description": "Aggregate sector or commodity filter, e.g. GrainsCrops."},
                    "exporter": {"type": "string", "description": "Source/exporter aggregate region for bilateral variables."},
                    "importer": {"type": "string", "description": "Destination/importer aggregate region for bilateral variables."},
                    "contains": {"type": "string", "description": "Case-insensitive substring filter over dimensions and LongName."},
                    "dimensions": {
                        "type": "object",
                        "additionalProperties": {"type": "string"},
                        "description": "Raw HAR dimension filters, e.g. {\"REG\":\"NAmerica\",\"TRAD_COMM\":\"GrainsCrops\"}.",
                    },
                    "max_rows": {"type": "integer", "description": "Maximum rows to return for query/detail views. Default 40."},
                    "top": {"type": "integer", "description": "Number of top absolute-value rows for default summaries. Default 8."},
                    "include_baseline": {
                        "type": "boolean",
                        "description": "Include compact baseline basedata.har and updated view excerpts when possible.",
                    },
                    "sort_by_abs": {
                        "type": "boolean",
                        "description": "Sort query rows by absolute value. Defaults true; set false to keep file order.",
                    },
                },
                "additionalProperties": False,
            },
        },
    },
]


SCRIPT_SUMMARIES = {
    "01_aggregate_gtap10a_2014_to_10x10.py": [RESULT_DIR / "01_aggregation" / "aggregation_summary.txt"],
    "04_fetch_observed_calibration_data.py": [RESULT_DIR / "04_observed_data" / "fetch_manifest.json"],
    "05_build_2024_baseline_update.py": [RESULT_DIR / "05_baseline_update" / "baseline_update_summary.txt"],
    "modify_gtap_closure.py": [],
    "06_apply_policy_shock_modifications.py": [RESULT_DIR / "06_policy_modifications" / "policy_modification_summary.txt"],
    "03_run_rungtap_scenario.py": [],
    "07_read_gtap_results.py": [RESULT_DIR / "07_result_reads" / "latest_result_read.json"],
}


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def aggregation_context_text() -> str:
    metadata = read_default_baseline_metadata()
    model_name = str(metadata.get("model_name") or "gtap2015_10x10")
    mapping_value = metadata.get("aggregation_mapping")
    mapping_path = Path(str(mapping_value)) if mapping_value else PROJECT_DIR / "runtime" / "rungtap" / model_name / "aggregation_mapping.txt"
    if not mapping_path.is_file():
        return (
            "Current GTAP aggregation context:\n"
            f"- The model-bound aggregation mapping was not found: {mapping_path}.\n"
        )

    sections = parse_mapping_sections(mapping_path.read_text(encoding="utf-8", errors="replace"))
    sector_members = {split_target(line)[0]: [] for line in sections[0]}
    canonical_sectors = {normalize_label(name): name for name in sector_members}
    for line in sections[1]:
        item = split_member(line)
        target = canonical_sectors.get(normalize_label(item["target"]), item["target"])
        sector_members.setdefault(target, []).append(f"{item['code']}({item['description']})")
    region_members = {split_target(line)[0]: [] for line in sections[2]}
    canonical_regions = {normalize_label(name): name for name in region_members}
    for line in sections[3]:
        item = split_member(line)
        target = canonical_regions.get(normalize_label(item["target"]), item["target"])
        region_members.setdefault(target, []).append(f"{item['code']}({item['description']})")
    factor_names = [line.split("&", 1)[0].strip() for line in sections[4]]

    lines = [
        "",
        "Current GTAP aggregation context:",
        f"- Model: {model_name}; mapping: {mapping_path}.",
        f"- Model resolution: {len(region_members)} aggregate regions and {len(sector_members)} aggregate sectors. Countries and specific commodities in natural-language requests must be mapped to these aggregates.",
        "- Regional aggregation:",
    ]
    for region in sorted(region_members):
        lines.append(f"  - {region}: {', '.join(region_members[region])}")
    lines.append("- Sectoral aggregation:")
    for sector in sorted(sector_members):
        lines.append(f"  - {sector}: {', '.join(sector_members[sector])}")
    lines.append(f"- Factor aggregation: {', '.join(factor_names)}")
    lines.extend(
        [
            "- The modification tool validates mappings again. If mapping fails, tell the user which aggregates are available.",
            "- Policy tools must resolve countries and commodities using the active mapping above. After a custom aggregation, do not continue assuming the default NAmerica, EastAsia, or GrainsCrops aggregates.",
        ]
    )
    return "\n".join(lines)


def build_system_prompt() -> str:
    global SYSTEM_PROMPT_CACHE
    if SYSTEM_PROMPT_CACHE is None:
        SYSTEM_PROMPT_CACHE = BASE_SYSTEM_PROMPT + "\n" + aggregation_context_text()
    return SYSTEM_PROMPT_CACHE


def session_messages(session_id: str) -> list[dict[str, Any]]:
    messages = SESSIONS.setdefault(session_id, [{"role": "system", "content": build_system_prompt()}])
    if messages and messages[0].get("role") == "system":
        messages[0]["content"] = build_system_prompt()
    return messages


def reset_session(session_id: str) -> bool:
    """Discard one conversation without affecting artifacts or other sessions."""
    return SESSIONS.pop(session_id, None) is not None


def repair_message_history(messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    repaired: list[dict[str, Any]] = []
    changed = False
    index = 0
    while index < len(messages):
        message = messages[index]
        role = message.get("role")
        tool_calls = message.get("tool_calls") or []
        if role == "assistant" and tool_calls:
            tool_call_ids = [tool_call.get("id") for tool_call in tool_calls if tool_call.get("id")]
            following = messages[index + 1 : index + 1 + len(tool_call_ids)]
            following_ids = [item.get("tool_call_id") for item in following if item.get("role") == "tool"]
            if (
                len(tool_call_ids) != len(tool_calls)
                or len(following) != len(tool_call_ids)
                or any(item.get("role") != "tool" for item in following)
                or set(following_ids) != set(tool_call_ids)
            ):
                changed = True
                break
            repaired.append(message)
            repaired.extend(following)
            index += 1 + len(tool_call_ids)
            continue
        if role == "tool":
            changed = True
            index += 1
            continue
        repaired.append(message)
        index += 1
    return repaired, changed or len(repaired) != len(messages)


def start_user_turn(session_id: str, user_text: str) -> list[dict[str, Any]]:
    messages = session_messages(session_id)
    repaired, changed = repair_message_history(messages)
    if changed:
        SESSIONS[session_id] = repaired
        messages = repaired
    messages.append({"role": "user", "content": user_text})
    return messages


def load_api_key() -> str:
    env_key = os.environ.get("OPENROUTER_API_KEY")
    if env_key:
        return env_key.strip()
    if KEY_FILE.exists():
        lines = KEY_FILE.read_text(encoding="utf-8").splitlines()
        if len(lines) >= 2 and lines[1].strip():
            return lines[1].strip()
    raise RuntimeError(
        "OpenRouter API key not found. Set OPENROUTER_API_KEY or place it on line 2 of scripts/key.txt."
    )


def openrouter_client() -> OpenAI:
    global _OPENROUTER_CLIENT
    if _OPENROUTER_CLIENT is None:
        _OPENROUTER_CLIENT = OpenAI(
            base_url=OPENROUTER_BASE_URL,
            api_key=load_api_key(),
            timeout=120.0,
        )
    return _OPENROUTER_CLIENT


def safe_project_path(value: str | None, default: Path | None = None) -> Path | None:
    if not value:
        return default
    path = Path(value)
    if not path.is_absolute():
        path = PROJECT_DIR / path
    resolved = path.resolve()
    project = PROJECT_DIR.resolve()
    if resolved != project and project not in resolved.parents:
        raise ValueError(f"Path is outside project workspace: {value}")
    return resolved


def safe_result_path(value: str | None, default: Path) -> Path:
    if not value:
        return default
    path = Path(value)
    if not path.is_absolute():
        if path.parts and path.parts[0].lower() == "result":
            path = PROJECT_DIR / path
        else:
            path = RESULT_DIR / path
    resolved = path.resolve()
    result = RESULT_DIR.resolve()
    if resolved != result and result not in resolved.parents:
        raise ValueError(f"Result path is outside result directory: {value}")
    return resolved


def safe_model_name(value: object, default: str = "gtap2015_10x10") -> str:
    name = str(value or default).strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", name):
        raise ValueError("model_name must contain only letters, digits, underscores, or hyphens and cannot be a path.")
    return name


def aggregation_slug(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("aggregation_name must contain at least one letter or digit.")
    slug = re.sub(r"[^a-z0-9_-]+", "_", raw.lower()).strip("_")
    if not slug:
        slug = f"custom_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:10]}"
    return slug[:48]


def tail(text: str, max_chars: int = 5000) -> str:
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def read_summary_files(script_name: str, extra_result_dir: Path | None = None) -> dict[str, str]:
    paths = list(SCRIPT_SUMMARIES.get(script_name, []))
    if extra_result_dir:
        paths.append(extra_result_dir / "run_summary.txt")
    summaries = {}
    for path in paths:
        if path.exists():
            summaries[str(path)] = tail(path.read_text(encoding="utf-8", errors="replace"), 4000)
    return summaries


def run_python_script(script_name: str, args: list[str] | None = None, timeout: int = 900, result_dir: Path | None = None) -> dict[str, Any]:
    script_path = SCRIPTS_DIR / script_name
    if not script_path.exists():
        raise FileNotFoundError(f"Script not found: {script_path}")
    command = [sys.executable, str(script_path)] + (args or [])
    started = time.time()
    completed = subprocess.run(
        command,
        cwd=PROJECT_DIR,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    return {
        "script": script_name,
        "command": " ".join(command),
        "returncode": completed.returncode,
        "elapsed_seconds": round(time.time() - started, 2),
        "stdout_tail": tail(completed.stdout),
        "stderr_tail": tail(completed.stderr),
        "summaries": read_summary_files(script_name, result_dir),
    }


def tool_aggregate_gtap_model(_: dict[str, Any]) -> dict[str, Any]:
    return run_python_script("01_aggregate_gtap10a_2014_to_10x10.py")


def tool_aggregate_custom_gtap_model(arguments: dict[str, Any]) -> dict[str, Any]:
    aggregation_name = str(arguments.get("aggregation_name") or "").strip()
    if not aggregation_name:
        raise ValueError("aggregate_custom_gtap_model requires aggregation_name.")
    region_groups = arguments.get("region_groups") or []
    sector_groups = arguments.get("sector_groups") or []
    if not region_groups and not sector_groups:
        raise ValueError("Custom aggregation requires at least one region_groups or sector_groups override.")

    slug = aggregation_slug(aggregation_name)
    model_name = safe_model_name(arguments.get("model_name"), f"gtap2015_{slug}")
    if model_name.lower() == "gtap2015_10x10":
        raise ValueError("gtap2015_10x10 is reserved for aggregate_gtap_model; choose a separate custom model_name.")
    mapping_file = AGGREGATION_CONFIG_DIR / f"{slug}.txt"
    spec = {
        "aggregation_name": aggregation_name,
        "region_groups": region_groups,
        "sector_groups": sector_groups,
    }
    args = [
        "--custom-spec-json",
        json.dumps(spec, ensure_ascii=False),
        "--mapping-output",
        str(mapping_file),
        "--model-name",
        model_name,
    ]
    if arguments.get("overwrite"):
        args.append("--overwrite")
    output = run_python_script("01_aggregate_gtap10a_2014_to_10x10.py", args=args, timeout=1800)
    output.update(
        {
            "aggregation_name": aggregation_name,
            "mapping_file": str(mapping_file),
            "model_name": model_name,
            "next_steps": [
                f"fetch_observed_data(aggregation_mapping={mapping_file})",
                f"build_2024_baseline_update(model_name={model_name})",
                f"run_gtap_scenario(model_name={model_name}, set_as_default_baseline=true) only after user confirmation",
            ],
        }
    )
    return output


def tool_fetch_observed_data(arguments: dict[str, Any]) -> dict[str, Any]:
    global SYSTEM_PROMPT_CACHE
    args: list[str] = []
    if arguments.get("start_year"):
        args += ["--start-year", str(arguments["start_year"])]
    if arguments.get("end_year"):
        args += ["--end-year", str(arguments["end_year"])]
    if arguments.get("refresh"):
        args.append("--refresh")
    if arguments.get("skip_wits"):
        args.append("--skip-wits")
    if arguments.get("max_wits_calls") is not None:
        args += ["--max-wits-calls", str(arguments["max_wits_calls"])]
    if arguments.get("wits_sleep") is not None:
        args += ["--wits-sleep", str(arguments["wits_sleep"])]
    if arguments.get("wpp_file"):
        wpp_file = safe_project_path(arguments["wpp_file"])
        args += ["--wpp-file", str(wpp_file)]
    if arguments.get("aggregation_mapping"):
        mapping_file = safe_project_path(arguments["aggregation_mapping"])
        args += ["--mapping-file", str(mapping_file)]
    output = run_python_script("04_fetch_observed_calibration_data.py", args=args, timeout=1800)
    if output.get("returncode") == 0:
        SYSTEM_PROMPT_CACHE = None
    return output


def tool_build_2024_baseline_update(arguments: dict[str, Any]) -> dict[str, Any]:
    args: list[str] = []
    if arguments.get("base_year"):
        args += ["--base-year", str(arguments["base_year"])]
    if arguments.get("target_year"):
        args += ["--target-year", str(arguments["target_year"])]
    if arguments.get("include_tariffs"):
        args.append("--include-tariffs")
    if arguments.get("write_all_cmfs"):
        args.append("--write-all-cmfs")
    if arguments.get("model_name"):
        args += ["--model-name", safe_model_name(arguments["model_name"])]
    return run_python_script("05_build_2024_baseline_update.py", args=args)


def tool_modify_gtap_closure(arguments: dict[str, Any]) -> dict[str, Any]:
    baseline_id = str(arguments.get("baseline_id") or "").strip()
    if baseline_id not in {"original_2014", "registered_2024"}:
        raise ValueError("modify_gtap_closure requires baseline_id=original_2014 or registered_2024")
    modifications = arguments.get("modifications") or []
    if not isinstance(modifications, list) or not modifications:
        raise ValueError("modify_gtap_closure requires at least one approved closure modification")
    spec = {
        "scenario_name": arguments.get("scenario_name") or "closure modification",
        "baseline_id": baseline_id,
        "modifications": modifications,
    }
    if arguments.get("model_name"):
        spec["model_name"] = safe_model_name(arguments["model_name"])
    return run_python_script(
        "modify_gtap_closure.py",
        args=["--spec-json", json.dumps(spec, ensure_ascii=False)],
    )


def tool_modify_shock_cmf(arguments: dict[str, Any]) -> dict[str, Any]:
    modifications = arguments.get("modifications") or []
    if not isinstance(modifications, list) or not modifications:
        raise ValueError("modify_shock_cmf requires a non-empty modifications array.")

    baseline_id = str(arguments.get("baseline_id") or "").strip()
    if baseline_id not in {"original_2014", "registered_2024"}:
        raise ValueError("modify_shock_cmf requires baseline_id=original_2014 or registered_2024")

    spec = {
        "scenario_name": arguments.get("scenario_name") or "policy modification",
        "baseline_id": baseline_id,
        "modifications": modifications,
    }
    args = ["--baseline-id", baseline_id, "--spec-json", json.dumps(spec, ensure_ascii=False)]
    if arguments.get("base_cmf"):
        base_cmf = safe_project_path(arguments.get("base_cmf"))
        args += ["--base-cmf", str(base_cmf)]
    if arguments.get("output_cmf"):
        output_cmf = safe_result_path(
            arguments.get("output_cmf"),
            RESULT_DIR / "06_policy_modifications" / "cmf" / "policy_modified.cmf",
        )
        args += ["--output-cmf", str(output_cmf)]
    if arguments.get("model_name"):
        args += ["--model-name", safe_model_name(arguments["model_name"])]
    return run_python_script("06_apply_policy_shock_modifications.py", args=args)


def tool_run_gtap_scenario(arguments: dict[str, Any]) -> dict[str, Any]:
    cmf = safe_project_path(arguments.get("cmf"))
    if cmf is None or not cmf.is_file():
        raise ValueError("run_gtap_scenario requires an existing CMF path; no baseline CMF is selected implicitly")
    cmf_context = extract_cmf_context(cmf.read_text(encoding="utf-8", errors="replace"))
    if not arguments.get("set_as_default_baseline") and cmf_context.get("baseline_id") not in {
        "original_2014",
        "registered_2024",
    }:
        raise ValueError(
            "Policy CMF does not declare baseline_id=original_2014 or registered_2024. "
            "Regenerate it with modify_gtap_closure or modify_shock_cmf."
        )
    if arguments.get("result_dir"):
        result_dir = safe_result_path(arguments.get("result_dir"), RESULT_DIR / "03_run_agent")
    else:
        stamp = time.strftime("%Y%m%d_%H%M%S")
        slug = re.sub(r"[^A-Za-z0-9_-]+", "_", cmf.stem).strip("_")[:50] or "scenario"
        result_dir = RESULT_DIR / f"03_run_{slug}_{stamp}"
    args: list[str] = []
    args += ["--cmf", str(cmf), "--result-dir", str(result_dir)]
    if arguments.get("model_name"):
        args += ["--model-name", safe_model_name(arguments["model_name"])]
    if arguments.get("set_as_default_baseline"):
        args.append("--set-as-default-baseline")
    output = run_python_script("03_run_rungtap_scenario.py", args=args, timeout=1800, result_dir=result_dir)
    if output.get("returncode") == 0:
        output["result_report"] = tool_read_gtap_results(
            {"result_dir": str(result_dir), "view": "default", "top": arguments.get("report_top") or 8}
        ).get("result")
    return output


def tool_read_gtap_results(arguments: dict[str, Any]) -> dict[str, Any]:
    args: list[str] = []
    if arguments.get("result_dir"):
        result_dir = safe_result_path(arguments.get("result_dir"), RESULT_DIR / "03_run_agent")
        args += ["--result-dir", str(result_dir)]
    if arguments.get("view"):
        args += ["--view", str(arguments["view"])]

    for value in arguments.get("variables") or []:
        args += ["--variable", str(value)]
    for value in arguments.get("headers") or []:
        args += ["--header", str(value)]

    for key, option in [
        ("region", "--region"),
        ("sector", "--sector"),
        ("exporter", "--exporter"),
        ("importer", "--importer"),
        ("contains", "--contains"),
        ("max_rows", "--max-rows"),
        ("top", "--top"),
    ]:
        if arguments.get(key) is not None:
            args += [option, str(arguments[key])]

    for key, value in (arguments.get("dimensions") or {}).items():
        args += ["--dimension", f"{key}={value}"]

    if arguments.get("include_baseline"):
        args.append("--include-baseline")
    if arguments.get("sort_by_abs") is False:
        args.append("--no-sort-abs")

    script_name = "07_read_gtap_results.py"
    script_path = SCRIPTS_DIR / script_name
    started = time.time()
    completed = subprocess.run(
        [sys.executable, str(script_path)] + args,
        cwd=PROJECT_DIR,
        text=True,
        capture_output=True,
        timeout=180,
        check=False,
    )

    parsed: dict[str, Any]
    stdout_lines = [line for line in completed.stdout.splitlines() if line.strip()]
    try:
        parsed = json.loads(stdout_lines[-1]) if stdout_lines else {"ok": False, "error": "No JSON output from result reader."}
    except json.JSONDecodeError:
        parsed = {"ok": False, "error": "Could not parse result reader JSON.", "stdout_tail": tail(completed.stdout)}

    returncode = completed.returncode
    if parsed.get("ok") is False:
        returncode = returncode or 1

    return {
        "script": script_name,
        "command": " ".join([sys.executable, str(script_path)] + args),
        "returncode": returncode,
        "elapsed_seconds": round(time.time() - started, 2),
        "stderr_tail": tail(completed.stderr),
        "result": parsed,
        "summaries": read_summary_files(script_name),
    }


TOOL_HANDLERS = {
    "aggregate_gtap_model": tool_aggregate_gtap_model,
    "aggregate_custom_gtap_model": tool_aggregate_custom_gtap_model,
    "fetch_observed_data": tool_fetch_observed_data,
    "build_2024_baseline_update": tool_build_2024_baseline_update,
    "modify_gtap_closure": tool_modify_gtap_closure,
    "run_gtap_scenario": tool_run_gtap_scenario,
    "modify_shock_cmf": tool_modify_shock_cmf,
    "read_gtap_results": tool_read_gtap_results,
}


def openrouter_request(messages: list[dict[str, Any]]) -> dict[str, Any]:
    response = openrouter_client().chat.completions.create(
        model=OPENROUTER_MODEL,
        messages=messages,
        tools=TOOLS,
        tool_choice="auto",
        stream=False,
        extra_body={"reasoning": {"enabled": True}},
    )
    return response.model_dump(mode="json", exclude_none=True)


def iter_openrouter_stream(messages: list[dict[str, Any]]):
    stream = openrouter_client().chat.completions.create(
        model=OPENROUTER_MODEL,
        messages=messages,
        tools=TOOLS,
        tool_choice="auto",
        stream=True,
        extra_body={"reasoning": {"enabled": True}},
    )
    for chunk in stream:
        payload = chunk.model_dump(mode="json", exclude_none=True)
        if payload.get("error"):
            raise RuntimeError(f"OpenRouter streaming error: {json.dumps(payload['error'], ensure_ascii=False)}")
        yield payload


def merge_tool_call_delta(tool_calls: list[dict[str, Any]], delta_calls: list[dict[str, Any]]) -> None:
    for delta in delta_calls:
        index = int(delta.get("index", 0))
        while len(tool_calls) <= index:
            tool_calls.append({"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
        target = tool_calls[index]
        if delta.get("id"):
            target["id"] = delta["id"]
        if delta.get("type"):
            target["type"] = delta["type"]
        function_delta = delta.get("function") or {}
        function = target.setdefault("function", {"name": "", "arguments": ""})
        if function_delta.get("name"):
            function["name"] = function.get("name", "") + function_delta["name"]
        if function_delta.get("arguments"):
            function["arguments"] = function.get("arguments", "") + function_delta["arguments"]


def stream_assistant_message(messages: list[dict[str, Any]]):
    reasoning_details: list[dict[str, Any]] = []
    fallback_reasoning = ""
    content = ""
    tool_calls: list[dict[str, Any]] = []
    for chunk in iter_openrouter_stream(messages):
        choices = chunk.get("choices") or []
        if not choices:
            continue
        delta = choices[0].get("delta") or {}
        detail_chunks = delta.get("reasoning_details") or []
        if isinstance(detail_chunks, list):
            reasoning_details.extend(detail_chunks)
            visible_reasoning = "".join(
                str(detail.get("text") or detail.get("summary") or "")
                for detail in detail_chunks
                if isinstance(detail, dict)
            )
            if visible_reasoning:
                yield {"type": "reasoning_delta", "content": visible_reasoning}
        reasoning_delta = delta.get("reasoning") or delta.get("reasoning_content")
        if reasoning_delta:
            fallback_reasoning += str(reasoning_delta)
            if not detail_chunks:
                yield {"type": "reasoning_delta", "content": str(reasoning_delta)}
        if delta.get("content"):
            content += delta["content"]
            yield {"type": "assistant_delta", "content": delta["content"]}
        if delta.get("tool_calls"):
            merge_tool_call_delta(tool_calls, delta["tool_calls"])

    message: dict[str, Any] = {"role": "assistant", "content": content or None}
    if reasoning_details:
        # OpenRouter requires these blocks to be returned in their original
        # order and without modification on subsequent requests.
        message["reasoning_details"] = reasoning_details
    elif fallback_reasoning:
        message["reasoning"] = fallback_reasoning
    if tool_calls:
        message["tool_calls"] = tool_calls
    yield {"type": "assistant_message", "message": message}


def normalize_assistant_message(message: dict[str, Any]) -> dict[str, Any]:
    tool_calls = message.get("tool_calls") or []
    normalized = {
        "role": "assistant",
        "content": message.get("content"),
    }
    if message.get("reasoning_details"):
        normalized["reasoning_details"] = message["reasoning_details"]
    elif message.get("reasoning") or message.get("reasoning_content"):
        normalized["reasoning"] = message.get("reasoning") or message.get("reasoning_content")
    if tool_calls:
        normalized["tool_calls"] = tool_calls
    return normalized


def execute_tool_call(tool_call: dict[str, Any]) -> tuple[dict[str, Any], str]:
    function = tool_call.get("function", {})
    name = function.get("name")
    raw_args = function.get("arguments") or "{}"
    try:
        arguments = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
    except json.JSONDecodeError:
        arguments = {}
    if name not in TOOL_HANDLERS:
        result = {"ok": False, "error": f"Unknown tool: {name}"}
    else:
        try:
            # RunGTAP currently uses one project-local work directory. Serialize
            # web tool execution so concurrent HTTP requests cannot corrupt it.
            with TOOL_EXECUTION_LOCK:
                output = TOOL_HANDLERS[name](arguments)
            result = {"ok": output.get("returncode", 0) == 0, "tool": name, "arguments": arguments, "output": output}
        except Exception as exc:
            result = {"ok": False, "tool": name, "arguments": arguments, "error": str(exc)}
    return result, json.dumps(result, ensure_ascii=False)


def chat(session_id: str, user_text: str) -> dict[str, Any]:
    messages = start_user_turn(session_id, user_text)

    events: list[dict[str, Any]] = []
    final_content = ""

    for _ in range(MAX_TOOL_ROUNDS):
        response = openrouter_request(messages)
        choice = response["choices"][0]
        assistant_message = normalize_assistant_message(choice["message"])
        tool_calls = assistant_message.get("tool_calls") or []

        if assistant_message.get("content"):
            final_content = assistant_message["content"]
            events.append({"type": "assistant", "content": assistant_message["content"]})

        if not tool_calls:
            messages.append(assistant_message)
            break

        pending_tool_messages: list[dict[str, Any]] = []
        for tool_call in tool_calls:
            function = tool_call.get("function", {})
            name = function.get("name", "unknown")
            arguments = function.get("arguments") or "{}"
            events.append({"type": "tool_start", "name": name, "arguments": arguments})
            result, result_text = execute_tool_call(tool_call)
            events.append({"type": "tool_end", "name": name, "result": result})
            pending_tool_messages.append({"role": "tool", "tool_call_id": tool_call["id"], "content": result_text})
            if not result.get("ok"):
                events.append({"type": "error", "name": name, "content": result.get("error", "Tool failed")})
        messages.append(assistant_message)
        messages.extend(pending_tool_messages)

    if not final_content:
        final_content = "The tool-call limit was reached before the model produced a final response. Review the execution trace or retry with a narrower request."
        messages.append({"role": "assistant", "content": final_content})
        events.append({"type": "assistant", "content": final_content})
    return {"session_id": session_id, "events": events, "final": final_content}


def chat_stream(session_id: str, user_text: str):
    messages = start_user_turn(session_id, user_text)
    yield {"type": "session", "session_id": session_id}

    for _ in range(MAX_TOOL_ROUNDS):
        assistant_message = None
        for event in stream_assistant_message(messages):
            if event["type"] == "assistant_message":
                assistant_message = event["message"]
            else:
                yield event
        if assistant_message is None:
            break

        tool_calls = assistant_message.get("tool_calls") or []
        if not tool_calls:
            messages.append(assistant_message)
            if assistant_message.get("content"):
                yield {"type": "assistant", "content": assistant_message["content"]}
            return

        pending_tool_messages: list[dict[str, Any]] = []
        for tool_call in tool_calls:
            function = tool_call.get("function", {})
            name = function.get("name", "unknown")
            arguments = function.get("arguments") or "{}"
            yield {"type": "tool_start", "name": name, "arguments": arguments}
            result, result_text = execute_tool_call(tool_call)
            yield {"type": "tool_end", "name": name, "result": result}
            pending_tool_messages.append({"role": "tool", "tool_call_id": tool_call["id"], "content": result_text})
            if not result.get("ok"):
                yield {"type": "error", "name": name, "content": result.get("error", "Tool failed")}
        messages.append(assistant_message)
        messages.extend(pending_tool_messages)

    final_content = "The tool-call limit was reached before the model produced a final response. Review the execution trace or retry with a narrower request."
    messages.append({"role": "assistant", "content": final_content})
    yield {"type": "assistant", "content": final_content}
