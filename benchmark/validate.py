from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path


BENCHMARK_DIR = Path(__file__).resolve().parent
DEFAULT_CASE_FILE = BENCHMARK_DIR / "scenarios" / "pilot.jsonl"
REQUIRED_FIELDS = {
    "id",
    "split",
    "title",
    "difficulty",
    "expected_behavior",
    "prompt",
    "reference_plan",
    "target_outputs",
    "tags",
}
BEHAVIORS = {"run", "clarify", "reject"}
DIFFICULTIES = {"basic", "intermediate", "advanced"}


def fail(message: str) -> None:
    raise ValueError(message)


def validate_case(case: dict, line_number: int) -> None:
    missing = sorted(REQUIRED_FIELDS - set(case))
    extra = sorted(set(case) - REQUIRED_FIELDS)
    if missing:
        fail(f"line {line_number}: missing fields: {', '.join(missing)}")
    if extra:
        fail(f"line {line_number}: unexpected fields: {', '.join(extra)}")
    if case["expected_behavior"] not in BEHAVIORS:
        fail(f"line {line_number}: invalid expected_behavior")
    if case["difficulty"] not in DIFFICULTIES:
        fail(f"line {line_number}: invalid difficulty")
    if not isinstance(case["prompt"], str) or len(case["prompt"].strip()) < 20:
        fail(f"line {line_number}: prompt is too short")
    if not isinstance(case["reference_plan"], dict):
        fail(f"line {line_number}: reference_plan must be an object")
    plan_fields = {"aggregation", "baseline_id", "closure", "shocks", "result_queries", "forbidden_operations"}
    missing_plan = sorted(plan_fields - set(case["reference_plan"]))
    if missing_plan:
        fail(f"line {line_number}: reference_plan missing: {', '.join(missing_plan)}")
    if case["expected_behavior"] == "run" and not case["target_outputs"]:
        fail(f"line {line_number}: runnable case must declare target_outputs")
    if case["expected_behavior"] != "run" and case["target_outputs"]:
        fail(f"line {line_number}: clarify/reject case must not declare numerical targets")


def load_cases(path: Path) -> list[dict]:
    cases = []
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw_line.strip():
            continue
        try:
            case = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            fail(f"line {line_number}: invalid JSON: {exc}")
        if not isinstance(case, dict):
            fail(f"line {line_number}: case must be an object")
        validate_case(case, line_number)
        cases.append(case)
    ids = [case["id"] for case in cases]
    duplicates = sorted(case_id for case_id, count in Counter(ids).items() if count > 1)
    if duplicates:
        fail(f"duplicate case ids: {', '.join(duplicates)}")
    return cases


def main() -> int:
    path = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else DEFAULT_CASE_FILE
    cases = load_cases(path)
    behavior_counts = Counter(case["expected_behavior"] for case in cases)
    difficulty_counts = Counter(case["difficulty"] for case in cases)
    print(
        json.dumps(
            {
                "ok": True,
                "case_file": str(path),
                "case_count": len(cases),
                "behaviors": dict(sorted(behavior_counts.items())),
                "difficulties": dict(sorted(difficulty_counts.items())),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
