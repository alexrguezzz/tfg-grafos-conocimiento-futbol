from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any
import os
import time

try:
    from .task_result import TaskResult, TaskStatus, error_result, ok_result
    from .standalone_timing import finish_standalone_timer
except ImportError:  # Allows direct execution from src/pipeline.
    from task_result import TaskResult, TaskStatus, error_result, ok_result
    from standalone_timing import finish_standalone_timer


TASK_RESULT_PATH_ENV = "SOCCERDATA_TASK_RESULT_PATH"
PIPELINE_MANAGED_STEP_ENV = "SOCCERDATA_PIPELINE_MANAGED_STEP"
TASK_ID_ENV = "SOCCERDATA_TASK_ID"
TASK_PHASE_ENV = "SOCCERDATA_TASK_PHASE"
TASK_LEAGUE_ENV = "SOCCERDATA_TASK_LEAGUE"
TASK_SEASON_ENV = "SOCCERDATA_TASK_SEASON"


def _write_if_enabled(result: TaskResult) -> None:
    if not os.getenv(PIPELINE_MANAGED_STEP_ENV):
        return
    raw_path = os.getenv(TASK_RESULT_PATH_ENV)
    if not raw_path:
        return

    path = Path(raw_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = result.to_dict()
    import json

    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def run_with_optional_task_result(
    task_id: str,
    phase: str,
    func: Callable[[], dict[str, Any] | None],
) -> None:
    start = time.perf_counter()
    resolved_task_id = os.getenv(TASK_ID_ENV) or task_id
    resolved_phase = os.getenv(TASK_PHASE_ENV) or phase
    try:
        details = func() or {}
        duration = time.perf_counter() - start
        result = ok_result(
            task_id=resolved_task_id,
            phase=resolved_phase,
            duration_seconds=duration,
            warnings=list(details.get("warnings") or []),
            league=details.get("league") or os.getenv(TASK_LEAGUE_ENV),
            season=details.get("season") or os.getenv(TASK_SEASON_ENV),
            input_files=list(details.get("input_files") or []),
            output_files=list(details.get("output_files") or []),
            metrics=dict(details.get("metrics") or {}),
        )
        explicit_status = details.get("status")
        if explicit_status == TaskStatus.WARNING.value:
            result.status = TaskStatus.WARNING
        _write_if_enabled(result)
        finish_standalone_timer(result.status.value, elapsed=duration)
    except Exception as exc:
        duration = time.perf_counter() - start
        result = error_result(
            task_id=resolved_task_id,
            phase=resolved_phase,
            duration_seconds=duration,
            league=os.getenv(TASK_LEAGUE_ENV),
            season=os.getenv(TASK_SEASON_ENV),
            errors=[str(exc)],
            technical_exception=repr(exc),
        )
        _write_if_enabled(result)
        finish_standalone_timer(TaskStatus.ERROR.value, elapsed=duration)
        raise
