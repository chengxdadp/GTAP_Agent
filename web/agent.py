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
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


PROJECT_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_DIR / "scripts"
RESULT_DIR = PROJECT_DIR / "result"
AGGREGATION_CONFIG_DIR = PROJECT_DIR / "config" / "aggregations"
KEY_FILE = SCRIPTS_DIR / "key.txt"
STANDARDIZED_DIR = RESULT_DIR / "04_observed_data" / "standardized"

DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-pro")
MAX_TOOL_ROUNDS = 8

SESSIONS: dict[str, list[dict[str, Any]]] = {}
SYSTEM_PROMPT_CACHE: str | None = None
TOOL_EXECUTION_LOCK = threading.Lock()


BASE_SYSTEM_PROMPT = """You are the agent for the GTAP automation workflow. Understand the user's objective, select the appropriate tools, inspect their results, and provide concise, reliable responses in English.

Operating procedure:
- You receive the full conversation history, registered tools, user instructions, tool calls, and tool results.
- When the task requires running project scripts or creating, modifying, or solving a CMF, independently select one or more tools. After each result, decide whether to summarize, continue, correct the arguments, retry, or stop.
- Never invent script results, file paths, or model conclusions. Support important conclusions with returned stdout, stderr, summary files, or result paths.
- If the user asks only to generate a file, do not also run RunGTAP. Call run_gtap_scenario only when the user asks to solve or run the scenario.
- After RunGTAP, if the user asks for interpretation, impacts, comparisons, or details about a region or sector, call read_gtap_results and answer from its structured JSON output.
- If a tool fails, inspect the failure and retry only when the arguments can be corrected. Otherwise identify the failed tool, explain the cause, and provide the relevant log or summary path.

Baseline year and two-stage workflow (important):
- The original GTAP10A database year is 2014. Policy analysis uses 2024 by default, so the database must first be updated and registered as the project's default 2024 baseline. This is a one-time setup step.
- Updating the default baseline to 2024 has two stages. First call build_2024_baseline_update to generate the 2014-to-2024 baseline-update CMF. This is not a policy shock: it updates the database to a 2024 baseline using the standard GTAP closure plus Swap avareg(REG)=qgdp(REG), shocks qgdp(REG), and solves avareg as implicit TFP. Then call run_gtap_scenario with set_as_default_baseline=true. A successful solve registers the solved database as asset/basedata_2024.har and the updated tariff rates as asset/baserate_2024.har for subsequent policy scenarios.
- For policy analysis, including tariffs, population, endowments, or productivity, call modify_shock_cmf with the default base_year=2024. It creates a standard-policy-closure CMF on the project's default 2024 baseline: qgdp is endogenous, GDP responds to the policy, there is no avareg=qgdp swap, and baseline macro shocks are not repeated.
- Critical gate: if modify_shock_cmf reports that the 2024 baseline has not been prepared because asset/basedata_2024.har is missing, do not silently run the baseline workflow. Tell the user: "The project does not yet have a default 2024 baseline. Would you like me to prepare it now?" Only after confirmation may you run build_2024_baseline_update, then run_gtap_scenario(set_as_default_baseline=true), and then return to the policy scenario.
- Use base_year=2014 only when the user explicitly asks to apply a shock to the 2014 database or start from 2014. That path appends policy statements to the 2014-to-2024 baseline-update CMF and reads tariff rates from the 2014 baserate.har.

Policy modifications:
- modify_shock_cmf accepts a modifications array and defaults to base_year=2024. Supported entries are:
  1. Bilateral import tariff: {"type":"bilateral_import_tariff","importer":"China","exporter":"United States","commodity":"soybeans","tariff_percent":20,"rate_mode":"target_rate"}
  2. Regional population: {"type":"regional_population","region":"EastAsia","shock_percent":1.2}
  3. Regional endowment: {"type":"regional_endowment","region":"EastAsia","shock_percent":2.0}
  4. Regional productivity: {"type":"regional_productivity","region":"EastAsia","shock_percent":1.5}
- The default rate_mode is target_rate, which sets the bilateral ad valorem import tariff to the requested target. The tool reads the baseline RTMS rate and converts it to the corresponding percentage change in the GTAP tms tax power. Use rate_change only when the user explicitly requests an increase or decrease in percentage points.
- Bilateral import tariffs are written as tms(commodity, exporter, importer). Regional macro modifications are written as pop(REG), qo(ENDW_COMM,REG), or aoall(PROD_COMM,REG).
- Map countries, regions, and commodities in natural-language requests to the active aggregate regions and sectors shown in the Current GTAP aggregation context below. Do not assume country-level or HS-product-level detail.
- In the final response, state the aggregation actually used, for example China -> EastAsia, United States -> NAMerica, and soybeans -> GrainsCrops.

Custom aggregation:
- aggregate_gtap_model always rebuilds the default 10-by-10 model from the project's default mapping. Do not invent arguments for it.
- When the user explicitly asks to split, merge, or otherwise change regional or sectoral aggregation, call aggregate_custom_gtap_model. In region_groups and sector_groups, list only the original GTAP members that must move; unlisted members keep their default assignments. Prefer original GTAP codes or exact names, such as chn, usa, and osd.
- A custom aggregation creates an independent mapping and model without overwriting the default 10-by-10 model. After the tool returns mapping_file and model_name, pass mapping_file to fetch_observed_data and model_name to build_2024_baseline_update and run_gtap_scenario.
- An existing default 2024 baseline is dimensionally incompatible with a new aggregation. Do not automatically replace the project default baseline. Explain that the observed-data mappings and 2024 baseline must be regenerated, and wait for user confirmation before running with set_as_default_baseline=true.

One-time preparation tools:
- aggregate_gtap_model and fetch_observed_data are environment/data preparation tools, not part of routine policy conversations. Do not call them during normal analysis.
- If a later step fails because the aggregate model or standardized data are missing, tell the user which preparation step is required.

Response style:
- Be concise and write in English.
- Lead with status, then list the key output paths or next action.
- Do not use emoji.
"""


TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "aggregate_gtap_model",
            "description": "Run script 01 to aggregate GTAP10A GTAP-APT 2014 data into the coarse RunGTAP model directory.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "aggregate_custom_gtap_model",
            "description": "Create a validated custom GTAPAgg mapping by moving selected original regions/sectors from the default mapping, then aggregate an independent RunGTAP model. Unspecified members keep their default assignments.",
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
                        "description": "Replace an existing custom mapping/model with the same name. Default false.",
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
            "description": "Run script 05 to build pre-policy baseline-update CMF files, defaulting to the latest observed year/current 2024 baseline, using standard GTAP closure plus GEMPACK swap statements.",
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
            "name": "run_gtap_scenario",
            "description": "Run script 03 to solve a GTAP scenario from a specified CMF file. If omitted, runs the latest 2024 baseline-update CMF.",
            "parameters": {
                "type": "object",
                "properties": {
                    "cmf": {"type": "string", "description": "CMF path, relative to project root or absolute under the workspace."},
                    "model_name": {"type": "string", "description": "RunGTAP model directory name. Default gtap2015_10x10."},
                    "result_dir": {"type": "string", "description": "Output directory, relative to project root or under result."},
                    "set_as_default_baseline": {
                        "type": "boolean",
                        "description": "Set true ONLY when running the 2014->2024 baseline-update CMF. After a successful solve, registers the solved database and updated tariff rates in asset/basedata_2024.har and asset/baserate_2024.har as the project's default baseline. Do not set for policy runs.",
                    },
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "modify_shock_cmf",
            "description": "Apply a structured policy modification spec to an existing CMF and write a new policy CMF without overwriting the source CMF.",
            "parameters": {
                "type": "object",
                "properties": {
                    "base_year": {
                        "type": "integer",
                        "enum": [2014, 2024],
                        "description": "Baseline year the policy is applied on. Default 2024: builds a clean standard-policy-closure CMF on the project's default 2024 baseline database (qgdp endogenous, no avareg swap). Use 2014 ONLY when the user explicitly asks to shock from the 2014 database; that path appends shocks onto the 2014->2024 baseline-update CMF.",
                    },
                    "base_cmf": {
                        "type": "string",
                        "description": "Legacy base_year=2014 only: base CMF path. Defaults to the latest baseline_update_2014_to_YYYY.cmf. Ignored when base_year is 2024.",
                    },
                    "output_cmf": {
                        "type": "string",
                        "description": "Optional output CMF path under result/. If omitted, a timestamped policy CMF is created.",
                    },
                    "scenario_name": {
                        "type": "string",
                        "description": "Short scenario name for audit comments and output naming.",
                    },
                    "model_name": {
                        "type": "string",
                        "description": "Optional RunGTAP model for a 2024 policy. Defaults to the model recorded with the project default baseline.",
                    },
                    "modifications": {
                        "type": "array",
                        "description": "Structured policy modifications to append to the CMF.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "type": {
                                    "type": "string",
                                    "enum": [
                                        "bilateral_import_tariff",
                                        "regional_population",
                                        "regional_endowment",
                                        "regional_productivity",
                                    ],
                                    "description": "Supported simple policy or regional shock modification type.",
                                },
                                "importer": {
                                    "type": "string",
                                    "description": "Importing country/region, e.g. China or EastAsia.",
                                },
                                "exporter": {
                                    "type": "string",
                                    "description": "Exporting country/region, e.g. United States or NAMerica.",
                                },
                                "commodity": {
                                    "type": "string",
                                    "description": "Commodity or aggregate sector, e.g. soybeans or GrainsCrops.",
                                },
                                "tariff_percent": {
                                    "type": "number",
                                    "description": "Tariff value in percent for bilateral_import_tariff.",
                                },
                                "region": {
                                    "type": "string",
                                    "description": "Country or aggregate region for regional_population/regional_endowment/regional_productivity.",
                                },
                                "shock_percent": {
                                    "type": "number",
                                    "description": "Percent shock value for regional_population/regional_endowment/regional_productivity.",
                                },
                                "rate_mode": {
                                    "type": "string",
                                    "enum": ["target_rate", "rate_change", "power_change"],
                                    "description": "target_rate sets the target ad valorem tariff rate after conversion from base RTMS to tms tax-power percent change; rate_change changes the ad valorem rate by the given percentage points; power_change directly shocks the GTAP tax power.",
                                },
                                "note": {
                                    "type": "string",
                                    "description": "Optional analyst note.",
                                },
                            },
                            "required": ["type"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["modifications"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_gtap_results",
            "description": "Read collected RunGTAP/GEMPACK results and return structured JSON summaries or targeted rows from solution/HAR outputs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "result_dir": {
                        "type": "string",
                        "description": "Run result directory, relative to project root or under result/. Defaults to the newest result/03_run* directory.",
                    },
                    "view": {
                        "type": "string",
                        "enum": [
                            "default",
                            "solution",
                            "volume",
                            "updated_data",
                            "base_data",
                            "compare_data",
                            "welfare",
                            "log",
                            "cmf",
                            "files",
                        ],
                        "description": "default gives broad status, pre-run shocks, GDP/EV/trade/sector summaries. solution reads GTAP.sol variables. updated_data/base_data read before/after level-style HAR tables.",
                    },
                    "variables": {
                        "type": "array",
                        "items": {"type": "string"},
                                    "description": "GTAP variable names to read, e.g. qgdp, avareg, EV, DTBAL, tot, qo, qxs, tms.",
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
    region_rows = read_csv_rows(STANDARDIZED_DIR / "gtap_region_country_map.csv")
    sector_rows = read_csv_rows(STANDARDIZED_DIR / "gtap_sector_map.csv")
    if not region_rows or not sector_rows:
        return (
            "Current GTAP aggregation context:\n"
            "- Standardized aggregation mapping CSV files were not found. Run fetch_observed_data or inspect result\\04_observed_data\\standardized.\n"
        )

    region_members: dict[str, list[str]] = {}
    for row in region_rows:
        region = row.get("gtap_region") or ""
        if not region:
            continue
        label = row.get("iso3") or row.get("gtap_code") or row.get("country_name") or ""
        name = row.get("country_name") or ""
        if label and name and label != name:
            label = f"{label}({name})"
        if label:
            region_members.setdefault(region, []).append(label)

    sector_members: dict[str, list[str]] = {}
    for row in sector_rows:
        sector = row.get("gtap_sector_agg") or ""
        if not sector:
            continue
        code = row.get("gtap_sector") or ""
        name = row.get("gtap_sector_name") or ""
        label = f"{code}({name})" if code and name else code or name
        if label:
            sector_members.setdefault(sector, []).append(label)

    lines = [
        "",
        "Current GTAP aggregation context:",
        f"- Model resolution: {len(region_members)} aggregate regions and {len(sector_members)} aggregate sectors. Countries and specific commodities in natural-language requests must be mapped to these aggregates.",
        "- Regional aggregation:",
    ]
    for region in sorted(region_members):
        lines.append(f"  - {region}: {', '.join(region_members[region])}")
    lines.append("- Sectoral aggregation:")
    for sector in sorted(sector_members):
        lines.append(f"  - {sector}: {', '.join(sector_members[sector])}")
    lines.extend(
        [
            "- The modification tool validates mappings again. If mapping fails, tell the user which aggregates are available.",
            "- Policy tools must resolve countries and commodities using the active mapping above. After a custom aggregation, do not continue assuming the default NAMerica, EastAsia, or GrainsCrops aggregates.",
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
    env_key = os.environ.get("DEEPSEEK_API_KEY")
    if env_key:
        return env_key.strip()
    if KEY_FILE.exists():
        return KEY_FILE.read_text(encoding="utf-8").strip()
    raise RuntimeError("Agent API key not found. Set DEEPSEEK_API_KEY or create scripts/key.txt.")


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


def tool_modify_shock_cmf(arguments: dict[str, Any]) -> dict[str, Any]:
    modifications = arguments.get("modifications") or []
    if not isinstance(modifications, list) or not modifications:
        raise ValueError("modify_shock_cmf requires a non-empty modifications array.")

    base_year = int(arguments.get("base_year") or 2024)

    spec = {
        "scenario_name": arguments.get("scenario_name") or "policy modification",
        "modifications": modifications,
    }
    args = ["--base-year", str(base_year), "--spec-json", json.dumps(spec, ensure_ascii=False)]
    if base_year == 2014:
        base_cmf = safe_project_path(arguments.get("base_cmf"), latest_baseline_cmf())
        if base_cmf is None:
            raise ValueError("Base CMF could not be resolved.")
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
    args: list[str] = []
    cmf = safe_project_path(arguments.get("cmf"), latest_baseline_cmf())
    result_dir = safe_result_path(arguments.get("result_dir"), RESULT_DIR / "03_run_baseline_2024")
    args += ["--cmf", str(cmf), "--result-dir", str(result_dir)]
    if arguments.get("model_name"):
        args += ["--model-name", safe_model_name(arguments["model_name"])]
    if arguments.get("set_as_default_baseline"):
        args.append("--set-as-default-baseline")
    return run_python_script("03_run_rungtap_scenario.py", args=args, timeout=1800, result_dir=result_dir)


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
    "run_gtap_scenario": tool_run_gtap_scenario,
    "modify_shock_cmf": tool_modify_shock_cmf,
    "read_gtap_results": tool_read_gtap_results,
}


def latest_baseline_cmf() -> Path:
    baseline_dir = RESULT_DIR / "05_baseline_update" / "cmf"
    candidates = sorted(baseline_dir.glob("baseline_update_2014_to_*.cmf"))
    legacy_dir = RESULT_DIR / "05_observed_shocks" / "cmf"
    if not candidates:
        candidates = sorted(legacy_dir.glob("observed_2014_to_*.cmf"))

    if not candidates:
        return baseline_dir / "baseline_update_2014_to_2024.cmf"
    def year_of(path: Path) -> int:
        match = re.search(r"to_(\d{4})\.cmf$", path.name)
        return int(match.group(1)) if match else 0
    return max(candidates, key=year_of)


def deepseek_request(
    messages: list[dict[str, Any]],
    include_thinking: bool = True,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": DEEPSEEK_MODEL,
        "messages": messages,
        "tools": TOOLS,
        "stream": False,
    }
    payload["tool_choice"] = "auto"
    if include_thinking:
        payload["reasoning_effort"] = "high"
        payload["thinking"] = {"type": "enabled"}

    data = json.dumps(payload, ensure_ascii=True).encode("utf-8")
    request = urllib.request.Request(
        DEEPSEEK_URL,
        data=data,
        headers={
            "Authorization": f"Bearer {load_api_key()}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        if include_thinking and error.code in {400, 422}:
            return deepseek_request(messages, include_thinking=False)
        raise RuntimeError(f"Model service HTTP {error.code}: {body}") from error


def iter_deepseek_stream(messages: list[dict[str, Any]], include_thinking: bool = True):
    payload: dict[str, Any] = {
        "model": DEEPSEEK_MODEL,
        "messages": messages,
        "tools": TOOLS,
        "tool_choice": "auto",
        "stream": True,
    }
    if include_thinking:
        payload["reasoning_effort"] = "high"
        payload["thinking"] = {"type": "enabled"}

    data = json.dumps(payload, ensure_ascii=True).encode("utf-8")
    request = urllib.request.Request(
        DEEPSEEK_URL,
        data=data,
        headers={
            "Authorization": f"Bearer {load_api_key()}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line or not line.startswith("data:"):
                    continue
                payload_text = line[5:].strip()
                if payload_text == "[DONE]":
                    break
                yield json.loads(payload_text)
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        if include_thinking and error.code in {400, 422}:
            yield from iter_deepseek_stream(messages, include_thinking=False)
            return
        raise RuntimeError(f"Model service HTTP {error.code}: {body}") from error


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
    reasoning_content = ""
    content = ""
    tool_calls: list[dict[str, Any]] = []
    for chunk in iter_deepseek_stream(messages):
        choices = chunk.get("choices") or []
        if not choices:
            continue
        delta = choices[0].get("delta") or {}
        if delta.get("reasoning_content"):
            reasoning_content += delta["reasoning_content"]
            yield {"type": "reasoning_delta", "content": delta["reasoning_content"]}
        if delta.get("content"):
            content += delta["content"]
            yield {"type": "assistant_delta", "content": delta["content"]}
        if delta.get("tool_calls"):
            merge_tool_call_delta(tool_calls, delta["tool_calls"])

    message: dict[str, Any] = {"role": "assistant", "content": content or None}
    if tool_calls:
        message["reasoning_content"] = reasoning_content
        message["tool_calls"] = tool_calls
    yield {"type": "assistant_message", "message": message}


def normalize_assistant_message(message: dict[str, Any]) -> dict[str, Any]:
    tool_calls = message.get("tool_calls") or []
    normalized = {
        "role": "assistant",
        "content": message.get("content"),
    }
    if tool_calls:
        # Thinking mode requires reasoning_content to be sent back on
        # subsequent requests for assistant messages that perform tool calls.
        normalized["reasoning_content"] = message.get("reasoning_content") or ""
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
        response = deepseek_request(messages)
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
