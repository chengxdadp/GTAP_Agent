from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from gtap_aggregation import normalize_label, parse_mapping_sections, split_member, split_target
from gtap_runtime import RUNGTAP_DIR


PROJECT_DIR = Path(__file__).resolve().parents[1]
ASSET_DIR = PROJECT_DIR / "asset"
DEFAULT_BASELINE_METADATA = ASSET_DIR / "default_baseline.json"
DEFAULT_MODEL_NAME = "gtap2015_10x10"

BASELINE_IDS = {"original_2014", "registered_2024"}

# GTAPAgg preserves legacy coefficient labels in some aggregated HAR files.  In
# particular, the VTWR and DPSM headers contain the labels VTWR and DPSM, while
# the GTAP model reads them into coefficients named VTMFSD and DPARSUM.  Their
# dimensions, sets, and elements are still checked below; only the intentional
# coefficient-name aliases are excluded from comparison.
GTAP_CHECK_ON_READ_LINES = [
    "check-on-read coefficients = no ;",
    "check-on-read sets = warn ;",
    "check-on-read elements = yes ;",
    "check-on-read exact = no ;",
]

STANDARD_POLICY_EXOGENOUS_LINES = [
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

CLOSURE_PATCH_DESCRIPTIONS = {
    "gdp_target_tfp": (
        "For each selected region, swap exogenous avareg with endogenous qgdp. "
        "qgdp becomes shockable and avareg becomes the implied regional value-added productivity response."
    ),
    "fixed_regional_investment": (
        "For each selected region, swap exogenous cgdslack with endogenous qcgds so gross investment quantity "
        "can be fixed by a shock and cgdslack clears the capital-goods market."
    ),
}


def safe_model_name(value: object | None, default: str = DEFAULT_MODEL_NAME) -> str:
    model_name = str(value or default).strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", model_name):
        raise ValueError(f"Invalid RunGTAP model name: {model_name!r}")
    return model_name


def read_default_baseline_metadata() -> dict[str, Any]:
    if not DEFAULT_BASELINE_METADATA.is_file():
        return {}
    payload = json.loads(DEFAULT_BASELINE_METADATA.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Default baseline metadata must be a JSON object: {DEFAULT_BASELINE_METADATA}")
    return payload


def resolve_baseline_context(baseline_id: str, model_name: object | None = None) -> dict[str, Any]:
    if baseline_id not in BASELINE_IDS:
        raise ValueError(f"baseline_id must be one of {sorted(BASELINE_IDS)}; got {baseline_id!r}")

    metadata = read_default_baseline_metadata() if baseline_id == "registered_2024" else {}
    recorded_model = metadata.get("model_name") if metadata else None
    resolved_model = safe_model_name(model_name, str(recorded_model or DEFAULT_MODEL_NAME))
    if recorded_model and model_name and resolved_model != recorded_model:
        raise ValueError(
            f"registered_2024 is bound to model {recorded_model!r}, not {resolved_model!r}. "
            "Register a compatible baseline before using that model."
        )

    model_dir = (RUNGTAP_DIR / resolved_model).resolve()
    if model_dir.parent != RUNGTAP_DIR.resolve():
        raise ValueError(f"Model must stay inside {RUNGTAP_DIR}: {model_dir}")
    for required in ["sets.har", "default.prm"]:
        if not (model_dir / required).is_file():
            raise FileNotFoundError(f"Model is missing {required}: {model_dir}")

    if baseline_id == "original_2014":
        basedata = model_dir / "basedata.har"
        baserate = model_dir / "baserate.har"
        baseline_year = 2014
        origin = "original GTAP 10A model database"
        mapping = model_dir / "aggregation_mapping.txt"
    else:
        basedata = Path(str(metadata.get("basedata") or ASSET_DIR / "basedata_2024.har"))
        baserate = Path(str(metadata.get("baserate") or ASSET_DIR / "baserate_2024.har"))
        baseline_year = int(metadata.get("baseline_year") or 2024)
        origin = "registered historical-update baseline"
        mapping_value = metadata.get("aggregation_mapping")
        mapping = Path(str(mapping_value)) if mapping_value else model_dir / "aggregation_mapping.txt"

    for label, path in [("baseline data", basedata), ("baseline rates", baserate), ("aggregation mapping", mapping)]:
        if not path.is_file():
            raise FileNotFoundError(f"{label.title()} is missing for {baseline_id}: {path}")

    return {
        "baseline_id": baseline_id,
        "baseline_year": baseline_year,
        "origin": origin,
        "model_name": resolved_model,
        "model_dir": str(model_dir),
        "basedata": str(basedata.resolve()),
        "baserate": str(baserate.resolve()),
        "aggregation_mapping": str(mapping.resolve()),
    }


def _dimension_aliases(target_lines: list[str], member_lines: list[str]) -> tuple[dict[str, str], set[str]]:
    targets = [split_target(line)[0] for line in target_lines]
    aliases = {normalize_label(target): target for target in targets}
    for line in member_lines:
        member = split_member(line)
        target = next((item for item in targets if normalize_label(item) == normalize_label(member["target"])), member["target"])
        for value in [member["code"], member["description"], member["left"]]:
            key = normalize_label(value)
            if key:
                aliases[key] = target
    return aliases, set(targets)


def _factor_aliases(target_lines: list[str], member_lines: list[str]) -> tuple[dict[str, str], set[str]]:
    targets = [line.split("&", 1)[0].strip() for line in target_lines]
    aliases = {normalize_label(target): target for target in targets}
    for line in member_lines:
        left, target = line.rsplit("&", 1)
        target = target.strip()
        parts = left.strip().split(None, 1)
        for value in [parts[0], parts[1] if len(parts) > 1 else "", left.strip()]:
            key = normalize_label(value)
            if key:
                aliases[key] = target
    return aliases, set(targets)


def load_aggregation_aliases(mapping_path: str | Path) -> dict[str, tuple[dict[str, str], set[str]]]:
    path = Path(mapping_path)
    sections = parse_mapping_sections(path.read_text(encoding="utf-8", errors="replace"))
    sector = _dimension_aliases(sections[0], sections[1])
    region = _dimension_aliases(sections[2], sections[3])
    factor = _factor_aliases(sections[4], sections[5])
    for alias, original_code in {
        "soy": "osd",
        "soybean": "osd",
        "soybeans": "osd",
        "wheat": "wht",
        "paddy rice": "pdr",
    }.items():
        target = sector[0].get(normalize_label(original_code))
        if target:
            sector[0][normalize_label(alias)] = target
    for alias, original_code in {
        "united states": "usa",
        "united states of america": "usa",
        "u.s.": "usa",
        "us": "usa",
        "america": "usa",
        "prc": "chn",
    }.items():
        target = region[0].get(normalize_label(original_code))
        if target:
            region[0][normalize_label(alias)] = target
    return {"sector": sector, "region": region, "factor": factor}


def resolve_aggregate(
    value: object,
    aliases: dict[str, str],
    available: set[str],
    label: str,
) -> tuple[str, str]:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError(f"{label} is required")
    key = normalize_label(raw)
    if key in aliases:
        return aliases[key], raw
    choices = ", ".join(sorted(available))
    raise ValueError(f"Could not map {label} {raw!r}. Available aggregate values: {choices}")


def normalize_closure_patches(
    patches: object,
    region_aliases: dict[str, str],
    regions: set[str],
) -> list[dict[str, Any]]:
    if patches is None:
        return []
    if not isinstance(patches, list):
        raise ValueError("closure modifications must be an array")
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for index, patch in enumerate(patches, start=1):
        if not isinstance(patch, dict):
            raise ValueError(f"closure modification {index} must be an object")
        patch_type = str(patch.get("type") or "").strip()
        if patch_type not in CLOSURE_PATCH_DESCRIPTIONS:
            raise ValueError(
                f"Unsupported closure modification {patch_type!r}. Allowed: {', '.join(CLOSURE_PATCH_DESCRIPTIONS)}"
            )
        selectors = patch.get("regions")
        if not isinstance(selectors, list) or not selectors:
            raise ValueError(f"closure modification {patch_type!r} requires a non-empty regions array")
        resolved_regions: list[str] = []
        for selector in selectors:
            region, _ = resolve_aggregate(selector, region_aliases, regions, "region")
            key = (patch_type, region)
            if key in seen:
                raise ValueError(f"Duplicate closure modification {patch_type!r} for region {region!r}")
            seen.add(key)
            resolved_regions.append(region)
        normalized.append(
            {
                "type": patch_type,
                "regions": resolved_regions,
                "description": CLOSURE_PATCH_DESCRIPTIONS[patch_type],
            }
        )
    return normalized


def closure_swap_lines(patches: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for patch in patches:
        for region in patch["regions"]:
            if patch["type"] == "gdp_target_tfp":
                lines.append(f'Swap avareg("{region}") = qgdp("{region}");')
            elif patch["type"] == "fixed_regional_investment":
                lines.append(f'Swap cgdslack("{region}") = qcgds("{region}");')
    return lines


def build_scenario_cmf(
    scenario_name: str,
    baseline: dict[str, Any],
    closure_patches: list[dict[str, Any]] | None = None,
    shock_lines: list[str] | None = None,
    context_extra: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    patches = closure_patches or []
    swaps = closure_swap_lines(patches)
    context = {
        **baseline,
        "scenario_name": scenario_name,
        "closure_id": "standard_policy" if not patches else "standard_policy_patched",
        "closure_modifications": patches,
        "closure_swaps": swaps,
        **(context_extra or {}),
    }
    exogenous_lines = STANDARD_POLICY_EXOGENOUS_LINES.copy()
    exogenous_lines[-1] += " ;"
    lines = [
        f"! GTAP Agent policy scenario: {scenario_name}",
        f"! Baseline ID: {baseline['baseline_id']}",
        f"! Baseline year: {baseline['baseline_year']}",
        f"! Closure ID: {context['closure_id']}",
        "! GTAP_AGENT_CONTEXT " + json.dumps(context, ensure_ascii=True, separators=(",", ":")),
        f"! Model directory: {baseline['model_dir']}",
        *GTAP_CHECK_ON_READ_LINES,
        f"aux files = {RUNGTAP_DIR}\\GTAP;",
        f"file gtapSETS = {baseline['model_dir']}\\sets.har;",
        f"file gtapDATA = {baseline['basedata']};",
        "Updated file gtapDATA = gdata.upd;",
        f"Solution file = {RUNGTAP_DIR}\\work\\GTAP;",
        f"file gtapPARM = {baseline['model_dir']}\\default.prm;",
        "Verbal Description =",
        f"{scenario_name};",
        "Method = Gragg;",
        "Steps = 4 8 12;",
        "automatic accuracy = no;",
        "subintervals = 10;",
        "exogenous",
        *exogenous_lines,
        "Rest Endogenous ;",
        "",
        *swaps,
    ]
    if swaps:
        lines.append("")
    if shock_lines:
        lines.extend(shock_lines)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n", context


def extract_cmf_context(text: str) -> dict[str, Any]:
    match = re.search(r"(?m)^! GTAP_AGENT_CONTEXT (\{.*\})\s*$", text)
    if match:
        payload = json.loads(match.group(1))
        return payload if isinstance(payload, dict) else {}
    context: dict[str, Any] = {}
    for field, pattern in [
        ("baseline_id", r"(?im)^!\s*Baseline ID:\s*(.+?)\s*$"),
        ("baseline_year", r"(?im)^!\s*Baseline year:\s*(\d{4})\s*$"),
        ("closure_id", r"(?im)^!\s*Closure ID:\s*(.+?)\s*$"),
        ("model_dir", r"(?im)^!\s*Model directory:\s*(.+?)\s*$"),
        ("basedata", r"(?im)^\s*file\s+gtapDATA\s*=\s*(.+?)\s*;\s*$"),
    ]:
        found = re.search(pattern, text)
        if found:
            value: Any = found.group(1).strip()
            context[field] = int(value) if field == "baseline_year" else value
    if context.get("model_dir"):
        context["model_name"] = Path(str(context["model_dir"])).name
    return context
