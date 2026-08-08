from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path

from gtap_scenario import (
    PROJECT_DIR,
    build_scenario_cmf,
    load_aggregation_aliases,
    normalize_closure_patches,
    resolve_baseline_context,
)


RESULT_DIR = PROJECT_DIR / "result"
CLOSURE_DIR = RESULT_DIR / "05_closure_modifications"


def parser() -> argparse.ArgumentParser:
    argument_parser = argparse.ArgumentParser(
        description="Create a GTAP scenario CMF by applying approved local closure swaps to the standard policy closure."
    )
    argument_parser.add_argument("--spec-json", required=True)
    return argument_parser


def slugify(value: object, default: str = "scenario") -> str:
    slug = re.sub(r"[^a-z0-9_-]+", "_", str(value or "").strip().lower()).strip("_")
    return (slug or default)[:60]


def main() -> None:
    spec = json.loads(parser().parse_args().spec_json)
    if not isinstance(spec, dict):
        raise ValueError("Closure specification must be a JSON object")
    baseline_id = str(spec.get("baseline_id") or "").strip()
    if not baseline_id:
        raise ValueError("baseline_id is required: use original_2014 or registered_2024")
    scenario_name = str(spec.get("scenario_name") or "closure scenario").strip()
    baseline = resolve_baseline_context(baseline_id, spec.get("model_name"))
    aliases = load_aggregation_aliases(baseline["aggregation_mapping"])
    patches = normalize_closure_patches(spec.get("modifications") or [], *aliases["region"])
    cmf_text, context = build_scenario_cmf(scenario_name, baseline, closure_patches=patches)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_cmf = CLOSURE_DIR / "cmf" / f"closure__{slugify(scenario_name)}__{timestamp}.cmf"
    output_cmf.parent.mkdir(parents=True, exist_ok=True)
    output_cmf.write_text(cmf_text, encoding="utf-8")
    manifest = output_cmf.with_suffix(".manifest.json")
    manifest.write_text(json.dumps(context, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    payload = {
        "ok": True,
        "output_cmf": str(output_cmf),
        "manifest": str(manifest),
        "baseline": baseline,
        "closure_id": context["closure_id"],
        "modifications": patches,
        "swap_lines": context["closure_swaps"],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
