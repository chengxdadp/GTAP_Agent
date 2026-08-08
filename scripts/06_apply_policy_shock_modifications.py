from __future__ import annotations

import argparse
import csv
import json
import math
import re
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from gtap_observed_data import PROJECT_DIR, RESULT_DIR, STANDARDIZED_DIR, read_csv
from gtap_runtime import RUNGTAP_DIR, find_executable
from gtap_scenario import (
    GTAP_CHECK_ON_READ_LINES,
    build_scenario_cmf,
    extract_cmf_context,
    load_aggregation_aliases,
    resolve_aggregate,
    resolve_baseline_context,
)


POLICY_DIR = RESULT_DIR / "06_policy_modifications"
POLICY_CMF_DIR = POLICY_DIR / "cmf"
SUMMARY_PATH = POLICY_DIR / "policy_modification_summary.txt"

ASSET_DIR = PROJECT_DIR / "asset"
BASELINE_2024_DATA = ASSET_DIR / "basedata_2024.har"
BASELINE_2024_RATES = ASSET_DIR / "baserate_2024.har"
DEFAULT_BASELINE_METADATA = ASSET_DIR / "default_baseline.json"

MODEL_NAME = "gtap2015_10x10"
MODEL_DIR = RUNGTAP_DIR / MODEL_NAME

POLICY_CLOSURE_EXOGENOUS_LINES = [
    "          pop",
    "          psaveslack pfactwld",
    "          profitslack incomeslack endwslack",
    "          cgdslack tradslack",
    "          ams atm atf ats atd",
    "          aosec aoreg avasec avareg",
    "          afcom afsec afreg afecom afesec afereg",
    "          aoall afall afeall",
    "          au dppriv dpgov dpsave",
    "          to tp tm tms tx txs",
    "          qo(ENDW_COMM,REG)",
]


def parser() -> argparse.ArgumentParser:
    argument_parser = argparse.ArgumentParser(
        description="Apply structured, conversational policy modifications to an existing GTAP CMF."
    )
    argument_parser.add_argument(
        "--baseline-id",
        choices=["original_2014", "registered_2024"],
        help="Explicit scenario input baseline. Required unless deprecated --base-year is supplied.",
    )
    argument_parser.add_argument(
        "--base-cmf",
        type=Path,
        default=None,
        help="Optional CMF returned by modify_gtap_closure.py or an earlier shock modification. Its embedded baseline must match --baseline-id.",
    )
    argument_parser.add_argument(
        "--base-year",
        type=int,
        choices=[2014, 2024],
        default=None,
        help=argparse.SUPPRESS,
    )
    argument_parser.add_argument(
        "--model-name",
        default=None,
        help="RunGTAP model. registered_2024 must use the model recorded in asset/default_baseline.json.",
    )
    argument_parser.add_argument(
        "--output-cmf",
        type=Path,
        default=None,
        help="Destination CMF under result/. Defaults to a timestamped policy CMF.",
    )
    argument_parser.add_argument(
        "--spec-json",
        required=True,
        help="JSON object or array containing policy modifications.",
    )
    return argument_parser


def normalize_key(value: object) -> str:
    text = str(value or "").strip().lower()
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", text)


def slugify(value: str | None, default: str = "policy") -> str:
    text = str(value or "").strip().lower()
    slug = re.sub(r"[^a-z0-9_-]+", "_", text).strip("_")
    return (slug or default)[:60]


def parse_number(value: object, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric: {value!r}") from exc
    if not math.isfinite(number):
        raise ValueError(f"{field} must be finite: {value!r}")
    return number


def ensure_inside(path: Path, parent: Path, label: str) -> Path:
    resolved = path.resolve()
    root = parent.resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"{label} must stay inside {root}: {path}")
    return resolved


def resolve_project_path(path: Path) -> Path:
    resolved = path if path.is_absolute() else PROJECT_DIR / path
    return ensure_inside(resolved, PROJECT_DIR, "base CMF")


def resolve_result_path(path: Path) -> Path:
    if path.is_absolute():
        resolved = path
    elif path.parts and path.parts[0].lower() == "result":
        resolved = PROJECT_DIR / path
    else:
        resolved = RESULT_DIR / path
    return ensure_inside(resolved, RESULT_DIR, "output CMF")


def latest_baseline_cmf() -> Path:
    baseline_dir = RESULT_DIR / "05_baseline_update" / "cmf"
    candidates = sorted(baseline_dir.glob("baseline_update_2014_to_*.cmf"))

    legacy_dir = RESULT_DIR / "05_observed_shocks" / "cmf"
    if not candidates:
        candidates = sorted(legacy_dir.glob("observed_2014_to_*.cmf"))

    def year_of(path: Path) -> int:
        match = re.search(r"to_(\d{4})\.cmf$", path.name)
        return int(match.group(1)) if match else 0

    if candidates:
        return max(candidates, key=year_of)
    return baseline_dir / "baseline_update_2014_to_2024.cmf"


def load_policy_spec(raw_json: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid policy JSON: {exc}") from exc
    if isinstance(payload, list):
        return {"modifications": payload}
    if isinstance(payload, dict):
        if "modifications" not in payload and "type" in payload:
            return {"modifications": [payload]}
        return payload
    raise ValueError("Policy spec must be a JSON object or array.")


def add_alias(aliases: dict[str, str], alias: object, target: str) -> None:
    key = normalize_key(alias)
    if key:
        aliases[key] = target


def default_baseline_metadata() -> dict[str, Any]:
    if not DEFAULT_BASELINE_METADATA.is_file():
        return {}
    try:
        payload = json.loads(DEFAULT_BASELINE_METADATA.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid default baseline metadata: {DEFAULT_BASELINE_METADATA}") from exc
    return payload if isinstance(payload, dict) else {}


def load_region_aliases() -> tuple[dict[str, str], set[str]]:
    rows = read_csv(STANDARDIZED_DIR / "gtap_region_country_map.csv")
    regions = sorted({row["gtap_region"] for row in rows if row.get("gtap_region")})
    aliases: dict[str, str] = {}
    for region in regions:
        add_alias(aliases, region, region)
    for row in rows:
        region = row.get("gtap_region")
        if not region:
            continue
        for field in ["gtap_code", "iso3", "country_name"]:
            add_alias(aliases, row.get(field), region)

    china_target = aliases.get(normalize_key("chn"), "EastAsia")
    usa_target = aliases.get(normalize_key("usa"), "NAmerica")
    manual_aliases = {
        "china": china_target,
        "chn": china_target,
        "prc": china_target,
        "usa": usa_target,
        "us": usa_target,
        "u.s.": usa_target,
        "united states": usa_target,
        "united states of america": usa_target,
        "america": usa_target,
        "north america": usa_target,
        "eu": "EU_28",
        "european union": "EU_28",
        "east asia": "EastAsia",
    }
    for alias, region in manual_aliases.items():
        if region in regions and normalize_key(alias) not in aliases:
            add_alias(aliases, alias, region)
    return aliases, set(regions)


def load_sector_aliases() -> tuple[dict[str, str], set[str]]:
    rows = read_csv(STANDARDIZED_DIR / "gtap_sector_map.csv")
    sectors = sorted({row["gtap_sector_agg"] for row in rows if row.get("gtap_sector_agg")})
    aliases: dict[str, str] = {}
    for sector in sectors:
        add_alias(aliases, sector, sector)
    for row in rows:
        target = row.get("gtap_sector_agg")
        if not target:
            continue
        for field in ["gtap_sector", "gtap_sector_name"]:
            add_alias(aliases, row.get(field), target)

    soy_target = aliases.get(normalize_key("osd"), "GrainsCrops")
    wheat_target = aliases.get(normalize_key("wht"), "GrainsCrops")
    rice_target = aliases.get(normalize_key("pdr"), "GrainsCrops")
    produce_target = aliases.get(normalize_key("v_f"), "GrainsCrops")
    manual_aliases = {
        "soy": soy_target,
        "soybean": soy_target,
        "soybeans": soy_target,
        "soy bean": soy_target,
        "soy beans": soy_target,
        "oil seed": soy_target,
        "oil seeds": soy_target,
        "oilseeds": soy_target,
        "oilsds": soy_target,
        "osd": soy_target,
    }
    for alias, sector in manual_aliases.items():
        if sector in sectors and normalize_key(alias) not in aliases:
            add_alias(aliases, alias, sector)
    return aliases, set(sectors)


def resolve_alias(value: object, aliases: dict[str, str], available: set[str], label: str) -> tuple[str, str]:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError(f"{label} is required.")
    key = normalize_key(raw)
    if key in aliases:
        return aliases[key], raw
    for item in available:
        if normalize_key(item) == key:
            return item, raw
    choices = ", ".join(sorted(available))
    raise ValueError(f"Could not map {label} {raw!r}. Available aggregate values: {choices}")


def model_dir_from_cmf(cmf_path: Path) -> Path | None:
    text = cmf_path.read_text(encoding="utf-8", errors="replace")
    patterns = [
        r"(?im)^\s*!\s*Model directory:\s*(.+?)\s*$",
        r"(?im)^\s*file\s+gtapSETS\s*=\s*(.+?)[\\/]+sets\.har\s*;",
        r"(?im)^\s*file\s+gtapDATA\s*=\s*(.+?)[\\/]+basedata\.har\s*;",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return Path(match.group(1).strip())
    return None


def disambiguate_columns(columns: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    output: list[str] = []
    for column in columns:
        clean = column.strip() or "index"
        seen[clean] = seen.get(clean, 0) + 1
        output.append(clean if seen[clean] == 1 else f"{clean}_{seen[clean]}")
    return output


def rate_lookup_key(sector: str, exporter_region: str, importer_region: str) -> tuple[str, str, str]:
    return (normalize_key(sector), normalize_key(exporter_region), normalize_key(importer_region))


def load_rtms_rates(baserate: Path) -> dict[tuple[str, str, str], float]:
    if not baserate.exists():
        raise FileNotFoundError(f"Could not find tariff rate HAR for target tariff conversion: {baserate}")

    har2csv = find_executable("har2csv.exe") or find_executable("har2csv")
    if not har2csv:
        raise FileNotFoundError("har2csv executable is required to convert target tariff rates from baserate.har.")

    with tempfile.TemporaryDirectory(prefix="gtap_rtms_") as tmp_dir:
        out_path = Path(tmp_dir) / "rtms.csv"
        completed = subprocess.run(
            [har2csv, str(baserate), str(out_path), "RTMS", "/H"],
            cwd=PROJECT_DIR,
            text=True,
            capture_output=True,
            check=False,
            timeout=120,
        )
        if completed.returncode != 0 or not out_path.exists():
            detail = completed.stderr or completed.stdout or "har2csv failed"
            raise RuntimeError(f"Could not read RTMS from {baserate}: {detail.strip()}")

        rates: dict[tuple[str, str, str], float] = {}
        with out_path.open(newline="", encoding="utf-8", errors="replace") as handle:
            reader = csv.reader(handle)
            columns: list[str] = []
            for row in reader:
                if not row:
                    continue
                if row[0].strip().lower() == "header":
                    columns = disambiguate_columns(row)
                    continue
                if not columns:
                    continue
                values = row + [""] * (len(columns) - len(row))
                record = dict(zip(columns, values))
                if record.get("Header", "").strip() != "RTMS":
                    continue
                try:
                    rates[
                        rate_lookup_key(record["TRAD_COMM"], record["REG"], record["REG_2"])
                    ] = float(record["Value"])
                except (KeyError, ValueError):
                    continue
    return rates


def tariff_power_change(base_rate: float, requested_rate: float, rate_mode: str) -> tuple[float, float]:
    if base_rate <= -100:
        raise ValueError(f"Cannot convert tariff rate because the base ad valorem rate is <= -100%: {base_rate}")
    if rate_mode == "target_rate":
        target_rate = requested_rate
    elif rate_mode == "rate_change":
        target_rate = base_rate + requested_rate
    elif rate_mode == "power_change":
        return requested_rate, math.nan
    else:
        raise ValueError(f"Unsupported rate_mode: {rate_mode}")

    base_power = 1.0 + base_rate / 100.0
    target_power = 1.0 + target_rate / 100.0
    power_change = (target_power / base_power - 1.0) * 100.0
    return power_change, target_rate


def normalize_rate_mode(value: object) -> str:
    key = normalize_key(value or "target_rate")
    target_rate = {"target", "targetrate", "targetpercent", "targetratepercent", "setto", "adv", "advalorem"}
    rate_change = {"change", "ratechange", "ratechangepct", "ratepercentchange", "increase"}
    power_change = {"power", "powerchange", "direct", "percentchangeinpower"}
    if key in target_rate:
        return "target_rate"
    if key in rate_change:
        return "rate_change"
    if key in power_change:
        return "power_change"
    if key in {"target_rate", "rate_change", "power_change"}:
        return key
    raise ValueError(f"Unsupported rate_mode: {value!r}")


def tariff_shock_line(sector: str, exporter_region: str, importer_region: str, power_change: float) -> str:
    lhs = f'Shock tms("{sector}","{exporter_region}","{importer_region}")'
    return f"{lhs} = {power_change:.6f};"


def regional_shock_line(shock_type: str, region: str, value: float) -> str:
    if shock_type == "regional_population":
        return f'Shock pop("{region}") = {value:.6f};'
    if shock_type == "regional_endowment":
        return f'Shock qo(ENDW_COMM,"{region}") = uniform {value:.6f};'
    if shock_type == "regional_productivity":
        return f'Shock aoall(PROD_COMM,"{region}") = uniform {value:.6f};'
    raise ValueError(f"Unsupported regional shock type: {shock_type}")


def shock_pattern(item: dict[str, Any]) -> str:
    def quoted(text: str) -> str:
        return re.escape(text)

    if item["type"] == "bilateral_import_tariff":
        return (
            r'(?im)^\s*Shock\s+tms\(\s*'
            rf'"{quoted(item["gtap_sector"])}"\s*,\s*'
            rf'"{quoted(item["exporter_region"])}"\s*,\s*'
            rf'"{quoted(item["importer_region"])}"\s*'
            r'\)\s*=.*?;\s*$'
        )
    if item["type"] == "regional_population":
        return rf'(?im)^\s*Shock\s+pop\(\s*"{quoted(item["region"])}"\s*\)\s*=.*?;\s*$'
    if item["type"] == "regional_endowment":
        return (
            r'(?im)^\s*Shock\s+qo\(\s*ENDW_COMM\s*,\s*'
            rf'"{quoted(item["region"])}"\s*\)\s*=.*?;\s*$'
        )
    if item["type"] == "regional_productivity":
        return (
            r'(?im)^\s*Shock\s+aoall\(\s*PROD_COMM\s*,\s*'
            rf'"{quoted(item["region"])}"\s*\)\s*=.*?;\s*$'
        )
    raise ValueError(f"Unsupported modification type: {item['type']}")


def resolve_modification(
    modification: dict[str, Any],
    region_aliases: dict[str, str],
    regions: set[str],
    sector_aliases: dict[str, str],
    sectors: set[str],
    rtms_rates: dict[tuple[str, str, str], float],
) -> dict[str, Any]:
    mod_type = normalize_key(modification.get("type") or modification.get("kind") or "bilateral_import_tariff")
    if mod_type in {"bilateralimporttariff", "importtariff", "tariff"}:
        importer_value = modification.get("importer_region") or modification.get("importer") or modification.get("destination")
        exporter_value = modification.get("exporter_region") or modification.get("exporter") or modification.get("source")
        commodity_value = modification.get("gtap_sector") or modification.get("sector") or modification.get("commodity")
        importer_region, importer_input = resolve_alias(importer_value, region_aliases, regions, "importer")
        exporter_region, exporter_input = resolve_alias(exporter_value, region_aliases, regions, "exporter")
        sector, commodity_input = resolve_alias(commodity_value, sector_aliases, sectors, "commodity")
        rate = parse_number(
            modification.get("tariff_percent", modification.get("rate_percent", modification.get("value"))),
            "tariff_percent",
        )
        rate_mode = normalize_rate_mode(modification.get("rate_mode") or modification.get("mode") or "target_rate")
        base_rate = math.nan
        target_rate = math.nan
        if rate_mode == "power_change":
            power_change = rate
        else:
            key = rate_lookup_key(sector, exporter_region, importer_region)
            if key not in rtms_rates:
                raise ValueError(
                    "Could not find base RTMS tariff rate for "
                    f"commodity={sector}, exporter={exporter_region}, importer={importer_region}."
                )
            base_rate = rtms_rates[key]
            power_change, target_rate = tariff_power_change(base_rate, rate, rate_mode)
        shock_line = tariff_shock_line(sector, exporter_region, importer_region, power_change)
        item = {
            "type": "bilateral_import_tariff",
            "importer_input": importer_input,
            "exporter_input": exporter_input,
            "commodity_input": commodity_input,
            "importer_region": importer_region,
            "exporter_region": exporter_region,
            "gtap_sector": sector,
            "tariff_percent": rate,
            "rate_mode": rate_mode,
            "base_tariff_percent": "" if math.isnan(base_rate) else base_rate,
            "target_tariff_percent": "" if math.isnan(target_rate) else target_rate,
            "tms_power_change_percent": power_change,
            "shock_percent": power_change,
            "shock_line": shock_line,
            "note": modification.get("note") or "",
        }
        item["match_pattern"] = shock_pattern(item)
        return item

    regional_types = {
        "regionalpopulation": "regional_population",
        "population": "regional_population",
        "pop": "regional_population",
        "regionalendowment": "regional_endowment",
        "endowment": "regional_endowment",
        "qo": "regional_endowment",
        "regionalproductivity": "regional_productivity",
        "productivity": "regional_productivity",
        "aoall": "regional_productivity",
    }
    if mod_type not in regional_types:
        raise ValueError(f"Unsupported modification type: {modification.get('type')!r}")

    shock_type = regional_types[mod_type]
    region_value = modification.get("region") or modification.get("gtap_region") or modification.get("target_region")
    region, region_input = resolve_alias(region_value, region_aliases, regions, "region")
    value = parse_number(
        modification.get("shock_percent", modification.get("percent", modification.get("value"))),
        "shock_percent",
    )
    shock_line = regional_shock_line(shock_type, region, value)
    item = {
        "type": shock_type,
        "region_input": region_input,
        "region": region,
        "shock_percent": value,
        "tariff_percent": "",
        "rate_mode": "",
        "shock_line": shock_line,
        "note": modification.get("note") or "",
    }
    item["match_pattern"] = shock_pattern(item)
    return item


def build_policy_cmf_2024(scenario_name: str, resolved: list[dict[str, Any]], model_name: str = MODEL_NAME) -> str:
    model_dir = RUNGTAP_DIR / model_name
    exogenous_lines = POLICY_CLOSURE_EXOGENOUS_LINES.copy()
    exogenous_lines[-1] = f"{exogenous_lines[-1]} ;"
    lines = [
        f"! GTAP policy scenario on the 2024 baseline: {scenario_name}",
        "! Base database: project default 2024 baseline (asset/basedata_2024.har), produced by the 2014->2024 baseline update run.",
        "! Closure: standard multiregion GE policy closure. qgdp is endogenous so GDP responds to the policy; no avareg=qgdp swap and no baseline macro shocks.",
        f"! Model directory: {model_dir}",
        *GTAP_CHECK_ON_READ_LINES,
        f"aux files = {RUNGTAP_DIR}\\GTAP;",
        f"file gtapSETS = {model_dir}\\sets.har;",
        f"file gtapDATA = {BASELINE_2024_DATA};",
        "Updated file gtapDATA = gdata.upd;",
        f"Solution file = {RUNGTAP_DIR}\\work\\GTAP;",
        f"file gtapPARM = {model_dir}\\default.prm;",
        "Verbal Description =",
        f"Policy scenario on 2024 baseline: {scenario_name};",
        "Method = Gragg;",
        "Steps = 4 8 12;",
        "automatic accuracy = no;",
        "subintervals = 10;",
        "exogenous",
        *exogenous_lines,
        "Rest Endogenous ;",
        "",
    ]
    for index, item in enumerate(resolved, start=1):
        lines.append(f"! Modification {index}: {item['type']}")
        lines.append(f"! Mapping: {mapping_note(item)}")
        lines.append(value_note(item))
        if item.get("note"):
            lines.append(f"! Note: {item['note']}")
        lines.append(item["shock_line"])
        lines.append("")
    return "\n".join(lines) + "\n"


def build_policy_block(scenario_name: str, base_cmf: Path, resolved: list[dict[str, Any]]) -> str:
    lines = [
        "! Policy modifications appended by scripts/06_apply_policy_shock_modifications.py",
        f"! Scenario: {scenario_name}",
        f"! Generated: {datetime.now().isoformat(timespec='seconds')}",
        f"! Base CMF: {base_cmf}",
    ]
    for index, item in enumerate(resolved, start=1):
        lines.extend(
            [
                f"! Modification {index}: {item['type']}",
                f"! Mapping: {mapping_note(item)}",
                value_note(item),
                f"! Existing matching shock lines replaced: {item.get('replacement_count', 0)}",
                (
                    f"! Replacement line already applied above: {item['shock_line']}"
                    if item.get("replaced_existing")
                    else item["shock_line"]
                ),
            ]
        )
        if item.get("note"):
            lines.insert(-1, f"! Note: {item['note']}")
    return "\n".join(lines)


def mapping_note(item: dict[str, Any]) -> str:
    if item["type"] == "bilateral_import_tariff":
        return (
            f"importer {item['importer_input']} -> {item['importer_region']}; "
            f"exporter {item['exporter_input']} -> {item['exporter_region']}; "
            f"commodity {item['commodity_input']} -> {item['gtap_sector']}"
        )
    return f"region {item['region_input']} -> {item['region']}"


def value_note(item: dict[str, Any]) -> str:
    if item["type"] == "bilateral_import_tariff":
        if item["rate_mode"] == "power_change":
            return f"! Rate mode: power_change; tms_power_change_percent: {item['tms_power_change_percent']:.6f}"
        return (
            f"! Rate mode: {item['rate_mode']}; requested_tariff_percent: {item['tariff_percent']:.6f}; "
            f"base_tariff_percent: {item['base_tariff_percent']:.6f}; "
            f"target_tariff_percent: {item['target_tariff_percent']:.6f}; "
            f"tms_power_change_percent: {item['tms_power_change_percent']:.6f}"
        )
    return f"! Shock percent: {item['shock_percent']:.6f}"


def apply_replacements(base_text: str, resolved: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    updated_text = base_text
    annotated: list[dict[str, Any]] = []
    for item in resolved:
        matches = list(re.finditer(item["match_pattern"], updated_text))
        item = dict(item)
        item["replaced_existing"] = bool(matches)
        item["replacement_count"] = len(matches)
        if matches:
            updated_text = re.sub(item["match_pattern"], item["shock_line"], updated_text)
        annotated.append(item)
    return updated_text, annotated


def write_resolved_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = [
        "type",
        "code",
        "description",
        "input_dimensions",
        "dimensions",
        "requested_value",
        "applied_value",
        "value_mode",
        "importer_input",
        "exporter_input",
        "commodity_input",
        "importer_region",
        "exporter_region",
        "gtap_sector",
        "tariff_percent",
        "base_tariff_percent",
        "target_tariff_percent",
        "tms_power_change_percent",
        "region_input",
        "region",
        "shock_percent",
        "rate_mode",
        "shock_line",
        "replaced_existing",
        "replacement_count",
        "note",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def public_item(item: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in item.items() if key != "match_pattern"}


SHOCK_VARIABLES: dict[str, dict[str, Any]] = {
    "tms": {"dimensions": ["commodity", "exporter", "importer"], "description": "bilateral import-tax power"},
    "tm": {"dimensions": ["commodity", "importer"], "description": "source-generic import-tax power"},
    "txs": {"dimensions": ["commodity", "exporter", "importer"], "description": "bilateral export-tax/subsidy power"},
    "tx": {"dimensions": ["commodity", "exporter"], "description": "destination-generic export-tax/subsidy power"},
    "to": {"dimensions": ["commodity", "region"], "description": "commodity output/income-tax power"},
    "tp": {"dimensions": ["region"], "description": "uniform private-consumption-tax shift"},
    "pop": {"dimensions": ["region"], "description": "regional population"},
    "qo": {"dimensions": ["factor", "region"], "description": "regional factor-endowment supply"},
    "aosec": {"dimensions": ["sector"], "description": "worldwide sector output-augmenting technology"},
    "aoreg": {"dimensions": ["region"], "description": "regional output-augmenting technology"},
    "aoall": {"dimensions": ["sector", "region"], "description": "sector-region output-augmenting technology"},
    "avasec": {"dimensions": ["sector"], "description": "worldwide sector value-added technology"},
    "avareg": {"dimensions": ["region"], "description": "regional value-added technology"},
    "afcom": {"dimensions": ["commodity"], "description": "worldwide intermediate-input technology"},
    "afsec": {"dimensions": ["sector"], "description": "sector-wide intermediate-input technology"},
    "afreg": {"dimensions": ["region"], "description": "regional intermediate-input technology"},
    "afall": {"dimensions": ["commodity", "sector", "region"], "description": "input-sector-region intermediate technology"},
    "afecom": {"dimensions": ["factor"], "description": "worldwide factor-augmenting technology"},
    "afesec": {"dimensions": ["sector"], "description": "sector-wide factor-augmenting technology"},
    "afereg": {"dimensions": ["region"], "description": "regional factor-augmenting technology"},
    "afeall": {"dimensions": ["factor", "sector", "region"], "description": "factor-sector-region augmenting technology"},
    "ams": {"dimensions": ["commodity", "exporter", "importer"], "description": "bilateral import-augmenting technology"},
    "atf": {"dimensions": ["commodity"], "description": "commodity-specific international-shipping technology"},
    "ats": {"dimensions": ["exporter"], "description": "origin-specific international-shipping technology"},
    "atd": {"dimensions": ["importer"], "description": "destination-specific international-shipping technology"},
    "qgdp": {"dimensions": ["region"], "description": "regional real GDP target; requires gdp_target_tfp closure swap"},
    "qcgds": {"dimensions": ["region"], "description": "regional gross-investment quantity; requires fixed_regional_investment closure swap"},
}

SEMANTIC_SHOCK_CODES = {
    "bilateralimporttariff": "tms",
    "sourcegenericimporttariff": "tm",
    "bilateralexporttax": "txs",
    "destinationgenericexporttax": "tx",
    "outputtax": "to",
    "uniformprivateconsumptiontax": "tp",
    "regionalpopulation": "pop",
    "factorendowment": "qo",
    "regionalendowment": "qo",
    "outputtechnology": "aoall",
    "regionalproductivity": "aoall",
    "importaugmentingtechnology": "ams",
    "intermediateinputtechnology": "afall",
    "factoraugmentingtechnology": "afeall",
    "gtapvariable": "",
}


def explicit_shock_pattern(code: str, rendered_arguments: list[str]) -> str:
    arguments = r"\s*,\s*".join(re.escape(value) for value in rendered_arguments)
    return rf"(?im)^\s*Shock\s+{re.escape(code)}\(\s*{arguments}\s*\)\s*=.*?;\s*$"


def _dimension_value(
    dimension: str,
    modification: dict[str, Any],
    aliases: dict[str, tuple[dict[str, str], set[str]]],
) -> tuple[str, str]:
    field_aliases = {
        "commodity": ["commodity", "input", "gtap_sector"],
        "sector": ["sector", "industry", "gtap_sector"],
        "factor": ["factor", "endowment"],
        "region": ["region", "gtap_region"],
        "exporter": ["exporter", "source"],
        "importer": ["importer", "destination"],
    }
    raw = next((modification.get(field) for field in field_aliases[dimension] if modification.get(field) is not None), None)
    alias_dimension = "region" if dimension in {"region", "exporter", "importer"} else "sector" if dimension in {"commodity", "sector"} else "factor"
    return resolve_aggregate(raw, *aliases[alias_dimension], dimension)


def resolve_explicit_modification(
    modification: dict[str, Any],
    aliases: dict[str, tuple[dict[str, str], set[str]]],
    rtms_rates: dict[tuple[str, str, str], float],
    closure_swaps: set[str],
) -> dict[str, Any]:
    raw_type = normalize_key(modification.get("type") or "gtap_variable")
    code = str(modification.get("code") or modification.get("variable") or SEMANTIC_SHOCK_CODES.get(raw_type) or "").lower()
    if code not in SHOCK_VARIABLES:
        raise ValueError(f"Unsupported GTAP shock code {code!r}. Allowed: {', '.join(SHOCK_VARIABLES)}")

    spec = SHOCK_VARIABLES[code]
    resolved_dimensions: dict[str, str] = {}
    input_dimensions: dict[str, str] = {}
    uniform = False
    for dimension in spec["dimensions"]:
        if raw_type == "regionalendowment" and dimension == "factor":
            resolved, raw = "ENDW_COMM", "all endowments"
            uniform = True
        elif raw_type == "regionalproductivity" and dimension == "sector":
            resolved, raw = "PROD_COMM", "all production sectors"
            uniform = True
        else:
            resolved, raw = _dimension_value(dimension, modification, aliases)
        resolved_dimensions[dimension] = resolved
        input_dimensions[dimension] = raw

    raw_value = modification.get("value")
    if raw_value is None:
        raw_value = modification.get("tariff_percent") if code == "tms" else modification.get("shock_percent")
    value = parse_number(raw_value, "value")
    value_mode = str(modification.get("value_mode") or modification.get("rate_mode") or "percent_change").strip().lower()
    base_rate = math.nan
    target_rate = math.nan
    applied_value = value
    if code == "tms":
        aliases_for_mode = {"percent_change": "power_change", "direct": "power_change"}
        tariff_mode = normalize_rate_mode(aliases_for_mode.get(value_mode, value_mode))
        if tariff_mode != "power_change":
            key = rate_lookup_key(
                resolved_dimensions["commodity"],
                resolved_dimensions["exporter"],
                resolved_dimensions["importer"],
            )
            if key not in rtms_rates:
                raise ValueError(f"No baseline RTMS rate for {resolved_dimensions}")
            base_rate = rtms_rates[key]
            applied_value, target_rate = tariff_power_change(base_rate, value, tariff_mode)
        value_mode = tariff_mode
    elif value_mode not in {"percent_change", "power_change"}:
        raise ValueError(f"{code} supports value_mode=percent_change only; got {value_mode!r}")

    arguments: list[str] = []
    rendered_arguments: list[str] = []
    for dimension in spec["dimensions"]:
        value_for_dimension = resolved_dimensions[dimension]
        arguments.append(value_for_dimension)
        rendered_arguments.append(value_for_dimension if value_for_dimension in {"ENDW_COMM", "PROD_COMM"} else f'"{value_for_dimension}"')

    if code == "qgdp":
        expected = f'Swap avareg("{resolved_dimensions["region"]}") = qgdp("{resolved_dimensions["region"]}");'
        if expected not in closure_swaps:
            raise ValueError(f"qgdp is endogenous for {resolved_dimensions['region']}; apply gdp_target_tfp in modify_gtap_closure first")
    if code == "avareg":
        swapped = f'Swap avareg("{resolved_dimensions["region"]}") = qgdp("{resolved_dimensions["region"]}");'
        if swapped in closure_swaps:
            raise ValueError(
                f"avareg is endogenous for {resolved_dimensions['region']} after gdp_target_tfp; shock qgdp or remove that closure patch"
            )
    if code == "qcgds":
        expected = f'Swap cgdslack("{resolved_dimensions["region"]}") = qcgds("{resolved_dimensions["region"]}");'
        if expected not in closure_swaps:
            raise ValueError(
                f"qcgds is endogenous for {resolved_dimensions['region']}; apply fixed_regional_investment in modify_gtap_closure first"
            )

    uniform_text = "uniform " if uniform else ""
    shock_line = f"Shock {code}({','.join(rendered_arguments)}) = {uniform_text}{applied_value:.6f};"
    return {
        "type": modification.get("type") or "gtap_variable",
        "code": code,
        "description": spec["description"],
        "input_dimensions": input_dimensions,
        "dimensions": resolved_dimensions,
        "requested_value": value,
        "applied_value": applied_value,
        "value_mode": value_mode,
        "base_tariff_percent": None if math.isnan(base_rate) else base_rate,
        "target_tariff_percent": None if math.isnan(target_rate) else target_rate,
        "shock_line": shock_line,
        "note": modification.get("note") or "",
        "match_pattern": explicit_shock_pattern(code, rendered_arguments),
    }


def replace_context_line(text: str, context: dict[str, Any]) -> str:
    line = "! GTAP_AGENT_CONTEXT " + json.dumps(context, ensure_ascii=True, separators=(",", ":"))
    if re.search(r"(?m)^! GTAP_AGENT_CONTEXT .*$", text):
        return re.sub(r"(?m)^! GTAP_AGENT_CONTEXT .*$", lambda _: line, text)
    return line + "\n" + text


def explicit_policy_block(scenario_name: str, source_cmf: Path | None, resolved: list[dict[str, Any]]) -> str:
    lines = [
        "! Structured shock modifications generated by scripts/06_apply_policy_shock_modifications.py",
        f"! Scenario: {scenario_name}",
        f"! Source CMF: {source_cmf if source_cmf else 'standard policy closure generated for this baseline'}",
    ]
    for index, item in enumerate(resolved, start=1):
        lines.extend(
            [
                f"! Shock {index}: {item['code']} — {item['description']}",
                f"! Dimensions: {json.dumps(item['dimensions'], ensure_ascii=False)}",
                f"! Requested value: {item['requested_value']}; mode: {item['value_mode']}",
            ]
        )
        if item.get("note"):
            lines.append(f"! Note: {item['note']}")
        if item.get("replaced_existing"):
            lines.append(f"! Replaced existing matching shock: {item['shock_line']}")
        else:
            lines.append(item["shock_line"])
        lines.append("")
    return "\n".join(lines).rstrip()


def run_explicit_policy(args: argparse.Namespace, spec: dict[str, Any], modifications: list[dict[str, Any]], scenario_name: str) -> dict[str, Any]:
    baseline_id = str(spec.get("baseline_id") or args.baseline_id or "").strip()
    if not baseline_id and args.base_year:
        baseline_id = "original_2014" if args.base_year == 2014 else "registered_2024"
    if not baseline_id:
        raise ValueError("baseline_id is required: choose original_2014 or registered_2024")
    model_name = args.model_name or spec.get("model_name")
    baseline = resolve_baseline_context(baseline_id, model_name)
    aliases = load_aggregation_aliases(baseline["aggregation_mapping"])

    source_cmf_value = args.base_cmf or (Path(str(spec["base_cmf"])) if spec.get("base_cmf") else None)
    source_cmf = resolve_project_path(source_cmf_value) if source_cmf_value else None
    if source_cmf:
        if not source_cmf.is_file():
            raise FileNotFoundError(f"Base CMF not found: {source_cmf}")
        base_text = source_cmf.read_text(encoding="utf-8", errors="replace").rstrip()
        context = extract_cmf_context(base_text)
        if context.get("baseline_id") != baseline_id:
            raise ValueError(
                f"Base CMF uses baseline_id={context.get('baseline_id')!r}; requested baseline_id={baseline_id!r}"
            )
        if context.get("model_name") and context["model_name"] != baseline["model_name"]:
            raise ValueError(f"Base CMF model {context['model_name']!r} does not match {baseline['model_name']!r}")
    else:
        base_text, context = build_scenario_cmf(scenario_name, baseline)
        base_text = base_text.rstrip()

    rtms_rates = load_rtms_rates(Path(baseline["baserate"]))
    closure_swaps = set(context.get("closure_swaps") or [])
    resolved = [resolve_explicit_modification(item, aliases, rtms_rates, closure_swaps) for item in modifications]
    base_text, resolved = apply_replacements(base_text, resolved)
    context = {
        **baseline,
        **context,
        "scenario_name": scenario_name,
        "shocks": [*(context.get("shocks") or []), *[public_item(item) for item in resolved]],
    }
    output_cmf = resolve_output_cmf(args, spec, scenario_name, f"policy__{baseline_id}")
    output_cmf.parent.mkdir(parents=True, exist_ok=True)
    final_text = replace_context_line(base_text, context) + "\n\n" + explicit_policy_block(scenario_name, source_cmf, resolved) + "\n"
    output_cmf.write_text(final_text, encoding="utf-8")

    resolved_json = output_cmf.with_suffix(".resolved.json")
    payload = {
        "ok": True,
        "scenario_name": scenario_name,
        "baseline": baseline,
        "closure_id": context.get("closure_id"),
        "closure_swaps": sorted(closure_swaps),
        "output_cmf": str(output_cmf),
        "resolved_json": str(resolved_json),
        "modifications": [public_item(item) for item in resolved],
    }
    resolved_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_resolved_csv(output_cmf.with_suffix(".resolved.csv"), [public_item(item) for item in resolved])
    POLICY_DIR.mkdir(parents=True, exist_ok=True)
    SUMMARY_PATH.write_text(
        "\n".join(
            [
                "Policy modification CMF created",
                f"Scenario: {scenario_name}",
                f"Baseline ID: {baseline_id}",
                f"Baseline data: {baseline['basedata']}",
                f"Model: {baseline['model_name']}",
                f"Closure: {context.get('closure_id')}",
                f"Output CMF: {output_cmf}",
                *[f"- {item['shock_line']}" for item in resolved],
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return payload


def main() -> None:
    args = parser().parse_args()
    spec = load_policy_spec(args.spec_json)
    modifications = spec.get("modifications") or []
    if not isinstance(modifications, list) or not modifications:
        raise ValueError("Policy spec must include a non-empty modifications array.")
    if not all(isinstance(item, dict) for item in modifications):
        raise ValueError("Each policy modification must be a JSON object.")

    scenario_name = str(spec.get("scenario_name") or "policy modification")
    resolved_payload = run_explicit_policy(args, spec, modifications, scenario_name)
    print(json.dumps(resolved_payload, ensure_ascii=False, indent=2))


def resolve_output_cmf(args: argparse.Namespace, spec: dict[str, Any], scenario_name: str, stem: str) -> Path:
    if args.output_cmf:
        return resolve_result_path(args.output_cmf)
    if spec.get("output_cmf"):
        return resolve_result_path(Path(spec["output_cmf"]))
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return resolve_result_path(POLICY_CMF_DIR / f"{stem}__{slugify(scenario_name)}__{timestamp}.cmf")


def write_policy_outputs(
    output_cmf: Path,
    scenario_name: str,
    base_label: str,
    resolved: list[dict[str, Any]],
) -> dict[str, Any]:
    resolved_json = output_cmf.with_suffix(".resolved.json")
    resolved_csv = output_cmf.with_suffix(".resolved.csv")
    resolved_payload = {
        "scenario_name": scenario_name,
        "base": base_label,
        "output_cmf": str(output_cmf),
        "modifications": [public_item(item) for item in resolved],
    }
    resolved_json.write_text(json.dumps(resolved_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_resolved_csv(resolved_csv, resolved)

    summary_lines = [
        "Policy modification CMF created",
        f"Scenario: {scenario_name}",
        f"Base: {base_label}",
        f"Output CMF: {output_cmf}",
        f"Resolved JSON: {resolved_json}",
        f"Resolved CSV: {resolved_csv}",
        "Applied shock lines:",
        *[f"- {item['shock_line']}" for item in resolved],
        "Existing lines replaced:",
        *[f"- {item['shock_line']} ({item['replacement_count']})" for item in resolved if item.get("replaced_existing")],
        "Aggregation note: country and commodity names are mapped to the current 10-region/10-sector GTAP aggregation.",
    ]
    POLICY_DIR.mkdir(parents=True, exist_ok=True)
    SUMMARY_PATH.write_text("\n".join(summary_lines) + "\n", encoding="utf-8")
    return resolved_payload


def run_policy_on_2024_baseline(
    args: argparse.Namespace,
    spec: dict[str, Any],
    modifications: list[dict[str, Any]],
    scenario_name: str,
    region_aliases: dict[str, str],
    regions: set[str],
    sector_aliases: dict[str, str],
    sectors: set[str],
) -> dict[str, Any]:
    if not BASELINE_2024_DATA.is_file() or not BASELINE_2024_RATES.is_file():
        raise FileNotFoundError(
            "The project has not been updated to a 2024 baseline: missing "
            f"{BASELINE_2024_DATA}. Run build_2024_baseline_update to generate the baseline CMF, "
            "then solve it with run_gtap_scenario(set_as_default_baseline=true) and register it as the project's default 2024 baseline before running policy analysis."
        )

    baseline_metadata = default_baseline_metadata()
    model_name = str(args.model_name or spec.get("model_name") or baseline_metadata.get("model_name") or MODEL_NAME)
    model_dir = RUNGTAP_DIR / model_name
    if model_dir.resolve().parent != RUNGTAP_DIR.resolve():
        raise ValueError(f"Invalid RunGTAP model name: {model_name}")
    for required_name in ["sets.har", "default.prm"]:
        if not (model_dir / required_name).is_file():
            raise FileNotFoundError(f"Default baseline model is missing {required_name}: {model_dir}")

    output_cmf = resolve_output_cmf(args, spec, scenario_name, "policy_2024")

    rtms_rates = load_rtms_rates(BASELINE_2024_RATES)
    resolved = [
        resolve_modification(item, region_aliases, regions, sector_aliases, sectors, rtms_rates)
        for item in modifications
    ]
    for item in resolved:
        item["replaced_existing"] = False
        item["replacement_count"] = 0

    cmf_text = build_policy_cmf_2024(scenario_name, resolved, model_name=model_name)
    output_cmf.parent.mkdir(parents=True, exist_ok=True)
    output_cmf.write_text(cmf_text, encoding="utf-8")
    return write_policy_outputs(
        output_cmf,
        scenario_name,
        f"default 2024 baseline ({BASELINE_2024_DATA}), model={model_name}",
        resolved,
    )


def run_policy_on_2014_baseline(
    args: argparse.Namespace,
    spec: dict[str, Any],
    modifications: list[dict[str, Any]],
    scenario_name: str,
    region_aliases: dict[str, str],
    regions: set[str],
    sector_aliases: dict[str, str],
    sectors: set[str],
) -> dict[str, Any]:
    base_cmf = resolve_project_path(args.base_cmf or Path(spec.get("base_cmf") or latest_baseline_cmf()))
    if not base_cmf.is_file():
        raise FileNotFoundError(f"Base CMF not found: {base_cmf}")

    output_cmf = resolve_output_cmf(args, spec, scenario_name, base_cmf.stem)
    if output_cmf.resolve() == base_cmf.resolve():
        raise ValueError("Output CMF must differ from base CMF to avoid overwriting the source scenario.")

    model_dir = model_dir_from_cmf(base_cmf)
    if model_dir is None:
        raise FileNotFoundError(f"Could not infer RunGTAP model directory from CMF: {base_cmf}")
    rtms_rates = load_rtms_rates(model_dir / "baserate.har")
    resolved = [
        resolve_modification(item, region_aliases, regions, sector_aliases, sectors, rtms_rates)
        for item in modifications
    ]

    base_text = base_cmf.read_text(encoding="utf-8", errors="replace").rstrip()
    base_text, resolved = apply_replacements(base_text, resolved)
    policy_block = build_policy_block(scenario_name, base_cmf, resolved)
    output_cmf.parent.mkdir(parents=True, exist_ok=True)
    output_cmf.write_text(base_text + "\n\n" + policy_block + "\n", encoding="utf-8")
    return write_policy_outputs(output_cmf, scenario_name, f"2014 baseline-update CMF ({base_cmf})", resolved)


if __name__ == "__main__":
    main()
