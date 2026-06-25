from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid

from audit import PipelineAudit
from console_output import format_path
from pipeline_args import PHASES_IGNORING_SCOPE, PHASES_USING_SCOPE, parse_pipeline_args
from task_registry import AMBIGUOUS_DEPENDENCIES, TASK_REGISTRY, TaskSpec, validate_artifacts
from task_result import TaskResult, TaskStatus, error_result, ok_result, skipped_result


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SEPARATOR = "=" * 72


@dataclass(frozen=True)
class PipelineStep:
    name: str
    phase: str
    script_path: Path


def build_steps() -> list[PipelineStep]:
    src_dir = PROJECT_ROOT / "src"

    return [
        # Extract phase
        PipelineStep("extract_espn_read_lineup", "extract", src_dir / "extract" / "extract_espn_read_lineup.py"),
        PipelineStep("extract_espn_read_matchsheet", "extract", src_dir / "extract" / "extract_espn_read_matchsheet.py"),
        PipelineStep("extract_matchhistory_read_games", "extract", src_dir / "extract" / "extract_matchhistory_read_games.py"),
        PipelineStep("extract_sofascore_read_league_table", "extract", src_dir / "extract" / "extract_sofascore_read_league_table.py"),
        PipelineStep("extract_sofascore_read_schedule", "extract", src_dir / "extract" / "extract_sofascore_read_schedule.py"),
        PipelineStep("extract_understat_read_player_match_stats", "extract", src_dir / "extract" / "extract_understat_read_player_match_stats.py"),
        PipelineStep("extract_understat_read_player_season_stats", "extract", src_dir / "extract" / "extract_understat_read_player_season_stats.py"),
        PipelineStep("extract_understat_read_schedule", "extract", src_dir / "extract" / "extract_understat_read_schedule.py"),
        PipelineStep("extract_understat_read_team_match_stats", "extract", src_dir / "extract" / "extract_understat_read_team_match_stats.py"),
        PipelineStep("extract_whoscored_read_events", "extract", src_dir / "extract" / "extract_whoscored_read_events.py"),
        PipelineStep("extract_whoscored_read_missing_players", "extract", src_dir / "extract" / "extract_whoscored_read_missing_players.py"),
        PipelineStep("extract_whoscored_read_schedule", "extract", src_dir / "extract" / "extract_whoscored_read_schedule.py"),
        # Transform phase
        PipelineStep("build_competitions", "transform", src_dir / "transform" / "build_competitions.py"),
        PipelineStep("build_seasons", "transform", src_dir / "transform" / "build_seasons.py"),
        PipelineStep("build_teams", "transform", src_dir / "transform" / "build_teams.py"),
        PipelineStep("build_team_competition_season", "transform", src_dir / "transform" / "build_team_competition_season.py"),
        PipelineStep("extract_clubelo_read_team_history", "transform", src_dir / "extract" / "extract_clubelo_read_team_history.py"),
        PipelineStep("build_matches", "transform", src_dir / "transform" / "build_matches.py"),
        PipelineStep("build_stadiums", "transform", src_dir / "transform" / "build_stadiums.py"),
        PipelineStep("build_weather_observations", "transform", src_dir / "transform" / "build_weather_observations.py"),
        PipelineStep("build_team_match_participation", "transform", src_dir / "transform" / "build_team_match_participation.py"),
        PipelineStep("build_elo_history", "transform", src_dir / "transform" / "build_elo_history.py"),
        PipelineStep("normalize_players", "transform", src_dir / "transform" / "normalize_players.py"),
        PipelineStep("build_players", "transform", src_dir / "transform" / "build_players.py"),
        PipelineStep("build_player_match_participation", "transform", src_dir / "transform" / "build_player_match_participation.py"),
        PipelineStep("build_player_competition_season_stats", "transform", src_dir / "transform" / "build_player_competition_season_stats.py"),
        PipelineStep("build_events", "transform", src_dir / "transform" / "build_events.py"),
        # RDF phase
        PipelineStep("rdf_competitions", "rdf", src_dir / "rdf" / "rdf_competitions.py"),
        PipelineStep("rdf_seasons", "rdf", src_dir / "rdf" / "rdf_seasons.py"),
        PipelineStep("rdf_teams", "rdf", src_dir / "rdf" / "rdf_teams.py"),
        PipelineStep("rdf_stadiums", "rdf", src_dir / "rdf" / "rdf_stadiums.py"),
        PipelineStep("rdf_matches", "rdf", src_dir / "rdf" / "rdf_matches.py"),
        PipelineStep("rdf_weather_observations", "rdf", src_dir / "rdf" / "rdf_weather_observations.py"),
        PipelineStep("rdf_team_match_participation", "rdf", src_dir / "rdf" / "rdf_team_match_participation.py"),
        PipelineStep("rdf_team_competition_season", "rdf", src_dir / "rdf" / "rdf_team_competition_season.py"),
        PipelineStep("rdf_elo_history", "rdf", src_dir / "rdf" / "rdf_elo_history.py"),
        PipelineStep("rdf_players", "rdf", src_dir / "rdf" / "rdf_players.py"),
        PipelineStep("rdf_player_match_participation", "rdf", src_dir / "rdf" / "rdf_player_match_participation.py"),
        PipelineStep("rdf_player_competition_season_stats", "rdf", src_dir / "rdf" / "rdf_player_competition_season_stats.py"),
        PipelineStep("rdf_events", "rdf", src_dir / "rdf" / "rdf_events.py"),
        # Merge phase
        PipelineStep("merge_ttl", "merge", src_dir / "rdf" / "merge_ttl.py"),
        # Validate phase
        PipelineStep("validate_player_normalization", "validate", src_dir / "validation" / "validate_player_normalization.py"),
        PipelineStep("validate_external_context", "validate", src_dir / "validation" / "validate_external_context.py"),
        PipelineStep("validate_ttl", "validate", src_dir / "validation" / "validate_ttl.py"),
        # Load phase
        PipelineStep("load_graphdb", "load", src_dir / "load" / "load_graphdb.py"),
    ]


def resolve_phases(args) -> list[str]:
    requested_phases = args.phases
    if "all" in requested_phases:
        return ["extract", "transform", "rdf", "merge", "validate", "load"]

    phase_order = ["extract", "transform", "rdf", "merge", "validate", "load"]
    return [phase for phase in phase_order if phase in requested_phases]


def select_steps(all_steps: list[PipelineStep], phases: list[str]) -> list[PipelineStep]:
    phase_order = ["extract", "transform", "rdf", "merge", "validate", "load"]
    ranked = {phase: i for i, phase in enumerate(phase_order)}

    selected = [step for step in all_steps if step.phase in phases]
    return sorted(selected, key=lambda step: ranked[step.phase])


def build_command(
    step: PipelineStep,
    *,
    league: str | None = None,
    season: str | None = None,
    events_rdf_chunk_size: int | None = None,
    clear_before_upload: bool = True,
) -> list[str]:
    cmd = [sys.executable, str(step.script_path)]

    if step.phase == "extract" and league and season:
        cmd.extend(["--leagues", league, "--seasons", season])
    elif step.name == "extract_clubelo_read_team_history" and league and season:
        cmd.extend(["--leagues", league, "--seasons", season])

    if step.name == "rdf_events" and events_rdf_chunk_size is not None:
        cmd.extend(["--events-rdf-chunk-size", str(events_rdf_chunk_size)])
    elif step.name == "load_graphdb" and not clear_before_upload:
        cmd.append("--no-clear-before-upload")

    return cmd


def build_step_env(leagues: list[str], seasons: list[str], *, enable_scope: bool = True) -> dict[str, str]:
    if bool(leagues) != bool(seasons):
        raise ValueError("leagues and seasons must be provided together.")

    env = os.environ.copy()
    env.pop("SOCCERDATA_PIPELINE_LEAGUES", None)
    env.pop("SOCCERDATA_PIPELINE_SEASONS", None)
    if enable_scope and leagues and seasons:
        env["SOCCERDATA_PIPELINE_LEAGUES"] = json.dumps(leagues, ensure_ascii=False)
        env["SOCCERDATA_PIPELINE_SEASONS"] = json.dumps(seasons, ensure_ascii=False)
    existing_pythonpath = env.get("PYTHONPATH")
    pythonpath_parts = [str(PROJECT_ROOT)]
    if existing_pythonpath:
        pythonpath_parts.append(existing_pythonpath)
    env["PYTHONPATH"] = os.pathsep.join(pythonpath_parts)
    return env


def load_script_result(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open(encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception as exc:
        return {"warnings": [f"No se pudo leer resultado estructurado del script: {exc}"]}
    return payload if isinstance(payload, dict) else {}


def build_temp_task_result_path() -> Path:
    return Path(tempfile.gettempdir()) / f"soccerdata_task_result_{uuid.uuid4().hex}.json"


def remove_temp_task_result(path: Path) -> None:
    if not path.exists():
        return
    try:
        path.unlink()
    except OSError:
        pass


def merge_script_payload(
    *,
    payload: dict[str, Any],
    metrics: dict[str, Any],
    warnings: list[str],
    errors: list[str],
) -> TaskStatus | None:
    if not payload:
        return None
    status = payload.get("status")
    if isinstance(payload.get("metrics"), dict):
        metrics.update(payload["metrics"])
    if isinstance(payload.get("warnings"), list):
        warnings.extend(str(value) for value in payload["warnings"])
    if isinstance(payload.get("errors"), list):
        errors.extend(str(value) for value in payload["errors"])
    if status in {TaskStatus.OK.value, TaskStatus.WARNING.value, TaskStatus.ERROR.value}:
        return TaskStatus(status)
    return None


def print_plan(steps: list[PipelineStep]) -> None:
    print("Plan de ejecucion:")
    for index, step in enumerate(steps, start=1):
        relative_path = step.script_path.relative_to(PROJECT_ROOT)
        print(f"  {index:02d}. [{step.phase}] {step.name} -> {relative_path}")
    print()


def resolved_paths(
    spec: TaskSpec,
    *,
    league: str | None = None,
    season: str | None = None,
    output: bool,
) -> list[Path]:
    artifacts = spec.output_artifacts if output else spec.input_artifacts
    return [artifact.resolve(PROJECT_ROOT, league=league, season=season) for artifact in artifacts]


def dependency_skip_reason(spec: TaskSpec, blocked_tasks: dict[str, str]) -> str | None:
    blockers = [dependency for dependency in spec.depends_on if dependency in blocked_tasks]
    if not blockers:
        return None
    details = "; ".join(f"{task_id}: {blocked_tasks[task_id]}" for task_id in blockers)
    return f"Dependencias bloqueantes no disponibles: {details}"


def mark_blocked(blocked_tasks: dict[str, str], result: TaskResult) -> None:
    if result.status == TaskStatus.ERROR:
        blocked_tasks[result.task_id] = "ERROR"
    elif result.status == TaskStatus.SKIPPED:
        blocked_tasks[result.task_id] = f"OMITIDO - {result.skip_reason or 'sin motivo registrado'}"


def run_step(
    step: PipelineStep,
    *,
    league: str | None = None,
    season: str | None = None,
    env: dict[str, str] | None = None,
    events_rdf_chunk_size: int | None = None,
    clear_before_upload: bool = True,
    task_result_path: Path | None = None,
) -> tuple[int, float, str | None, dict[str, Any]]:
    if not step.script_path.exists():
        raise FileNotFoundError(f"Expected script not found: {step.script_path}")

    cmd = build_command(
        step,
        league=league,
        season=season,
        events_rdf_chunk_size=events_rdf_chunk_size,
        clear_before_upload=clear_before_upload,
    )
    start = time.perf_counter()
    step_env = dict(env or os.environ.copy())
    if task_result_path is not None:
        step_env["SOCCERDATA_PIPELINE_MANAGED_STEP"] = "1"
        step_env["SOCCERDATA_TASK_RESULT_PATH"] = str(task_result_path)
        step_env["SOCCERDATA_TASK_ID"] = step.name
        step_env["SOCCERDATA_TASK_PHASE"] = step.phase
        if league:
            step_env["SOCCERDATA_TASK_LEAGUE"] = league
        if season:
            step_env["SOCCERDATA_TASK_SEASON"] = season

    completed = subprocess.run(
        cmd,
        cwd=PROJECT_ROOT,
        env=step_env,
        check=False,
    )
    elapsed = time.perf_counter() - start
    script_result = load_script_result(task_result_path) if task_result_path is not None else {}
    return completed.returncode, elapsed, " ".join(cmd), script_result


def execute_step(
    step: PipelineStep,
    *,
    spec: TaskSpec,
    blocked_tasks: dict[str, str],
    env: dict[str, str],
    events_rdf_chunk_size: int,
    clear_before_upload: bool,
    league: str | None = None,
    season: str | None = None,
) -> TaskResult:
    input_files = resolved_paths(spec, league=league, season=season, output=False)
    output_files = resolved_paths(spec, league=league, season=season, output=True)

    skip_reason = dependency_skip_reason(spec, blocked_tasks)
    if skip_reason:
        return skipped_result(
            task_id=step.name,
            phase=step.phase,
            league=league,
            season=season,
            skip_reason=skip_reason,
            input_files=input_files,
        )

    input_validation = validate_artifacts(
        spec.input_artifacts,
        project_root=PROJECT_ROOT,
        league=league,
        season=season,
    )
    if input_validation.errors:
        return skipped_result(
            task_id=step.name,
            phase=step.phase,
            league=league,
            season=season,
            skip_reason="Entradas no utilizables: " + " | ".join(input_validation.errors),
            input_files=input_files,
        )

    try:
        task_result_path = build_temp_task_result_path()
        return_code, elapsed, command, script_payload = run_step(
            step,
            league=league,
            season=season,
            env=env,
            events_rdf_chunk_size=events_rdf_chunk_size,
            clear_before_upload=clear_before_upload,
            task_result_path=task_result_path,
        )
        remove_temp_task_result(task_result_path)
    except Exception as exc:
        return error_result(
            task_id=step.name,
            phase=step.phase,
            duration_seconds=0.0,
            league=league,
            season=season,
            input_files=input_files,
            output_files=output_files,
            errors=[str(exc)],
            technical_exception=repr(exc),
        )

    metrics: dict[str, Any] = {"command": command}
    warnings = list(input_validation.warnings)
    if input_validation.metrics:
        metrics.update(input_validation.metrics)

    script_errors: list[str] = []
    script_status = merge_script_payload(
        payload=script_payload,
        metrics=metrics,
        warnings=warnings,
        errors=script_errors,
    )

    if return_code != 0:
        metrics["return_code"] = return_code
        return error_result(
            task_id=step.name,
            phase=step.phase,
            duration_seconds=elapsed,
            league=league,
            season=season,
            input_files=input_files,
            output_files=output_files,
            errors=script_errors or [f"El subproceso termino con codigo {return_code}"],
            metrics=metrics,
            technical_exception=script_payload.get("technical_exception") if script_payload else None,
        )

    if script_status == TaskStatus.ERROR:
        return error_result(
            task_id=step.name,
            phase=step.phase,
            duration_seconds=elapsed,
            league=league,
            season=season,
            input_files=input_files,
            output_files=output_files,
            errors=script_errors or ["El script declaro estado ERROR"],
            warnings=warnings,
            metrics=metrics,
            technical_exception=script_payload.get("technical_exception") if script_payload else None,
        )
    if script_status == TaskStatus.WARNING and not warnings:
        warnings.append("El script declaro estado WARNING")

    output_validation = validate_artifacts(
        spec.output_artifacts,
        project_root=PROJECT_ROOT,
        league=league,
        season=season,
    )
    metrics.update(output_validation.metrics)
    warnings.extend(output_validation.warnings)
    if output_validation.errors:
        return error_result(
            task_id=step.name,
            phase=step.phase,
            duration_seconds=elapsed,
            league=league,
            season=season,
            input_files=input_files,
            output_files=output_files,
            errors=["Salida no utilizable: " + " | ".join(output_validation.errors)],
            warnings=warnings,
            metrics=metrics,
        )

    return ok_result(
        task_id=step.name,
        phase=step.phase,
        duration_seconds=elapsed,
        league=league,
        season=season,
        input_files=input_files,
        output_files=output_files,
        warnings=warnings,
        metrics=metrics,
    )


def print_step_result(result: TaskResult) -> None:
    print()
    scope = ""
    if result.league and result.season:
        scope = f" [{result.league} / {result.season}]"
    if result.status == TaskStatus.OK:
        print(f"PASO [OK]{scope} finalizado en {result.duration_seconds:.2f}s")
    elif result.status == TaskStatus.WARNING:
        print(f"PASO [AVISO]{scope} finalizado en {result.duration_seconds:.2f}s")
    elif result.status == TaskStatus.ERROR:
        print(f"PASO [ERROR]{scope} fallido en {result.duration_seconds:.2f}s")
        for error in result.errors:
            print(f"  - {error}")
    else:
        print(f"PASO [OMITIDO]{scope}: {result.skip_reason}")
    print()


def run_and_record(
    audit: PipelineAudit,
    blocked_tasks: dict[str, str],
    step: PipelineStep,
    *,
    env: dict[str, str],
    events_rdf_chunk_size: int,
    clear_before_upload: bool,
    league: str | None = None,
    season: str | None = None,
) -> None:
    spec = TASK_REGISTRY[step.name]
    print(SEPARATOR)
    print(f"Ejecutando paso: {step.name} ({step.phase})")
    print()
    result = execute_step(
        step,
        spec=spec,
        blocked_tasks=blocked_tasks,
        env=env,
        events_rdf_chunk_size=events_rdf_chunk_size,
        clear_before_upload=clear_before_upload,
        league=league,
        season=season,
    )
    audit.add(result)
    mark_blocked(blocked_tasks, result)
    print_step_result(result)


def phases_using_scope(phases: list[str]) -> list[str]:
    return [phase for phase in phases if phase in PHASES_USING_SCOPE]


def phases_ignoring_scope(phases: list[str]) -> list[str]:
    return [phase for phase in phases if phase in PHASES_IGNORING_SCOPE]


def print_header(args, phases: list[str], steps: list[PipelineStep]) -> None:
    print()
    print("Ejecutor del pipeline SoccerData")
    print(SEPARATOR)
    print(f"Raiz del proyecto: {format_path(PROJECT_ROOT)}")
    print(f"Fases seleccionadas: {', '.join(phases)}")
    scope_provided = bool(args.leagues and args.seasons)
    scope_used_by = phases_using_scope(phases)
    scope_ignored_by = phases_ignoring_scope(phases)
    if scope_provided:
        print(f"Ligas: {', '.join(args.leagues)}")
        print(f"Temporadas: {', '.join(args.seasons)}")
        if scope_used_by:
            print(f"Alcance aplicado a fase(s): {', '.join(scope_used_by)}")
            if scope_ignored_by:
                print(f"Nota de alcance: ignorado por fase(s): {', '.join(scope_ignored_by)}")
        else:
            print(
                "Aviso: se indicaron ligas/temporadas, pero las fases seleccionadas no usan "
                f"alcance del pipeline: {', '.join(scope_ignored_by)}"
            )
    else:
        print("Alcance: no indicado")
        if scope_used_by:
            print(
                "Nota de alcance: las fases seleccionadas se ejecutaran globalmente donde sea compatible: "
                f"{', '.join(scope_used_by)}"
            )
        else:
            print("Nota de alcance: las fases seleccionadas no requieren ligas/temporadas.")
    if "rdf" in phases:
        print(f"Tamano de bloque RDF de eventos: {args.events_rdf_chunk_size}")
    if "load" in phases:
        print(f"Limpiar GraphDB antes de cargar: {args.clear_before_upload}")
    if "transform" in phases:
        print("Modo transform: reconstruccion acotada desde archivos raw que coinciden con las ligas/temporadas")
    print()
    print_plan(steps)


def main() -> None:
    args = parse_pipeline_args()
    phases = resolve_phases(args)
    all_steps = build_steps()
    steps = select_steps(all_steps, phases)
    leagues_to_run = args.leagues
    seasons_to_run = args.seasons
    step_env = build_step_env(
        leagues_to_run,
        seasons_to_run,
        enable_scope=bool(phases_using_scope(phases)),
    )

    if not steps:
        raise ValueError("No pipeline steps selected.")

    missing_specs = [step.name for step in steps if step.name not in TASK_REGISTRY]
    if missing_specs:
        raise RuntimeError(f"No hay TaskSpec registrado para: {', '.join(missing_specs)}")

    print_header(args, phases, steps)

    if args.dry_run:
        return

    audit = PipelineAudit(
        project_root=PROJECT_ROOT,
        phases=phases,
        leagues=leagues_to_run,
        seasons=seasons_to_run,
        ambiguous_dependencies=AMBIGUOUS_DEPENDENCIES,
    )
    blocked_tasks: dict[str, str] = {}
    total_start = time.perf_counter()

    extract_steps = [step for step in steps if step.phase == "extract"]
    transform_steps = [step for step in steps if step.phase == "transform"]
    rdf_steps = [step for step in steps if step.phase == "rdf"]
    merge_steps = [step for step in steps if step.phase == "merge"]
    validate_steps = [step for step in steps if step.phase == "validate"]
    load_steps = [step for step in steps if step.phase == "load"]

    if extract_steps:
        print(SEPARATOR)
        print("BLOQUE EXTRACT")
        print(SEPARATOR)
        print()

        for league in leagues_to_run:
            for season in seasons_to_run:
                print(SEPARATOR)
                print(f"Extraccion liga/temporada: {league} / {season}")
                print(SEPARATOR)
                print()

                for step in extract_steps:
                    run_and_record(
                        audit,
                        blocked_tasks,
                        step,
                        league=league,
                        season=season,
                        env=step_env,
                        events_rdf_chunk_size=args.events_rdf_chunk_size,
                        clear_before_upload=args.clear_before_upload,
                    )

    if transform_steps:
        print(SEPARATOR)
        print("BLOQUE TRANSFORM")
        print(SEPARATOR)
        print()
        print("Modo acotado: cada transform reconstruye y sobrescribe su propia salida canonica.")
        print()

        for step in transform_steps:
            if step.name == "extract_clubelo_read_team_history":
                for league in leagues_to_run:
                    for season in seasons_to_run:
                        print(SEPARATOR)
                        print(f"Transformacion liga/temporada (ClubElo): {league} / {season}")
                        print(SEPARATOR)
                        run_and_record(
                            audit,
                            blocked_tasks,
                            step,
                            league=league,
                            season=season,
                            env=step_env,
                            events_rdf_chunk_size=args.events_rdf_chunk_size,
                            clear_before_upload=args.clear_before_upload,
                        )
                continue

            run_and_record(
                audit,
                blocked_tasks,
                step,
                env=step_env,
                events_rdf_chunk_size=args.events_rdf_chunk_size,
                clear_before_upload=args.clear_before_upload,
            )

    def run_global_phase(phase_name: str, phase_steps: list[PipelineStep]) -> None:
        if not phase_steps:
            return

        print(SEPARATOR)
        print(f"BLOQUE {phase_name.upper()}")
        print(SEPARATOR)
        print()

        for step in phase_steps:
            run_and_record(
                audit,
                blocked_tasks,
                step,
                env=step_env,
                events_rdf_chunk_size=args.events_rdf_chunk_size,
                clear_before_upload=args.clear_before_upload,
            )

    run_global_phase("rdf", rdf_steps)
    run_global_phase("merge", merge_steps)
    run_global_phase("validate", validate_steps)
    run_global_phase("load", load_steps)

    total_elapsed = time.perf_counter() - total_start
    report_path = audit.write_json()
    print(SEPARATOR)
    print()
    print(f"Informe de auditoria: {format_path(report_path)}")

    if audit.has_blocking_failure():
        print(f"Pipeline finalizado con incidencias bloqueantes en {total_elapsed:.2f}s")
        print(f"Resumen: {audit.summary()}")
        raise SystemExit(1)

    print(f"Pipeline finalizado correctamente en {total_elapsed:.2f}s")
    print(f"Resumen: {audit.summary()}")


if __name__ == "__main__":
    main()
