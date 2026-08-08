from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sqlite3
import subprocess
import sys
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BENCHMARK_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BENCHMARK_DIR.parent
DEFAULT_CONFIG = BENCHMARK_DIR / "config.json"
DEFAULT_DATABASE = BENCHMARK_DIR / "log.sqlite"

if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from benchmark.validate import load_cases  # noqa: E402
from web.agent import (  # noqa: E402
    MAX_TOOL_ROUNDS,
    OPENROUTER_MODEL,
    TOOLS,
    build_system_prompt,
    execute_tool_call,
    normalize_assistant_message,
    openrouter_client,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def git_state() -> tuple[str | None, bool | None]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_DIR,
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=PROJECT_DIR,
                text=True,
                capture_output=True,
                check=True,
            ).stdout.strip()
        )
        return commit, dirty
    except (OSError, subprocess.SubprocessError):
        return None, None


def parser() -> argparse.ArgumentParser:
    argument_parser = argparse.ArgumentParser(description="Run the GTAP Agent benchmark and log every event to SQLite.")
    argument_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    argument_parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    argument_parser.add_argument("--repetitions", type=int, default=None)
    argument_parser.add_argument(
        "--condition",
        action="append",
        choices=["agent_tools", "no_tools"],
        help="Condition to run; repeat for both. Defaults to config.json.",
    )
    argument_parser.add_argument("--case-id", action="append", help="Run only selected case IDs.")
    argument_parser.add_argument("--seed", type=int, default=20260808)
    argument_parser.add_argument("--batch-id", default=None)
    return argument_parser


def connect_database(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS benchmark_batches (
            batch_id TEXT PRIMARY KEY,
            benchmark_version TEXT NOT NULL,
            started_at TEXT NOT NULL,
            ended_at TEXT,
            status TEXT NOT NULL,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            repetitions INTEGER NOT NULL,
            seed INTEGER NOT NULL,
            config_json TEXT NOT NULL,
            code_commit TEXT,
            code_dirty INTEGER,
            error TEXT
        );

        CREATE TABLE IF NOT EXISTS benchmark_cases (
            case_id TEXT PRIMARY KEY,
            split TEXT NOT NULL,
            title TEXT NOT NULL,
            difficulty TEXT NOT NULL,
            expected_behavior TEXT NOT NULL,
            prompt TEXT NOT NULL,
            prompt_sha256 TEXT NOT NULL,
            case_json TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS benchmark_runs (
            run_id TEXT PRIMARY KEY,
            batch_id TEXT NOT NULL REFERENCES benchmark_batches(batch_id),
            case_id TEXT NOT NULL REFERENCES benchmark_cases(case_id),
            condition TEXT NOT NULL,
            repetition INTEGER NOT NULL,
            ordinal INTEGER NOT NULL,
            status TEXT NOT NULL,
            started_at TEXT NOT NULL,
            ended_at TEXT,
            latency_seconds REAL,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            model_parameters_json TEXT NOT NULL,
            system_prompt TEXT NOT NULL,
            system_prompt_sha256 TEXT NOT NULL,
            final_response TEXT,
            parsed_response_json TEXT,
            messages_json TEXT,
            artifact_paths_json TEXT,
            input_tokens INTEGER,
            output_tokens INTEGER,
            reasoning_tokens INTEGER,
            api_cost REAL,
            error TEXT,
            UNIQUE(batch_id, case_id, condition, repetition)
        );

        CREATE TABLE IF NOT EXISTS benchmark_api_calls (
            api_call_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL REFERENCES benchmark_runs(run_id),
            call_index INTEGER NOT NULL,
            started_at TEXT NOT NULL,
            ended_at TEXT NOT NULL,
            latency_seconds REAL NOT NULL,
            request_json TEXT NOT NULL,
            response_json TEXT,
            finish_reason TEXT,
            input_tokens INTEGER,
            output_tokens INTEGER,
            reasoning_tokens INTEGER,
            api_cost REAL,
            error TEXT,
            UNIQUE(run_id, call_index)
        );

        CREATE TABLE IF NOT EXISTS benchmark_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL REFERENCES benchmark_runs(run_id),
            sequence INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            created_at TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            UNIQUE(run_id, sequence)
        );

        CREATE TABLE IF NOT EXISTS benchmark_tool_calls (
            tool_call_row_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL REFERENCES benchmark_runs(run_id),
            round_index INTEGER NOT NULL,
            tool_index INTEGER NOT NULL,
            tool_call_id TEXT,
            tool_name TEXT NOT NULL,
            arguments_json TEXT NOT NULL,
            started_at TEXT NOT NULL,
            ended_at TEXT NOT NULL,
            latency_seconds REAL NOT NULL,
            ok INTEGER NOT NULL,
            result_json TEXT NOT NULL,
            error TEXT,
            UNIQUE(run_id, round_index, tool_index)
        );

        CREATE INDEX IF NOT EXISTS idx_runs_batch ON benchmark_runs(batch_id, ordinal);
        CREATE INDEX IF NOT EXISTS idx_runs_case ON benchmark_runs(case_id, condition);
        CREATE INDEX IF NOT EXISTS idx_events_run ON benchmark_events(run_id, sequence);
        CREATE INDEX IF NOT EXISTS idx_tools_run ON benchmark_tool_calls(run_id, round_index, tool_index);
        """
    )
    return connection


def insert_case(connection: sqlite3.Connection, case: dict[str, Any]) -> None:
    connection.execute(
        """
        INSERT INTO benchmark_cases (
            case_id, split, title, difficulty, expected_behavior, prompt, prompt_sha256, case_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(case_id) DO UPDATE SET
            split=excluded.split,
            title=excluded.title,
            difficulty=excluded.difficulty,
            expected_behavior=excluded.expected_behavior,
            prompt=excluded.prompt,
            prompt_sha256=excluded.prompt_sha256,
            case_json=excluded.case_json
        """,
        (
            case["id"],
            case["split"],
            case["title"],
            case["difficulty"],
            case["expected_behavior"],
            case["prompt"],
            sha256_text(case["prompt"]),
            json_text(case),
        ),
    )


def usage_values(response: dict[str, Any]) -> tuple[int | None, int | None, int | None, float | None]:
    usage = response.get("usage") or {}
    completion_details = usage.get("completion_tokens_details") or {}
    cost = usage.get("cost")
    return (
        usage.get("prompt_tokens"),
        usage.get("completion_tokens"),
        completion_details.get("reasoning_tokens"),
        float(cost) if cost is not None else None,
    )


def record_api_call(
    connection: sqlite3.Connection,
    run_id: str,
    call_index: int,
    request_payload: dict[str, Any],
    response: dict[str, Any] | None,
    started_at: str,
    ended_at: str,
    latency: float,
    error: str | None = None,
) -> None:
    input_tokens = output_tokens = reasoning_tokens = cost = None
    finish_reason = None
    if response:
        input_tokens, output_tokens, reasoning_tokens, cost = usage_values(response)
        choices = response.get("choices") or []
        if choices:
            finish_reason = choices[0].get("finish_reason")
    connection.execute(
        """
        INSERT INTO benchmark_api_calls (
            run_id, call_index, started_at, ended_at, latency_seconds, request_json,
            response_json, finish_reason, input_tokens, output_tokens, reasoning_tokens, api_cost, error
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            call_index,
            started_at,
            ended_at,
            latency,
            json_text(request_payload),
            json_text(response) if response is not None else None,
            finish_reason,
            input_tokens,
            output_tokens,
            reasoning_tokens,
            cost,
            error,
        ),
    )


def parse_json_response(content: str) -> dict[str, Any] | None:
    candidate = content.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", candidate, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        candidate = fenced.group(1)
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def collect_artifact_paths(value: Any) -> list[str]:
    paths: set[str] = set()

    def visit(item: Any, key: str = "") -> None:
        if isinstance(item, dict):
            for child_key, child_value in item.items():
                visit(child_value, str(child_key))
        elif isinstance(item, list):
            for child in item:
                visit(child, key)
        elif isinstance(item, str) and any(
            token in key.lower() for token in ("path", "dir", "file", "cmf", "manifest", "mapping")
        ):
            candidate = Path(item)
            if candidate.is_absolute() or item.lower().startswith(("result", "runtime", "config", "asset")):
                paths.add(item)

    visit(value)
    return sorted(paths)


def api_completion(
    connection: sqlite3.Connection,
    run_id: str,
    call_index: int,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    response_format: dict[str, Any] | None = None,
) -> dict[str, Any]:
    request_payload: dict[str, Any] = {
        "model": OPENROUTER_MODEL,
        "messages": messages,
        "stream": False,
        "extra_body": {"reasoning": {"enabled": True}},
    }
    kwargs = dict(request_payload)
    if tools is not None:
        request_payload["tools"] = tools
        request_payload["tool_choice"] = "auto"
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    if response_format is not None:
        request_payload["response_format"] = response_format
        kwargs["response_format"] = response_format

    started_at = utc_now()
    started = time.perf_counter()
    try:
        response_object = openrouter_client().chat.completions.create(**kwargs)
        response = response_object.model_dump(mode="json", exclude_none=True)
    except Exception as exc:
        ended_at = utc_now()
        latency = time.perf_counter() - started
        record_api_call(
            connection,
            run_id,
            call_index,
            request_payload,
            None,
            started_at,
            ended_at,
            latency,
            f"{type(exc).__name__}: {exc}",
        )
        connection.commit()
        raise
    ended_at = utc_now()
    latency = time.perf_counter() - started
    record_api_call(
        connection,
        run_id,
        call_index,
        request_payload,
        response,
        started_at,
        ended_at,
        latency,
    )
    connection.commit()
    return response


def add_event(connection: sqlite3.Connection, run_id: str, sequence: int, event_type: str, payload: Any) -> None:
    connection.execute(
        "INSERT INTO benchmark_events (run_id, sequence, event_type, created_at, payload_json) VALUES (?, ?, ?, ?, ?)",
        (run_id, sequence, event_type, utc_now(), json_text(payload)),
    )


def run_agent_tools(
    connection: sqlite3.Connection,
    run_id: str,
    prompt: str,
    system_prompt: str,
) -> dict[str, Any]:
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt},
    ]
    events: list[dict[str, Any]] = []
    artifacts: set[str] = set()
    final_content = ""
    call_index = 0
    event_sequence = 0

    for round_index in range(1, MAX_TOOL_ROUNDS + 1):
        call_index += 1
        response = api_completion(connection, run_id, call_index, messages, TOOLS)
        choice = response["choices"][0]
        assistant_message = normalize_assistant_message(choice["message"])
        tool_calls = assistant_message.get("tool_calls") or []
        api_event = {
            "round": round_index,
            "finish_reason": choice.get("finish_reason"),
            "assistant": assistant_message,
        }
        events.append({"type": "assistant_response", **api_event})
        event_sequence += 1
        add_event(connection, run_id, event_sequence, "assistant_response", api_event)

        if assistant_message.get("content"):
            final_content = str(assistant_message["content"])
        if not tool_calls:
            messages.append(assistant_message)
            connection.commit()
            return {
                "final": final_content,
                "parsed": None,
                "messages": messages,
                "events": events,
                "artifacts": sorted(artifacts),
                "status": "completed",
            }

        pending_tool_messages: list[dict[str, Any]] = []
        for tool_index, tool_call in enumerate(tool_calls, start=1):
            function = tool_call.get("function") or {}
            name = str(function.get("name") or "unknown")
            raw_arguments = function.get("arguments") or "{}"
            try:
                arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
            except json.JSONDecodeError:
                arguments = {}
            tool_started_at = utc_now()
            tool_started = time.perf_counter()
            result, result_text = execute_tool_call(tool_call)
            tool_ended_at = utc_now()
            tool_latency = time.perf_counter() - tool_started
            artifacts.update(collect_artifact_paths(result))
            connection.execute(
                """
                INSERT INTO benchmark_tool_calls (
                    run_id, round_index, tool_index, tool_call_id, tool_name, arguments_json,
                    started_at, ended_at, latency_seconds, ok, result_json, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    round_index,
                    tool_index,
                    tool_call.get("id"),
                    name,
                    json_text(arguments),
                    tool_started_at,
                    tool_ended_at,
                    tool_latency,
                    int(bool(result.get("ok"))),
                    result_text,
                    result.get("error"),
                ),
            )
            tool_event = {"round": round_index, "tool": name, "arguments": arguments, "result": result}
            events.append({"type": "tool_result", **tool_event})
            event_sequence += 1
            add_event(connection, run_id, event_sequence, "tool_result", tool_event)
            pending_tool_messages.append(
                {"role": "tool", "tool_call_id": tool_call.get("id"), "content": result_text}
            )
            connection.commit()
        messages.append(assistant_message)
        messages.extend(pending_tool_messages)

    final_content = final_content or "Tool-call limit reached before a final response."
    messages.append({"role": "assistant", "content": final_content})
    return {
        "final": final_content,
        "parsed": None,
        "messages": messages,
        "events": events,
        "artifacts": sorted(artifacts),
        "status": "tool_limit",
    }


def run_no_tools(
    connection: sqlite3.Connection,
    run_id: str,
    prompt: str,
    system_prompt: str,
) -> dict[str, Any]:
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt},
    ]
    response = api_completion(
        connection,
        run_id,
        1,
        messages,
        None,
        response_format={"type": "json_object"},
    )
    choice = response["choices"][0]
    assistant = normalize_assistant_message(choice["message"])
    messages.append(assistant)
    content = str(assistant.get("content") or "")
    parsed = parse_json_response(content)
    add_event(
        connection,
        run_id,
        1,
        "assistant_response",
        {"finish_reason": choice.get("finish_reason"), "assistant": assistant},
    )
    connection.commit()
    return {
        "final": content,
        "parsed": parsed,
        "messages": messages,
        "events": [{"type": "assistant_response", "assistant": assistant}],
        "artifacts": [],
        "status": "completed" if parsed is not None else "invalid_json",
    }


def summarize_usage(connection: sqlite3.Connection, run_id: str) -> tuple[int | None, int | None, int | None, float | None]:
    row = connection.execute(
        """
        SELECT SUM(input_tokens), SUM(output_tokens), SUM(reasoning_tokens), SUM(api_cost)
        FROM benchmark_api_calls WHERE run_id = ?
        """,
        (run_id,),
    ).fetchone()
    return tuple(row) if row else (None, None, None, None)


def execute_run(
    connection: sqlite3.Connection,
    batch_id: str,
    case: dict[str, Any],
    condition: str,
    repetition: int,
    ordinal: int,
    system_prompt: str,
) -> str:
    run_id = f"{batch_id}_{ordinal:03d}_{uuid.uuid4().hex[:8]}"
    started_at = utc_now()
    started = time.perf_counter()
    model_parameters = {
        "reasoning": {"enabled": True},
        "tools_enabled": condition == "agent_tools",
        "response_format": "json_object" if condition == "no_tools" else None,
    }
    connection.execute(
        """
        INSERT INTO benchmark_runs (
            run_id, batch_id, case_id, condition, repetition, ordinal, status, started_at,
            provider, model, model_parameters_json, system_prompt, system_prompt_sha256
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            batch_id,
            case["id"],
            condition,
            repetition,
            ordinal,
            "running",
            started_at,
            "openrouter",
            OPENROUTER_MODEL,
            json_text(model_parameters),
            system_prompt,
            sha256_text(system_prompt),
        ),
    )
    connection.commit()

    result: dict[str, Any] | None = None
    error: str | None = None
    try:
        if condition == "agent_tools":
            result = run_agent_tools(connection, run_id, case["prompt"], system_prompt)
        else:
            result = run_no_tools(connection, run_id, case["prompt"], system_prompt)
        status = result["status"]
    except Exception as exc:
        status = "error"
        error = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"

    ended_at = utc_now()
    latency = time.perf_counter() - started
    input_tokens, output_tokens, reasoning_tokens, api_cost = summarize_usage(connection, run_id)
    connection.execute(
        """
        UPDATE benchmark_runs SET
            status=?, ended_at=?, latency_seconds=?, final_response=?, parsed_response_json=?,
            messages_json=?, artifact_paths_json=?, input_tokens=?, output_tokens=?, reasoning_tokens=?,
            api_cost=?, error=?
        WHERE run_id=?
        """,
        (
            status,
            ended_at,
            latency,
            result.get("final") if result else None,
            json_text(result.get("parsed")) if result and result.get("parsed") is not None else None,
            json_text(result.get("messages")) if result else None,
            json_text(result.get("artifacts")) if result else None,
            input_tokens,
            output_tokens,
            reasoning_tokens,
            api_cost,
            error,
            run_id,
        ),
    )
    connection.commit()
    return status


def main() -> int:
    args = parser().parse_args()
    config_path = args.config.resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    case_path = (BENCHMARK_DIR / config["case_file"]).resolve()
    cases = load_cases(case_path)
    if args.case_id:
        selected = set(args.case_id)
        cases = [case for case in cases if case["id"] in selected]
        missing = sorted(selected - {case["id"] for case in cases})
        if missing:
            raise ValueError(f"Unknown case IDs: {', '.join(missing)}")
    repetitions = args.repetitions if args.repetitions is not None else int(config["repetitions"])
    if repetitions < 1:
        raise ValueError("repetitions must be at least 1")
    conditions = args.condition or list(config["conditions"])
    database_path = args.database.resolve()
    batch_id = args.batch_id or f"pilot_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    code_commit, code_dirty = git_state()
    connection = connect_database(database_path)
    started_at = utc_now()
    connection.execute(
        """
        INSERT INTO benchmark_batches (
            batch_id, benchmark_version, started_at, status, provider, model, repetitions,
            seed, config_json, code_commit, code_dirty
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            batch_id,
            config["benchmark_version"],
            started_at,
            "running",
            "openrouter",
            OPENROUTER_MODEL,
            repetitions,
            args.seed,
            json_text(config),
            code_commit,
            None if code_dirty is None else int(code_dirty),
        ),
    )
    for case in cases:
        insert_case(connection, case)
    connection.commit()

    agent_system_prompt = build_system_prompt()
    no_tools_system_prompt = (BENCHMARK_DIR / "prompts" / "no_tools_system.md").read_text(encoding="utf-8")
    jobs = [
        (case, condition, repetition)
        for repetition in range(1, repetitions + 1)
        for case in cases
        for condition in conditions
    ]
    random.Random(args.seed).shuffle(jobs)
    errors = 0
    try:
        for ordinal, (case, condition, repetition) in enumerate(jobs, start=1):
            print(
                f"[{ordinal}/{len(jobs)}] {condition} rep={repetition} case={case['id']}",
                flush=True,
            )
            system_prompt = agent_system_prompt if condition == "agent_tools" else no_tools_system_prompt
            status = execute_run(
                connection,
                batch_id,
                case,
                condition,
                repetition,
                ordinal,
                system_prompt,
            )
            print(f"  status={status}", flush=True)
            if status == "error":
                errors += 1
        batch_status = "completed" if errors == 0 else "completed_with_errors"
        connection.execute(
            "UPDATE benchmark_batches SET status=?, ended_at=? WHERE batch_id=?",
            (batch_status, utc_now(), batch_id),
        )
        connection.commit()
    except BaseException as exc:
        connection.execute(
            "UPDATE benchmark_batches SET status=?, ended_at=?, error=? WHERE batch_id=?",
            ("interrupted", utc_now(), f"{type(exc).__name__}: {exc}", batch_id),
        )
        connection.commit()
        raise
    finally:
        connection.close()

    print(
        json.dumps(
            {
                "ok": errors == 0,
                "batch_id": batch_id,
                "database": str(database_path),
                "runs": len(jobs),
                "errors": errors,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
