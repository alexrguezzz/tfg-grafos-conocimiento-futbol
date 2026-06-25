from __future__ import annotations

import run_pipeline
from task_registry import TASK_REGISTRY


def _artifact_paths(task_id: str) -> set[str]:
    return {artifact.path_template for artifact in TASK_REGISTRY[task_id].input_artifacts}


def test_all_runner_steps_are_registered() -> None:
    missing = [step.name for step in run_pipeline.build_steps() if step.name not in TASK_REGISTRY]

    assert missing == []


def test_rdf_events_declares_participation_input_and_dependency() -> None:
    spec = TASK_REGISTRY["rdf_events"]

    assert _artifact_paths("rdf_events") == {
        "data/processed/canonical/events.csv",
        "data/processed/canonical/player_match_participation.csv",
    }
    assert spec.depends_on == ("build_events", "build_player_match_participation")


def test_load_graphdb_waits_for_all_validations() -> None:
    spec = TASK_REGISTRY["load_graphdb"]

    assert spec.depends_on == (
        "validate_player_normalization",
        "validate_external_context",
        "validate_ttl",
    )


def test_load_graphdb_only_checks_merged_ttl_presence() -> None:
    spec = TASK_REGISTRY["load_graphdb"]

    assert len(spec.input_artifacts) == 1
    assert spec.input_artifacts[0].path_template == "data/ttl/full_knowledge_graph.ttl"
    assert spec.input_artifacts[0].kind == "file"


def test_ttl_pipeline_artifact_checks_are_presence_only_before_validate_ttl() -> None:
    rdf_spec = TASK_REGISTRY["rdf_events"]
    merge_spec = TASK_REGISTRY["merge_ttl"]

    assert rdf_spec.output_artifacts[0].path_template == "data/ttl/events.ttl"
    assert rdf_spec.output_artifacts[0].kind == "file"
    assert {artifact.kind for artifact in merge_spec.input_artifacts} == {"file"}
    assert merge_spec.output_artifacts[0].path_template == "data/ttl/full_knowledge_graph.ttl"
    assert merge_spec.output_artifacts[0].kind == "file"
