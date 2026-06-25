from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import run_pipeline
from pipeline_args import parse_pipeline_args


def test_scope_is_not_required_for_global_phases() -> None:
    args = parse_pipeline_args(["--phases", "rdf", "merge", "load"])

    assert args.leagues == []
    assert args.seasons == []


@pytest.mark.parametrize("phase", ["extract", "transform", "all"])
def test_scope_is_required_for_scoped_phases(phase: str) -> None:
    with pytest.raises(SystemExit) as exc_info:
        parse_pipeline_args(["--phases", phase])

    assert exc_info.value.code == 2


def test_leagues_and_seasons_must_be_provided_together() -> None:
    with pytest.raises(SystemExit) as exc_info:
        parse_pipeline_args(["--phases", "rdf", "--leagues", "ESP-La Liga"])

    assert exc_info.value.code == 2


def test_validate_accepts_optional_scope() -> None:
    args = parse_pipeline_args(
        [
            "--phases",
            "validate",
            "--leagues",
            "ESP-La Liga",
            "--seasons",
            "2023-2024",
        ]
    )

    assert args.leagues == ["ESP-La Liga"]
    assert args.seasons == ["2023-2024"]


def test_build_step_env_clears_scope_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOCCERDATA_PIPELINE_LEAGUES", '["ESP-La Liga"]')
    monkeypatch.setenv("SOCCERDATA_PIPELINE_SEASONS", '["2023-2024"]')

    env = run_pipeline.build_step_env(["ESP-La Liga"], ["2023-2024"], enable_scope=False)

    assert "SOCCERDATA_PIPELINE_LEAGUES" not in env
    assert "SOCCERDATA_PIPELINE_SEASONS" not in env


def test_build_step_env_exports_scope_when_enabled() -> None:
    env = run_pipeline.build_step_env(["ESP-La Liga"], ["2023-2024"])

    assert json.loads(env["SOCCERDATA_PIPELINE_LEAGUES"]) == ["ESP-La Liga"]
    assert json.loads(env["SOCCERDATA_PIPELINE_SEASONS"]) == ["2023-2024"]


def test_print_header_warns_when_scope_is_ignored(capsys: pytest.CaptureFixture[str]) -> None:
    args = SimpleNamespace(
        leagues=["ESP-La Liga"],
        seasons=["2023-2024"],
        events_rdf_chunk_size=50_000,
        clear_before_upload=True,
    )

    run_pipeline.print_header(args, ["rdf"], [])

    out = capsys.readouterr().out
    assert "Aviso: se indicaron ligas/temporadas" in out
    assert "alcance del pipeline: rdf" in out
