from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import json
from typing import Any

from task_result import TaskResult, TaskStatus


class PipelineAudit:
    def __init__(
        self,
        *,
        project_root: Path,
        phases: list[str],
        leagues: list[str],
        seasons: list[str],
        ambiguous_dependencies: list[str] | None = None,
    ) -> None:
        self.project_root = project_root
        self.phases = phases
        self.leagues = leagues
        self.seasons = seasons
        self.ambiguous_dependencies = ambiguous_dependencies or []
        self.results: list[TaskResult] = []

    def add(self, result: TaskResult) -> None:
        self.results.append(result)

    def has_blocking_failure(self) -> bool:
        return any(result.status in {TaskStatus.ERROR, TaskStatus.SKIPPED} for result in self.results)

    def summary(self) -> dict[str, Any]:
        counts = Counter(result.status.value for result in self.results)
        return {
            status.value: counts.get(status.value, 0)
            for status in TaskStatus
            if counts.get(status.value, 0)
        }

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "project_root": str(self.project_root),
            "phases": self.phases,
            "leagues": self.leagues,
            "seasons": self.seasons,
            "summary": self.summary(),
            "tasks": [result.to_dict() for result in self.results],
        }
        if self.ambiguous_dependencies:
            payload["ambiguous_dependencies"] = self.ambiguous_dependencies
        return payload

    def write_json(self, path: Path | None = None) -> Path:
        output_path = path or self.project_root / "logs" / "pipeline_report.json"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as handle:
            json.dump(self.to_dict(), handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        return output_path
