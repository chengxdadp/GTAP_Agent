from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from gtap_aggregation import build_custom_mapping
from gtap_runtime import GTAPAGG_DIR, RUNGTAP_DIR, env_path

PROJECT_DIR = Path(__file__).resolve().parents[1]
ASSET_DIR = PROJECT_DIR / "asset"
RESULT_DIR = PROJECT_DIR / "result"

PKG_FILE = env_path("GTAP_PKG_FILE") or ASSET_DIR / "GTAP10A_GTAP-APT_2014.PKG"
DEFAULT_AGG_PROJECT_CANDIDATES = [
    GTAPAGG_DIR / "GTAP10A" / "GTAP-APT",
    GTAPAGG_DIR / "GTAP10A" / "GTAP",
]

DEFAULT_MODEL_NAME = "gtap2015_10x10"

AGG_RESULT_DIR = RESULT_DIR / "01_aggregation"
AGG_CONFIG_DIR = PROJECT_DIR / "config" / "aggregations"
MODEL_TEMPLATE_DIR = RUNGTAP_DIR / "model_templates"
MODEL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


def parser() -> argparse.ArgumentParser:
    argument_parser = argparse.ArgumentParser(
        description="Aggregate GTAP10A data with the default mapping or a structured custom aggregation."
    )
    argument_parser.add_argument("--mapping-file", type=Path, help="Base GTAPAgg GUI-format mapping. Defaults to default.txt.")
    argument_parser.add_argument("--model-name", help=f"Output RunGTAP model directory name. Default {DEFAULT_MODEL_NAME}.")
    argument_parser.add_argument("--custom-spec-json", help="Structured JSON containing region_groups and/or sector_groups overrides.")
    argument_parser.add_argument("--mapping-output", type=Path, help="Destination for a generated custom mapping under config/aggregations.")
    argument_parser.add_argument("--overwrite", action="store_true", help="Allow replacing an existing custom mapping/model.")
    return argument_parser


def slugify(value: object, default: str = "custom") -> str:
    raw = str(value or "").strip()
    slug = re.sub(r"[^a-z0-9_-]+", "_", raw.lower()).strip("_")
    if not slug and raw:
        slug = f"{default}_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:10]}"
    return (slug or default)[:48]


def validate_model_name(value: object) -> str:
    name = str(value or "").strip()
    if not MODEL_NAME_PATTERN.fullmatch(name):
        raise ValueError("Model name must contain only letters, digits, underscores, or hyphens and cannot be a path.")
    return name

def resolve_gtapagg_project_dir() -> Path:
    candidates = []
    env_project = env_path("GTAPAGG_PROJECT_DIR")
    if env_project:
        candidates.append(env_project)
    candidates.extend(DEFAULT_AGG_PROJECT_CANDIDATES)

    checked = []
    for candidate in candidates:
        checked.append(candidate)
        if (
            (candidate / "runagg.bat").is_file()
            and (candidate / "default.txt").is_file()
            and (candidate / "2014").is_dir()
        ):
            return candidate
    raise FileNotFoundError(
        "Could not find a GTAPAgg project directory with runagg.bat, default.txt, and 2014 input data. "
        f"Checked: {', '.join(str(path) for path in checked)}. "
        "Set GTAPAGG_PROJECT_DIR to override."
    )


def ensure_inside(child: Path, parent: Path) -> None:
    child_resolved = child.resolve()
    parent_resolved = parent.resolve()
    if child_resolved != parent_resolved and parent_resolved not in child_resolved.parents:
        raise RuntimeError(f"Refusing to operate outside {parent_resolved}: {child_resolved}")


def require_file(path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"Required file not found: {path}")


def require_dir(path: Path) -> None:
    if not path.is_dir():
        raise FileNotFoundError(f"Required directory not found: {path}")


def copy_if_exists(source: Path, dest: Path) -> None:
    if source.exists():
        if source.is_dir():
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(source, dest)
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest)


def copy_model_template_files(model_name: str, model_dir: Path) -> list[str]:
    template_dir = MODEL_TEMPLATE_DIR / model_name
    if not template_dir.is_dir():
        return []
    copied = []
    for source in template_dir.iterdir():
        if source.is_file():
            shutil.copy2(source, model_dir / source.name)
            copied.append(source.name)
    return sorted(copied)


def parse_gtapagg_mapping(path: Path) -> list[list[str]]:
    """Parse the 6-section GTAPAgg GUI mapping format."""
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
        if not in_section or not current and current is None:
            continue
        if not line or line.startswith("!"):
            continue
        current.append(line)

    if len(sections) != 6:
        raise ValueError(f"Expected 6 sections in {path}, found {len(sections)}")
    return sections


def split_name_desc(line: str) -> tuple[str, str]:
    if "&" in line:
        left, right = line.split("&", 1)
        return left.strip(), right.strip()
    return line.strip(), ""


def split_old_mapping(line: str, valid_targets: dict[str, str] | None = None) -> tuple[str, str, str]:
    left, target = line.split("&", 1)
    left = left.strip()
    target = target.strip()
    if valid_targets:
        target = valid_targets.get(target.lower(), target)
    parts = left.split(None, 1)
    old_name = parts[0]
    old_desc = parts[1] if len(parts) > 1 else ""
    return old_name, old_desc, target


def split_factor(line: str) -> tuple[str, str, str, str]:
    parts = [part.strip() for part in line.split("&")]
    while len(parts) < 4:
        parts.append("")
    return parts[0], parts[1].lower(), parts[2], parts[3]


def write_flexagg_text(gtapagg_file: Path, output_file: Path) -> None:
    sections = parse_gtapagg_mapping(gtapagg_file)

    sectors = [split_name_desc(line) for line in sections[0]]
    sector_targets = {name.lower(): name for name, _ in sectors}
    sector_map = [split_old_mapping(line, sector_targets) for line in sections[1]]

    regions = [split_name_desc(line) for line in sections[2]]
    region_targets = {name.lower(): name for name, _ in regions}
    region_map = [split_old_mapping(line, region_targets) for line in sections[3]]

    factors = [split_factor(line) for line in sections[4]]
    factor_targets = {name.lower(): name for name, *_ in factors}
    factor_map = [split_old_mapping(line, factor_targets) for line in sections[5]]

    lines: list[str] = [
        " ! FlexAgg text-data mapping generated from GTAPAgg GUI mapping.",
        f" ! Source aggregation is GTAP10A GTAP-APT 2014, {len(sectors)} sectors x {len(regions)} regions.",
        "",
        f'{len(sectors)} STRINGS LENGTH 12 header "H2" longname "Set TRAD_COMM traded commodities";',
    ]
    lines.extend(f"{name:<12} ! {desc}" for name, desc in sectors)
    lines.extend(
        [
            "",
            f'{len(sector_map)} STRINGS LENGTH 12 header "DCOM" longname "Sectoral aggregation mapping";',
        ]
    )
    lines.extend(f"{target:<12} ! {old:<12} {desc}" for old, desc, target in sector_map)

    lines.extend(
        [
            "",
            f'{len(regions)} STRINGS LENGTH 12 header "H1" longname "Set REG regions";',
        ]
    )
    lines.extend(f"{name:<12} ! {desc}" for name, desc in regions)
    lines.extend(
        [
            "",
            f'{len(region_map)} STRINGS LENGTH 12 header "DREG" longname "Regional aggregation mapping";',
        ]
    )
    lines.extend(f"{target:<12} ! {old:<12} {desc}" for old, desc, target in region_map)

    lines.extend(
        [
            "",
            f'{len(factors)} STRINGS LENGTH 12 header "H6" longname "Set ENDW_COMM endowment commodities";',
        ]
    )
    for name, mobility, etrae, desc in factors:
        comment = etrae if mobility == "sluggish" else "mobile"
        if desc:
            comment = f"{comment} {desc}"
        lines.append(f"{name:<12} ! {comment}")

    lines.extend(["", f'{len(factors)} integer header "SLUG";'])
    lines.extend(f"{1 if mobility == 'sluggish' else 0} ! {mobility}" for _, mobility, _, _ in factors)

    lines.extend(["", f'{len(factors)} real header "ETRE" longname "Value of ETRAE for endowments";'])
    for _, mobility, etrae, _ in factors:
        value = etrae if mobility == "sluggish" and etrae != "---" else "-2.0"
        lines.append(f"{value} ! {mobility}")

    lines.extend(
        [
            "",
            f'{len(factor_map)} STRINGS LENGTH 12 header "DEND" longname "Endowment aggregation mapping";',
        ]
    )
    lines.extend(f"{target:<12} ! {old:<12} {desc}" for old, desc, target in factor_map)
    lines.extend(["", " ! END OF FILE", ""])

    output_file.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    args = parser().parse_args()
    agg_project_dir = resolve_gtapagg_project_dir()
    input_dir = env_path("GTAPAGG_INPUT_DIR") or agg_project_dir / "2014"
    base_mapping = args.mapping_file or env_path("GTAPAGG_MAPPING") or agg_project_dir / "default.txt"

    custom_spec: dict | None = None
    custom_metadata: dict | None = None
    if args.custom_spec_json:
        try:
            custom_spec = json.loads(args.custom_spec_json)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid custom aggregation JSON: {exc}") from exc
        if not isinstance(custom_spec, dict):
            raise ValueError("Custom aggregation JSON must be an object.")

    if custom_spec:
        aggregation_name = custom_spec.get("aggregation_name") or "custom"
        model_name = validate_model_name(args.model_name or f"gtap2015_{slugify(aggregation_name)}")
        candidate_model_dir = RUNGTAP_DIR / model_name
        ensure_inside(candidate_model_dir, RUNGTAP_DIR)
        if candidate_model_dir.exists() and not args.overwrite:
            raise FileExistsError(f"Custom RunGTAP model already exists: {candidate_model_dir}. Use overwrite=true to replace it.")
        mapping_output = args.mapping_output or AGG_CONFIG_DIR / f"{slugify(aggregation_name)}.txt"
        if not mapping_output.is_absolute():
            mapping_output = PROJECT_DIR / mapping_output
        mapping_output = mapping_output.resolve()
        AGG_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        ensure_inside(mapping_output, AGG_CONFIG_DIR)
        if mapping_output.exists() and not args.overwrite:
            raise FileExistsError(f"Custom aggregation mapping already exists: {mapping_output}. Use overwrite=true to replace it.")
        custom_text, custom_metadata = build_custom_mapping(
            base_mapping.read_text(encoding="utf-8", errors="replace"), custom_spec
        )
        mapping_output.write_text(custom_text, encoding="utf-8")
        custom_metadata.update(
            {
                "model_name": model_name,
                "base_mapping": str(base_mapping.resolve()),
                "mapping_file": str(mapping_output),
            }
        )
        mapping_output.with_suffix(".metadata.json").write_text(
            json.dumps(custom_metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        gtapagg_mapping = mapping_output
    else:
        if args.mapping_output:
            raise ValueError("--mapping-output requires --custom-spec-json.")
        model_name = validate_model_name(args.model_name or DEFAULT_MODEL_NAME)
        gtapagg_mapping = base_mapping.resolve()

    model_dir = RUNGTAP_DIR / model_name

    require_file(PKG_FILE)
    require_dir(RUNGTAP_DIR)
    require_dir(agg_project_dir)
    require_dir(input_dir)
    require_file(gtapagg_mapping)
    require_file(agg_project_dir / "runagg.bat")

    ensure_inside(model_dir, RUNGTAP_DIR)

    RESULT_DIR.mkdir(exist_ok=True)
    AGG_RESULT_DIR.mkdir(parents=True, exist_ok=True)

    scenario_mapping = AGG_RESULT_DIR / f"{slugify(model_name)}_flexagg.txt"
    write_flexagg_text(gtapagg_mapping, scenario_mapping)

    model_dir.mkdir(parents=True, exist_ok=True)
    if model_dir.exists():
        for item in model_dir.iterdir():
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()

    command = [
        "cmd.exe",
        "/c",
        "runagg.bat",
        str(input_dir),
        str(scenario_mapping),
        str(model_dir),
    ]

    started = datetime.now()
    completed = subprocess.run(
        command,
        cwd=agg_project_dir,
        text=True,
        capture_output=True,
        check=False,
    )

    template_files = copy_model_template_files(model_name, model_dir)

    (AGG_RESULT_DIR / "runagg_stdout.log").write_text(completed.stdout, encoding="utf-8", errors="replace")
    (AGG_RESULT_DIR / "runagg_stderr.log").write_text(completed.stderr, encoding="utf-8", errors="replace")
    copy_if_exists(agg_project_dir / "wholejob.log", AGG_RESULT_DIR / "wholejob.log")
    copy_if_exists(agg_project_dir / "error.log", AGG_RESULT_DIR / "error.log")
    copy_if_exists(agg_project_dir / "output" / "GTAPv7" / "gtapv7.zip", AGG_RESULT_DIR / "gtapv7.zip")

    required_outputs = [
        "basedata.har",
        "baserate.har",
        "baseview.har",
        "default.prm",
        "sets.har",
        "metadata.har",
    ]
    missing_outputs = [name for name in required_outputs if not (model_dir / name).is_file()]

    summary = [
        f"Aggregation started: {started.isoformat(timespec='seconds')}",
        f"Aggregation ended: {datetime.now().isoformat(timespec='seconds')}",
        f"Return code: {completed.returncode}",
        f"Input PKG checked: {PKG_FILE}",
        f"GTAPAgg project directory: {agg_project_dir}",
        f"GTAPAgg input directory: {input_dir}",
        f"GTAPAgg mapping source: {gtapagg_mapping}",
        f"FlexAgg text-data mapping generated at: {scenario_mapping}",
        f"RunGTAP model name: {model_name}",
        f"RunGTAP model directory: {model_dir}",
        f"RunGTAP template files restored: {', '.join(template_files) if template_files else 'none'}",
        f"Missing required outputs: {', '.join(missing_outputs) if missing_outputs else 'none'}",
    ]
    (AGG_RESULT_DIR / "aggregation_summary.txt").write_text("\n".join(summary) + "\n", encoding="utf-8")

    if completed.returncode != 0:
        raise RuntimeError(f"GTAPAgg run failed. See {AGG_RESULT_DIR / 'error.log'}")
    if missing_outputs:
        raise RuntimeError(f"Aggregation finished but required outputs are missing: {missing_outputs}")

    shutil.copy2(gtapagg_mapping, model_dir / "aggregation_mapping.txt")
    if custom_metadata:
        (model_dir / "aggregation_metadata.json").write_text(
            json.dumps(custom_metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    print(f"OK: aggregated GTAP 2014 data to {model_dir}")
    print(
        json.dumps(
            {
                "ok": True,
                "model_name": model_name,
                "model_dir": str(model_dir),
                "mapping_file": str(gtapagg_mapping),
                "custom": bool(custom_spec),
                "aggregation": custom_metadata,
            },
            ensure_ascii=False,
        )
    )
    print(f"Logs: {AGG_RESULT_DIR}")


if __name__ == "__main__":
    main()
