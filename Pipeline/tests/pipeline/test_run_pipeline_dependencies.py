from __future__ import annotations

import pytest

import run_pipeline
from task_registry import TASK_REGISTRY
from task_result import TaskStatus


@pytest.mark.parametrize(
    "blocked_validation",
    [
        "validate_player_normalization",
        "validate_external_context",
        "validate_ttl",
    ],
)
def test_load_graphdb_is_skipped_when_any_validation_is_blocked(blocked_validation: str) -> None:
    step = next(step for step in run_pipeline.build_steps() if step.name == "load_graphdb")

    result = run_pipeline.execute_step(
        step,
        spec=TASK_REGISTRY["load_graphdb"],
        blocked_tasks={blocked_validation: "ERROR"},
        env={},
        events_rdf_chunk_size=50_000,
        clear_before_upload=True,
    )

    assert result.status == TaskStatus.SKIPPED
    assert blocked_validation in (result.skip_reason or "")
