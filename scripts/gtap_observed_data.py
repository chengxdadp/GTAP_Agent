from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from gtap_runtime import GTAPAGG_DIR, env_path


PROJECT_DIR = Path(__file__).resolve().parents[1]
RESULT_DIR = PROJECT_DIR / "result"
OBSERVED_DIR = RESULT_DIR / "04_observed_data"
RAW_DIR = OBSERVED_DIR / "raw"
STANDARDIZED_DIR = OBSERVED_DIR / "standardized"

BASE_YEAR = 2014
USER_AGENT = "gtap-observed-shock-pipeline/1.0"
DEFAULT_GTAPAGG_PROJECT_CANDIDATES = [
    GTAPAGG_DIR / "GTAP10A" / "GTAP-APT",
    GTAPAGG_DIR / "GTAP10A" / "GTAP",
]

WORLD_BANK_GDP = "NY.GDP.MKTP.KD"
WORLD_BANK_POP = "SP.POP.TOTL"
WITS_TARIFF = "AHS-WGHTD-AVRG"


def ensure_dirs() -> None:
    for path in [OBSERVED_DIR, RAW_DIR, STANDARDIZED_DIR]:
        path.mkdir(parents=True, exist_ok=True)


def resolve_gtapagg_project_dir() -> Path:
    candidates = []
    env_project = env_path("GTAPAGG_PROJECT_DIR")
    if env_project:
        candidates.append(env_project)
    candidates.extend(DEFAULT_GTAPAGG_PROJECT_CANDIDATES)

    checked = []
    for candidate in candidates:
        checked.append(candidate)
        if (candidate / "default.txt").is_file():
            return candidate
    raise FileNotFoundError(
        "Could not find a GTAPAgg project directory with default.txt. "
        f"Checked: {', '.join(str(path) for path in checked)}. "
        "Set GTAPAGG_PROJECT_DIR or GTAPAGG_MAPPING to override."
    )


def resolve_gtapagg_mapping() -> Path:
    env_mapping = env_path("GTAPAGG_MAPPING")
    if env_mapping:
        return env_mapping
    return resolve_gtapagg_project_dir() / "default.txt"


def url_cache_path(source: str, url: str, suffix: str = ".json") -> Path:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    folder = RAW_DIR / source
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{digest}{suffix}"


def request_bytes(url: str, token: str | None = None, timeout: int = 60) -> bytes:
    headers = {"User-Agent": USER_AGENT}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def cached_json(source: str, url: str, refresh: bool = False, token: str | None = None) -> dict | list:
    path = url_cache_path(source, url)
    if path.exists() and not refresh:
        return json.loads(path.read_text(encoding="utf-8"))
    data = request_bytes(url, token=token)
    path.write_bytes(data)
    return json.loads(data.decode("utf-8"))


def write_csv(path: Path, rows: Iterable[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def parse_gtapagg_mapping(path: Path) -> tuple[list[dict], list[dict]]:
    sections: list[list[str]] = []
    current: list[str] | None = None
    in_section = False
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if line.startswith("="):
            if not in_section:
                current = []
                in_section = True
            else:
                sections.append(current or [])
                current = None
                in_section = False
            continue
        if not in_section or current is None or not line or line.startswith("!"):
            continue
        current.append(line)
    if len(sections) != 6:
        raise ValueError(f"Expected 6 GTAPAgg sections in {path}, found {len(sections)}")

    sectors = []
    for line in sections[1]:
        left, target = line.split("&", 1)
        old_parts = left.strip().split(None, 1)
        sectors.append(
            {
                "gtap_sector": old_parts[0],
                "gtap_sector_name": old_parts[1] if len(old_parts) > 1 else "",
                "gtap_sector_agg": target.strip(),
            }
        )

    regions = []
    for line in sections[3]:
        left, target = line.split("&", 1)
        old_parts = left.strip().split(None, 1)
        code = old_parts[0]
        regions.append(
            {
                "gtap_code": code,
                "iso3": gtap_code_to_iso3(code),
                "country_name": old_parts[1] if len(old_parts) > 1 else "",
                "gtap_region": target.strip(),
            }
        )
    return sectors, regions


def gtap_code_to_iso3(code: str) -> str:
    # Most GTAP region codes in GTAP10A are lower-case ISO3 codes. Aggregate
    # residual regions such as xoc/xna/xtw do not have country API series.
    lowered = code.lower()
    if lowered.startswith("x"):
        return ""
    return lowered.upper()


def numeric(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def pct_change(new_value: float | None, old_value: float | None) -> float | None:
    if new_value is None or old_value in (None, 0):
        return None
    return 100.0 * (new_value / old_value - 1.0)


def parse_sdmx_json_observations(payload: dict) -> list[dict]:
    rows: list[dict] = []
    structure = payload.get("structure", {})
    dimensions = (
        structure.get("dimensions", {})
        .get("observation", [])
    )
    time_values = []
    for dim in dimensions:
        if dim.get("id", "").lower() in {"time_period", "timeperiod"}:
            time_values = [value.get("id") or value.get("name") for value in dim.get("values", [])]
            break

    series_dimensions = structure.get("dimensions", {}).get("series", [])
    series_labels = []
    for dim in series_dimensions:
        values = dim.get("values", [])
        series_labels.append([value.get("id") or value.get("name") for value in values])

    for dataset in payload.get("dataSets", []):
        for series_key, series in dataset.get("series", {}).items():
            labels = {}
            parts = [int(part) for part in series_key.split(":") if part != ""]
            for idx, part in enumerate(parts):
                if idx < len(series_dimensions) and part < len(series_labels[idx]):
                    labels[series_dimensions[idx].get("id", f"dim_{idx}")] = series_labels[idx][part]
            for obs_key, obs in series.get("observations", {}).items():
                year = time_values[int(obs_key)] if time_values and obs_key.isdigit() else obs_key
                rows.append({**labels, "year": int(year), "value": numeric(obs[0])})
    return rows


def chunked(values: list[str], size: int) -> Iterable[list[str]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]


def latest_non_null_year(rows: list[dict], value_key: str = "value") -> int | None:
    years = [int(row["year"]) for row in rows if row.get(value_key) not in (None, "", "nan")]
    return max(years) if years else None


@dataclass
class FetchConfig:
    start_year: int = BASE_YEAR
    end_year: int | None = None
    refresh: bool = False
    include_wits: bool = True
    wits_sleep: float = 0.05
    max_wits_calls: int | None = None
    un_token: str | None = None
    wpp_file: Path | None = None


def world_bank_indicator(countries: list[str], indicator: str, start: int, end: int, refresh: bool) -> list[dict]:
    rows: list[dict] = []
    for country_chunk in chunked(countries, 45):
        country_arg = ";".join(country_chunk)
        url = (
            f"https://api.worldbank.org/v2/country/{country_arg}/indicator/{indicator}"
            f"?format=json&per_page=20000&date={start}:{end}"
        )
        payload = cached_json("world_bank", url, refresh=refresh)
        if not isinstance(payload, list) or len(payload) < 2:
            continue
        for item in payload[1]:
            value = numeric(item.get("value"))
            rows.append(
                {
                    "iso3": item.get("countryiso3code") or "",
                    "country_name": item.get("country", {}).get("value") or "",
                    "year": int(item["date"]),
                    "indicator": indicator,
                    "value": value,
                    "source": "world_bank",
                }
            )
    return rows


def un_wpp_population_from_api(countries: list[str], start: int, end: int, token: str, refresh: bool) -> list[dict]:
    locations_url = "https://population.un.org/dataportalapi/api/v1/locations?pageNumber=1&pageSize=500"
    locations = cached_json("un_wpp", locations_url, refresh=refresh)
    location_by_iso = {
        item.get("iso3"): item.get("id")
        for item in locations.get("data", [])
        if item.get("iso3") and item.get("id")
    }
    rows: list[dict] = []
    for iso3 in countries:
        location_id = location_by_iso.get(iso3)
        if not location_id:
            continue
        url = (
            "https://population.un.org/dataportalapi/api/v1/data/"
            f"indicators/49/locations/{location_id}/start/{start}/end/{end}"
        )
        try:
            payload = cached_json("un_wpp", url, refresh=refresh, token=token)
        except urllib.error.HTTPError:
            continue
        for item in payload.get("data", []):
            value = numeric(item.get("value"))
            if value is None:
                continue
            # WPP population indicators are commonly expressed in thousands.
            rows.append(
                {
                    "iso3": iso3,
                    "country_name": item.get("location") or "",
                    "year": int(item.get("timeLabel") or item.get("timeId")),
                    "indicator": "WPP_TOTAL_POPULATION",
                    "value": value * 1000.0,
                    "source": "un_wpp_api",
                }
            )
    return rows


def un_wpp_population_from_file(path: Path, countries: list[str], start: int, end: int) -> list[dict]:
    suffix = path.suffix.lower()
    rows: list[dict] = []
    if suffix == ".csv":
        table = read_csv(path)
    elif suffix in {".xlsx", ".xlsm"}:
        import openpyxl

        workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
        sheet = workbook.active
        raw_rows = list(sheet.iter_rows(values_only=True))
        header_index = None
        for index, row in enumerate(raw_rows[:50]):
            lowered = [str(cell).strip().lower() if cell is not None else "" for cell in row]
            if "iso3" in lowered and any(cell in {"year", "time"} for cell in lowered):
                header_index = index
                break
        if header_index is None:
            raise ValueError(f"Could not find WPP header row in {path}")
        headers = [str(cell).strip() if cell is not None else "" for cell in raw_rows[header_index]]
        table = [
            {headers[col]: value for col, value in enumerate(row) if col < len(headers)}
            for row in raw_rows[header_index + 1 :]
        ]
    else:
        raise ValueError(f"Unsupported WPP file type: {path}")

    wanted = set(countries)
    for item in table:
        lower_keys = {key.lower(): key for key in item.keys()}
        iso_key = lower_keys.get("iso3") or lower_keys.get("iso3code")
        year_key = lower_keys.get("year") or lower_keys.get("time")
        value_key = None
        for candidate in ["population", "total population, as of 1 january (thousands)", "poptotal", "totalpopulation"]:
            value_key = lower_keys.get(candidate)
            if value_key:
                break
        if not iso_key or not year_key or not value_key:
            continue
        iso3 = str(item.get(iso_key) or "").upper()
        year = int(float(item.get(year_key)))
        value = numeric(item.get(value_key))
        if iso3 in wanted and start <= year <= end and value is not None:
            rows.append(
                {
                    "iso3": iso3,
                    "country_name": str(item.get(lower_keys.get("location") or lower_keys.get("country") or "") or ""),
                    "year": year,
                    "indicator": "WPP_TOTAL_POPULATION",
                    "value": value * 1000.0 if value < 10_000_000 else value,
                    "source": "un_wpp_file",
                }
            )
    return rows


def wits_tariff_series(
    countries: list[str],
    start: int,
    end: int,
    refresh: bool,
    sleep_seconds: float,
    max_calls: int | None = None,
    existing_keys: set[tuple[str, int]] | None = None,
) -> list[dict]:
    rows: list[dict] = []
    calls = 0
    existing_keys = existing_keys or set()
    for iso3 in countries:
        for year in range(start, end + 1):
            if not refresh and (iso3, year) in existing_keys:
                continue
            if max_calls is not None and calls >= max_calls:
                return rows
            reporter = iso3.lower()
            url = (
                "https://wits.worldbank.org/API/V1/SDMX/V21/datasource/tradestats-tariff/"
                f"reporter/{reporter}/year/{year}/partner/wld/product/all/indicator/{WITS_TARIFF}?format=json"
            )
            try:
                payload = cached_json("wits_tariff", url, refresh=refresh)
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
                continue
            parsed = parse_sdmx_json_observations(payload)
            for item in parsed:
                value = item.get("value")
                if value is not None:
                    rows.append(
                        {
                            "iso3": iso3,
                            "year": year,
                            "indicator": WITS_TARIFF,
                            "value": value,
                            "source": "wits",
                        }
                    )
                    break
            calls += 1
            if sleep_seconds:
                time.sleep(sleep_seconds)
    return rows


def write_manifest(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "entries": entries,
    }
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def env_un_token() -> str | None:
    return os.environ.get("UN_DATAPORTAL_TOKEN") or os.environ.get("UN_WPP_TOKEN")


def safe_float(value: str | float | None) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, float):
        return value
    if str(value).lower() == "none":
        return None
    return float(value)


def sanitize_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_")
