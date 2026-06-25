from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class TaskStatus(str, Enum):
    OK = "OK"
    WARNING = "WARNING"
    ERROR = "ERROR"
    SKIPPED = "SKIPPED"


def _compact_value(value: Any) -> Any:
    if isinstance(value, TaskStatus):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, list):
        return [_compact_value(item) for item in value if item not in (None, "", [], {})]
    if isinstance(value, tuple):
        return [_compact_value(item) for item in value if item not in (None, "", [], {})]
    if isinstance(value, dict):
        return {
            str(key): _compact_value(item)
            for key, item in value.items()
            if item not in (None, "", [], {})
        }
    return value


@dataclass
class TaskResult:
    task_id: str
    phase: str
    status: TaskStatus
    duration_seconds: float | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    league: str | None = None
    season: str | None = None
    skip_reason: str | None = None
    input_files: list[str | Path] = field(default_factory=list)
    output_files: list[str | Path] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    technical_exception: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "task_id": self.task_id,
            "phase": self.phase,
            "status": self.status.value,
        }
        if self.duration_seconds is not None:
            payload["duration_seconds"] = round(self.duration_seconds, 3)

        optional_values = {
            "league": self.league,
            "season": self.season,
            "skip_reason": self.skip_reason,
            "input_files": self.input_files,
            "output_files": self.output_files,
            "errors": self.errors,
            "warnings": self.warnings,
            "metrics": self.metrics,
            "technical_exception": self.technical_exception,
        }
        for key, value in optional_values.items():
            compacted = _compact_value(value)
            if compacted not in (None, "", [], {}):
                payload[key] = compacted

        return payload


def ok_result(
    *,
    task_id: str,
    phase: str,
    duration_seconds: float,
    warnings: list[str] | None = None,
    league: str | None = None,
    season: str | None = None,
    input_files: list[str | Path] | None = None,
    output_files: list[str | Path] | None = None,
    metrics: dict[str, Any] | None = None,
) -> TaskResult:
    warning_values = warnings or []
    return TaskResult(
        task_id=task_id,
        phase=phase,
        status=TaskStatus.WARNING if warning_values else TaskStatus.OK,
        duration_seconds=duration_seconds,
        warnings=warning_values,
        league=league,
        season=season,
        input_files=input_files or [],
        output_files=output_files or [],
        metrics=metrics or {},
    )


def error_result(
    *,
    task_id: str,
    phase: str,
    duration_seconds: float,
    errors: list[str],
    warnings: list[str] | None = None,
    league: str | None = None,
    season: str | None = None,
    input_files: list[str | Path] | None = None,
    output_files: list[str | Path] | None = None,
    metrics: dict[str, Any] | None = None,
    technical_exception: str | None = None,
) -> TaskResult:
    return TaskResult(
        task_id=task_id,
        phase=phase,
        status=TaskStatus.ERROR,
        duration_seconds=duration_seconds,
        errors=errors,
        warnings=warnings or [],
        league=league,
        season=season,
        input_files=input_files or [],
        output_files=output_files or [],
        metrics=metrics or {},
        technical_exception=technical_exception,
    )


def skipped_result(
    *,
    task_id: str,
    phase: str,
    skip_reason: str,
    league: str | None = None,
    season: str | None = None,
    input_files: list[str | Path] | None = None,
) -> TaskResult:
    return TaskResult(
        task_id=task_id,
        phase=phase,
        status=TaskStatus.SKIPPED,
        league=league,
        season=season,
        skip_reason=skip_reason,
        input_files=input_files or [],
    )
