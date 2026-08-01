from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from gtap_runtime import find_executable


PROJECT_DIR = Path(__file__).resolve().parents[1]
RESULT_DIR = PROJECT_DIR / "result"
READ_RESULT_DIR = RESULT_DIR / "07_result_reads"


DEFAULT_SOLUTION_VARIABLES = ["qgdp", "avareg", "EV", "DTBAL", "tot", "WEV"]
DEFAULT_SECTOR_VARIABLES = ["qo"]
DEFAULT_VOLUME_HEADERS = ["DQO", "DTOT"]
DEFAULT_UPDATED_HEADERS = ["AG01", "AG02", "SMRY"]


def parser() -> argparse.ArgumentParser:
    argument_parser = argparse.ArgumentParser(description="Read and summarize collected RunGTAP/GEMPACK results.")
    argument_parser.add_argument("--result-dir", type=Path, help="Run result directory. Defaults to the newest result/03_run* directory.")
    argument_parser.add_argument(
        "--view",
        default="default",
        choices=[
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
        help="Result view to read.",
    )
    argument_parser.add_argument("--variable", action="append", help="GTAP variable/header to read. Can be repeated or comma-separated.")
    argument_parser.add_argument("--header", action="append", help="HAR header to read. Can be repeated or comma-separated.")
    argument_parser.add_argument("--region", help="Aggregate region filter, e.g. EastAsia.")
    argument_parser.add_argument("--sector", help="Aggregate sector/commodity filter, e.g. GrainsCrops.")
    argument_parser.add_argument("--exporter", help="Source/exporter region filter for bilateral variables.")
    argument_parser.add_argument("--importer", help="Destination/importer region filter for bilateral variables.")
    argument_parser.add_argument("--contains", help="Case-insensitive substring filter over all dimensions and long names.")
    argument_parser.add_argument("--max-rows", type=int, default=40, help="Maximum rows to return for query/detail views.")
    argument_parser.add_argument("--top", type=int, default=8, help="Top absolute-value rows for default broad summaries.")
    argument_parser.add_argument("--include-baseline", action="store_true", help="Include compact baseline and updated data excerpts when possible.")
    argument_parser.add_argument("--no-sort-abs", action="store_true", help="Keep HAR row order instead of sorting by absolute value.")
    return argument_parser


def as_list(values: list[str] | None) -> list[str]:
    if not values:
        return []
    items: list[str] = []
    for value in values:
        items.extend(part.strip() for part in value.split(",") if part.strip())
    return items


def safe_project_path(value: str | Path | None, default: Path | None = None) -> Path | None:
    if value is None:
        return default
    path = Path(value)
    if not path.is_absolute():
        path = PROJECT_DIR / path
    resolved = path.resolve()
    project = PROJECT_DIR.resolve()
    if resolved != project and project not in resolved.parents:
        raise ValueError(f"Path is outside project directory: {value}")
    return resolved


def newest_run_result_dir() -> Path:
    candidates = [
        path
        for path in RESULT_DIR.glob("03_run*")
        if path.is_dir() and ((path / "run_summary.txt").exists() or (path / "GTAP.log").exists())
    ]
    if not candidates:
        raise FileNotFoundError("No RunGTAP result directory found under result/03_run*.")

    def stamp(path: Path) -> float:
        summary = path / "run_summary.txt"
        return summary.stat().st_mtime if summary.exists() else path.stat().st_mtime

    return max(candidates, key=stamp)


def read_text(path: Path, max_chars: int | None = None) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    if max_chars is not None and len(text) > max_chars:
        return text[-max_chars:]
    return text


def parse_key_value_lines(text: str) -> dict[str, str]:
    data: dict[str, str] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        data[key.strip()] = value.strip()
    return data


def resolve_path_from_summary(value: str | None, result_dir: Path) -> Path | None:
    if not value or value.lower() == "none":
        return None
    path = Path(value)
    if path.is_absolute():
        return path
    project_path = PROJECT_DIR / path
    if project_path.exists():
        return project_path.resolve()
    result_path = result_dir / path
    if result_path.exists():
        return result_path.resolve()
    return project_path.resolve()


def run_subprocess(command: list[str], timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=PROJECT_DIR, text=True, capture_output=True, check=False, timeout=timeout)


def har_metadata(har_path: Path) -> dict[str, dict[str, str]]:
    har2txt = find_executable("har2txt.exe") or find_executable("har2txt")
    if not har2txt or not har_path.exists():
        return {}
    with tempfile.TemporaryDirectory(prefix="gtap_har_meta_") as tmp_dir:
        out_path = Path(tmp_dir) / "headers.txt"
        completed = run_subprocess([har2txt, str(har_path), str(out_path)])
        if completed.returncode != 0 or not out_path.exists():
            return {}
        text = read_text(out_path)

    metadata: dict[str, dict[str, str]] = {}
    pattern = re.compile(r'Header\s+"([^"]+)"\s+LongName\s+"([^"]*)"', re.IGNORECASE)
    for match in pattern.finditer(text):
        header = match.group(1).strip()
        long_name = match.group(2).strip()
        variable = variable_from_long_name(long_name, header)
        metadata[header] = {"header": header, "variable": variable, "long_name": long_name}
    return metadata


def variable_from_long_name(long_name: str, header: str) -> str:
    if "#" in long_name:
        return long_name.split("#", 1)[0].strip()
    match = re.match(r"([A-Za-z_][A-Za-z0-9_]*)", long_name)
    return match.group(1).strip() if match else header.strip()


def resolve_headers(metadata: dict[str, dict[str, str]], variables: list[str], headers: list[str]) -> list[str]:
    resolved: list[str] = []
    by_header = {key.lower(): key for key in metadata}
    by_variable: dict[str, list[str]] = {}
    for header, item in metadata.items():
        by_variable.setdefault(item.get("variable", "").lower(), []).append(header)

    for header in headers:
        key = header.strip().lower()
        resolved.append(by_header.get(key, header.strip()))

    for variable in variables:
        key = variable.strip().lower()
        if key in by_header:
            resolved.append(by_header[key])
            continue
        matches = by_variable.get(key)
        if matches:
            resolved.extend(matches)
            continue
        for header, item in metadata.items():
            haystack = f"{item.get('variable', '')} {item.get('long_name', '')}".lower()
            if key in haystack:
                resolved.append(header)

    seen: set[str] = set()
    unique: list[str] = []
    for header in resolved:
        token = header.strip()
        if token and token not in seen:
            seen.add(token)
            unique.append(token)
    return unique


def disambiguate_columns(columns: list[str]) -> list[str]:
    seen: Counter[str] = Counter()
    output: list[str] = []
    for column in columns:
        clean = column.strip() or "index"
        seen[clean] += 1
        output.append(clean if seen[clean] == 1 else f"{clean}_{seen[clean]}")
    return output


def semantic_fields(dimensions: dict[str, str]) -> dict[str, str]:
    fields: dict[str, str] = {}
    regions = [value for key, value in dimensions.items() if key.upper().startswith("REG")]
    commodities = [value for key, value in dimensions.items() if "COMM" in key.upper()]
    if len(regions) == 1:
        fields["region"] = regions[0]
    elif len(regions) >= 2:
        fields["source_region"] = regions[0]
        fields["destination_region"] = regions[-1]
    if commodities:
        fields["sector"] = commodities[0]
    return fields


def read_har_rows(har_path: Path, headers: list[str] | None, metadata: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    har2csv = find_executable("har2csv.exe") or find_executable("har2csv")
    if not har2csv:
        raise RuntimeError("har2csv executable was not found on PATH or GEMPACK/RUNGTAP environment paths.")
    if not har_path.exists():
        raise FileNotFoundError(f"HAR file not found: {har_path}")

    with tempfile.TemporaryDirectory(prefix="gtap_har_csv_") as tmp_dir:
        out_path = Path(tmp_dir) / "data.csv"
        command = [har2csv, str(har_path), str(out_path)]
        if headers:
            command.extend(headers)
        command.append("/H")
        completed = run_subprocess(command, timeout=180)
        if completed.returncode != 0 or not out_path.exists():
            raise RuntimeError((completed.stderr or completed.stdout or "har2csv failed").strip())
        rows = parse_har_csv(out_path, metadata)
    return rows


def parse_har_csv(path: Path, metadata: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    columns: list[str] = []
    with path.open(newline="", encoding="utf-8", errors="replace") as handle:
        reader = csv.reader(handle)
        for raw in reader:
            if not raw:
                continue
            if raw[0].strip().lower() == "header":
                columns = disambiguate_columns(raw)
                continue
            if not columns or len(raw) < 2:
                continue
            if columns[-1] == "Value" and len(raw) < len(columns):
                values = raw[:1] + [""] * (len(columns) - len(raw)) + raw[1:]
            else:
                values = raw + [""] * (len(columns) - len(raw))
            record = dict(zip(columns, values))
            header = record.pop("Header", "").strip()
            value_text = record.pop("Value", "")
            try:
                value: float | str = float(value_text)
            except ValueError:
                value = value_text
            dimensions = {key: value for key, value in record.items() if value != ""}
            item = metadata.get(header.strip(), {"variable": header.strip(), "long_name": ""})
            output: dict[str, Any] = {
                "header": header.strip(),
                "variable": item.get("variable", header.strip()),
                "long_name": item.get("long_name", ""),
                "dimensions": dimensions,
                "value": value,
            }
            output.update(semantic_fields(dimensions))
            rows.append(output)
    return rows


def matches_filter(value: str, target: str | None) -> bool:
    if not target:
        return True
    return value.strip().lower() == target.strip().lower()


def contains_filter(row: dict[str, Any], target: str | None) -> bool:
    if not target:
        return True
    haystack = json.dumps(row, ensure_ascii=False).lower()
    return target.lower() in haystack


def filter_rows(
    rows: list[dict[str, Any]],
    region: str | None = None,
    sector: str | None = None,
    exporter: str | None = None,
    importer: str | None = None,
    contains: str | None = None,
) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for row in rows:
        dimensions = row.get("dimensions", {})
        region_values = [str(value) for key, value in dimensions.items() if key.upper().startswith("REG")]
        sector_values = [str(value) for key, value in dimensions.items() if "COMM" in key.upper()]
        if region and not any(matches_filter(value, region) for value in region_values):
            continue
        if sector and not any(matches_filter(value, sector) for value in sector_values):
            continue
        if exporter and not (region_values and matches_filter(region_values[0], exporter)):
            continue
        if importer and not (len(region_values) >= 2 and matches_filter(region_values[-1], importer)):
            continue
        if not contains_filter(row, contains):
            continue
        filtered.append(row)
    return filtered


def sort_and_limit(rows: list[dict[str, Any]], limit: int, sort_abs: bool = True) -> list[dict[str, Any]]:
    if sort_abs:
        rows = sorted(rows, key=lambda row: abs(row.get("value", 0.0)) if isinstance(row.get("value"), (int, float)) else -1, reverse=True)
    return [compact_row(row) for row in rows[: max(0, limit)]]


def compact_row(row: dict[str, Any]) -> dict[str, Any]:
    output = {
        "variable": row.get("variable"),
        "header": row.get("header"),
        "value": round(row["value"], 6) if isinstance(row.get("value"), float) else row.get("value"),
    }
    if row.get("region"):
        output["region"] = row["region"]
    if row.get("source_region"):
        output["source_region"] = row["source_region"]
    if row.get("destination_region"):
        output["destination_region"] = row["destination_region"]
    if row.get("sector"):
        output["sector"] = row["sector"]
    dimensions = row.get("dimensions", {})
    extra_dimensions = {
        key: value
        for key, value in dimensions.items()
        if key not in {"REG", "REG_2", "TRAD_COMM", "NSAV_COMM", "PROD_COMM", "DEMD_COMM"}
    }
    if extra_dimensions:
        output["dimensions"] = extra_dimensions
    return output


def summarize_run_status(result_dir: Path) -> dict[str, Any]:
    summary_text = read_text(result_dir / "run_summary.txt")
    summary = parse_key_value_lines(summary_text)
    flags = sorted(path.name for path in result_dir.glob("*.flg"))
    if summary.get("Flags") and summary["Flags"].lower() != "none":
        flags = sorted(set(flags + [part.strip() for part in summary["Flags"].split(",") if part.strip()]))
    missing_outputs = []
    if summary.get("Missing required outputs") and summary["Missing required outputs"].lower() != "none":
        missing_outputs = [part.strip() for part in summary["Missing required outputs"].split(",") if part.strip()]

    gtap_log = read_text(result_dir / "GTAP.log", max_chars=60000)
    completed_without_error = "(The program has completed without error.)" in gtap_log
    terminated_with_error = "(The program terminated with an error.)" in gtap_log or bool(flags)
    warnings = re.findall(r"%%WARNING\.[^\n]*(?:\n\s+[^\n]*)?", gtap_log, flags=re.IGNORECASE)
    warning_count_match = re.search(r"\(There were\s+(\d+)\s+general warnings?\.\)", gtap_log, flags=re.IGNORECASE)
    variable_accuracy = last_match(r'Variable accuracy "face" number is\s+(\d+)', gtap_log)
    data_accuracy = last_match(r'Data accuracy "face" number is\s+(\d+)', gtap_log)
    residual_ratio = last_match(r"Maximum residual ratio across whole simulation is\s+([0-9.E+-]+)", gtap_log)

    return {
        "run_ok": completed_without_error and not flags and not missing_outputs,
        "completed_without_error": completed_without_error,
        "terminated_with_error": terminated_with_error,
        "flags": flags,
        "missing_required_outputs": missing_outputs,
        "return_code": summary.get("Return code"),
        "run_started": summary.get("Run started"),
        "run_ended": summary.get("Run ended"),
        "model_directory": summary.get("Model directory"),
        "scenario_cmf": summary.get("Scenario CMF"),
        "collected_results": summary.get("Collected results"),
        "warnings_count": int(warning_count_match.group(1)) if warning_count_match else len(warnings),
        "warnings": warnings[:5],
        "accuracy": {
            "variable_face": int(variable_accuracy) if variable_accuracy else None,
            "data_face": int(data_accuracy) if data_accuracy else None,
            "maximum_residual_ratio": residual_ratio,
        },
    }


def last_match(pattern: str, text: str) -> str | None:
    matches = re.findall(pattern, text, flags=re.IGNORECASE)
    return matches[-1] if matches else None


def extract_error_lines(result_dir: Path) -> list[str]:
    texts = [
        read_text(path, max_chars=20000)
        for path in [result_dir / "GTAP.log", result_dir / "rungtap_stderr.log", result_dir / "rungtap_stdout.log"]
        if path.exists()
    ]
    lines: list[str] = []
    pattern = re.compile(r"(error|problem|terminated|not as expected|missing|required|failed|warning)", re.IGNORECASE)
    for text in texts:
        for line in text.splitlines():
            clean = line.strip()
            if clean and pattern.search(clean):
                lines.append(clean)
    return lines[-20:]


def extract_cmf_shocks(result_dir: Path, run_status: dict[str, Any]) -> dict[str, Any]:
    scenario_cmf = resolve_path_from_summary(run_status.get("scenario_cmf"), result_dir)
    cmf_candidates = [path for path in [scenario_cmf, result_dir / "GTAP.cmf"] if path and path.exists()]
    cmf_path = cmf_candidates[0] if cmf_candidates else None
    if not cmf_path:
        return {"path": str(scenario_cmf) if scenario_cmf else None, "shocks": [], "resolved_policy": None}

    text = read_text(cmf_path)
    shocks = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("shock "):
            shocks.append(stripped)

    resolved_policy = None
    resolved_path = cmf_path.with_suffix(".resolved.json")
    if resolved_path.exists():
        try:
            resolved_policy = json.loads(read_text(resolved_path))
        except json.JSONDecodeError:
            resolved_policy = {"path": str(resolved_path), "error": "Could not parse resolved JSON."}

    return {
        "path": str(cmf_path),
        "shock_count": len(shocks),
        "shocks": shocks[:80],
        "resolved_policy": resolved_policy,
    }


def available_files(result_dir: Path) -> dict[str, Any]:
    interesting = ["GTAP.sol", "GTAP.sl4", "GTAP.log", "GTAP.cmf", "GTAPVol.har", "decomp.har", "newview.har", "newrate.har", "gdata.upd", "run_summary.txt"]
    return {
        name: {"exists": (result_dir / name).exists(), "bytes": (result_dir / name).stat().st_size if (result_dir / name).exists() else 0}
        for name in interesting
    }


def result_file_for_view(view: str, result_dir: Path, run_status: dict[str, Any]) -> Path:
    if view in {"solution", "welfare"}:
        return result_dir / "GTAP.sol"
    if view == "volume":
        return result_dir / "GTAPVol.har"
    if view == "updated_data":
        return result_dir / "newview.har"
    if view == "base_data":
        model_dir = resolve_path_from_summary(run_status.get("model_directory"), result_dir)
        if not model_dir:
            raise FileNotFoundError("Model directory is unknown; cannot locate basedata.har.")
        return model_dir / "basedata.har"
    if view == "compare_data":
        return result_dir / "gdata.upd"
    raise ValueError(f"View {view} does not map to a HAR file.")


def read_view_rows(
    view: str,
    result_dir: Path,
    run_status: dict[str, Any],
    variables: list[str],
    headers: list[str],
    args: argparse.Namespace,
) -> dict[str, Any]:
    har_path = result_file_for_view(view, result_dir, run_status)
    metadata = har_metadata(har_path)
    if view == "welfare" and not variables and not headers:
        variables = ["EV", "WEV", "EV_ALT", "WEV_ALT"]
    resolved = resolve_headers(metadata, variables, headers)
    if not resolved and (variables or headers):
        resolved = headers + variables
    rows = read_har_rows(har_path, resolved or None, metadata)
    rows = filter_rows(rows, region=args.region, sector=args.sector, exporter=args.exporter, importer=args.importer, contains=args.contains)
    return {
        "source_file": str(har_path),
        "headers_read": resolved or "all",
        "row_count": len(rows),
        "rows": sort_and_limit(rows, args.max_rows, sort_abs=not args.no_sort_abs),
        "metadata": {header: metadata.get(header, {}) for header in (resolved or list(metadata)[:30])},
    }


def default_solution_summary(result_dir: Path, run_status: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    solution_path = result_dir / "GTAP.sol"
    if not solution_path.exists():
        return {"available": False, "reason": "GTAP.sol is missing."}
    metadata = har_metadata(solution_path)
    headers = resolve_headers(metadata, DEFAULT_SOLUTION_VARIABLES + DEFAULT_SECTOR_VARIABLES, [])
    rows = read_har_rows(solution_path, headers, metadata)

    indicators: dict[str, Any] = {}
    for variable in DEFAULT_SOLUTION_VARIABLES:
        variable_rows = [row for row in rows if str(row.get("variable", "")).lower() == variable.lower()]
        indicators[variable] = sort_and_limit(variable_rows, 20, sort_abs=False)

    qo_rows = [row for row in rows if str(row.get("variable", "")).lower() == "qo"]
    qo_rows = filter_rows(qo_rows, region=args.region, sector=args.sector, contains=args.contains)
    indicators["top_sector_output_changes_qo"] = sort_and_limit(qo_rows, args.top, sort_abs=True)
    return {
        "available": True,
        "source_file": str(solution_path),
        "unit_note": "Most GTAP solution variables are percent changes; EV/WEV and DTBAL are in $ US million per their LongName.",
        "indicators": indicators,
    }


def default_volume_summary(result_dir: Path, args: argparse.Namespace) -> dict[str, Any]:
    volume_path = result_dir / "GTAPVol.har"
    if not volume_path.exists():
        return {"available": False, "reason": "GTAPVol.har is missing."}
    metadata = har_metadata(volume_path)
    headers = resolve_headers(metadata, [], DEFAULT_VOLUME_HEADERS)
    rows = read_har_rows(volume_path, headers, metadata)
    rows = filter_rows(rows, region=args.region, sector=args.sector, exporter=args.exporter, importer=args.importer, contains=args.contains)
    return {
        "available": True,
        "source_file": str(volume_path),
        "top_volume_changes": sort_and_limit(rows, args.top, sort_abs=True),
    }


def baseline_updated_excerpt(result_dir: Path, run_status: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    output: dict[str, Any] = {}
    model_dir = resolve_path_from_summary(run_status.get("model_directory"), result_dir)
    base_path = model_dir / "basedata.har" if model_dir else None
    updated_path = result_dir / "newview.har"
    if base_path and base_path.exists():
        metadata = har_metadata(base_path)
        headers = resolve_headers(metadata, [], ["SAVE", "POP", "VKB"])
        rows = read_har_rows(base_path, headers, metadata)
        rows = filter_rows(rows, region=args.region, sector=args.sector, contains=args.contains)
        output["baseline"] = {"source_file": str(base_path), "rows": sort_and_limit(rows, args.top, sort_abs=True)}
    if updated_path.exists():
        metadata = har_metadata(updated_path)
        headers = resolve_headers(metadata, [], DEFAULT_UPDATED_HEADERS)
        rows = read_har_rows(updated_path, headers, metadata)
        rows = filter_rows(rows, region=args.region, sector=args.sector, contains=args.contains)
        output["updated_view"] = {"source_file": str(updated_path), "rows": sort_and_limit(rows, args.top, sort_abs=True)}
    return output


def compact_log(result_dir: Path) -> dict[str, Any]:
    return {
        "run_summary": read_text(result_dir / "run_summary.txt", max_chars=5000),
        "error_lines": extract_error_lines(result_dir),
        "gtap_log_tail": read_text(result_dir / "GTAP.log", max_chars=5000),
    }


def build_output(args: argparse.Namespace) -> dict[str, Any]:
    result_dir = safe_project_path(args.result_dir) if args.result_dir else newest_run_result_dir()
    if result_dir is None or not result_dir.exists():
        raise FileNotFoundError(f"Result directory not found: {args.result_dir}")

    run_status = summarize_run_status(result_dir)
    cmf = extract_cmf_shocks(result_dir, run_status)
    variables = as_list(args.variable)
    headers = as_list(args.header)
    output: dict[str, Any] = {
        "ok": True,
        "result_dir": str(result_dir),
        "view": args.view,
        "run_status": run_status,
        "pre_run": {"scenario": cmf},
        "available_files": available_files(result_dir),
        "notes": [
            "Default view reports broad pre-run inputs and post-run changes. Use view=solution with variable/region/sector/exporter/importer for targeted follow-up.",
            "For before/after level-style tables, use view=base_data for basedata.har and view=updated_data or compare_data for updated HAR outputs.",
        ],
    }

    if args.view == "files":
        return output
    if args.view == "log":
        output["log"] = compact_log(result_dir)
        return output
    if args.view == "cmf":
        return output

    if not run_status.get("run_ok"):
        output["diagnostics"] = {"error_lines": extract_error_lines(result_dir)}
        if args.view == "default":
            return output

    if args.view == "default":
        output["post_run"] = {
            "solution_summary": default_solution_summary(result_dir, run_status, args),
            "volume_summary": default_volume_summary(result_dir, args),
        }
        if args.include_baseline:
            output["before_after_excerpt"] = baseline_updated_excerpt(result_dir, run_status, args)
        return output

    output["query"] = read_view_rows(args.view, result_dir, run_status, variables, headers, args)
    return output


def main() -> None:
    args = parser().parse_args()
    try:
        output = build_output(args)
    except Exception as exc:
        output = {"ok": False, "error": str(exc)}

    READ_RESULT_DIR.mkdir(parents=True, exist_ok=True)
    summary_path = READ_RESULT_DIR / "latest_result_read.json"
    summary_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
