from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from artifact_validation import (
    ValidationResult,
    validate_csv_file,
    validate_existing_file,
    validate_json_file,
    validate_ttl_file,
)


AMBIGUOUS_DEPENDENCIES = [
    (
        "Las transformaciones canonicas consumen varias fuentes raw con reglas de fallback internas. "
        "En esta iteracion se bloquean solo por las salidas raw principales declaradas; las relaciones "
        "fuente-a-columna mas finas quedan pendientes."
    ),
    (
        "Las validaciones globales validate_player_normalization y validate_external_context cubren "
        "relaciones cruzadas amplias. Aqui dependen de sus CSV principales, sin modelar cada relacion "
        "interna para evitar un grafo demasiado fragil."
    ),
]


@dataclass(frozen=True)
class ArtifactSpec:
    path_template: str
    kind: str = "csv"
    required_columns: tuple[str, ...] = ()
    id_columns: tuple[str, ...] = ()
    allow_empty: bool = False

    def resolve(self, project_root: Path, league: str | None = None, season: str | None = None) -> Path:
        values = {
            "league": _file_scope_fragment(league or ""),
            "season": _file_scope_fragment(season or ""),
        }
        return project_root / self.path_template.format(**values)


@dataclass(frozen=True)
class TaskSpec:
    task_id: str
    phase: str
    input_artifacts: tuple[ArtifactSpec, ...] = ()
    output_artifacts: tuple[ArtifactSpec, ...] = ()
    depends_on: tuple[str, ...] = ()
    per_scope: bool = False


def _file_scope_fragment(value: str) -> str:
    return str(value).replace(" ", "_").replace("/", "-")


def raw_csv(source: str, dataset: str, prefix: str, *, allow_empty: bool = False) -> ArtifactSpec:
    return ArtifactSpec(
        f"data/raw/{source}/{dataset}/{prefix}_{{league}}_{{season}}.csv",
        allow_empty=allow_empty,
    )


def canonical_csv(
    filename: str,
    *,
    required_columns: tuple[str, ...] = (),
    id_columns: tuple[str, ...] = (),
    allow_empty: bool = False,
) -> ArtifactSpec:
    return ArtifactSpec(
        f"data/processed/canonical/{filename}",
        required_columns=required_columns,
        id_columns=id_columns,
        allow_empty=allow_empty,
    )


def ttl(filename: str) -> ArtifactSpec:
    return ArtifactSpec(f"data/ttl/{filename}", kind="ttl")


def presence_only(artifact: ArtifactSpec) -> ArtifactSpec:
    return ArtifactSpec(artifact.path_template, kind="file")


RAW_OUTPUTS: dict[str, ArtifactSpec] = {
    "extract_espn_read_lineup": raw_csv("espn", "lineup", "read_lineup"),
    "extract_espn_read_matchsheet": raw_csv("espn", "matchsheet", "read_matchsheet"),
    "extract_matchhistory_read_games": raw_csv("matchhistory", "games", "read_games"),
    "extract_sofascore_read_league_table": raw_csv("sofascore", "league_table", "read_league_table"),
    "extract_sofascore_read_schedule": raw_csv("sofascore", "schedule", "read_schedule"),
    "extract_understat_read_player_match_stats": raw_csv("understat", "player_match_stats", "read_player_match_stats"),
    "extract_understat_read_player_season_stats": raw_csv("understat", "player_season_stats", "read_player_season_stats"),
    "extract_understat_read_schedule": raw_csv("understat", "schedule", "read_schedule"),
    "extract_understat_read_team_match_stats": raw_csv("understat", "team_match_stats", "read_team_match_stats"),
    "extract_whoscored_read_events": raw_csv("whoscored", "events", "read_events"),
    "extract_whoscored_read_missing_players": raw_csv(
        "whoscored",
        "missing_players",
        "read_missing_players",
        allow_empty=True,
    ),
    "extract_whoscored_read_schedule": raw_csv("whoscored", "schedule", "read_schedule"),
    "extract_clubelo_read_team_history": raw_csv("clubelo", "team_history", "read_team_history"),
}


CANONICAL_OUTPUTS: dict[str, ArtifactSpec] = {
    "build_competitions": canonical_csv(
        "competitions.csv",
        required_columns=("id_competition", "name"),
        id_columns=("id_competition",),
    ),
    "build_seasons": canonical_csv(
        "seasons.csv",
        required_columns=("id_season", "name"),
        id_columns=("id_season",),
    ),
    "build_teams": canonical_csv(
        "teams.csv",
        required_columns=("id_team", "name"),
        id_columns=("id_team",),
    ),
    "build_team_competition_season": canonical_csv(
        "team_competition_season.csv",
        required_columns=("id_teamCompetitionSeason", "id_team", "id_competition", "id_season"),
        id_columns=("id_teamCompetitionSeason",),
    ),
    "build_matches": canonical_csv(
        "matches.csv",
        required_columns=("id_match", "id_competition", "id_season", "id_home_team", "id_away_team", "matchStatus"),
        id_columns=("id_match",),
    ),
    "build_stadiums": canonical_csv(
        "stadiums.csv",
        required_columns=("id_stadium", "name"),
        id_columns=("id_stadium",),
    ),
    "build_weather_observations": canonical_csv(
        "weather_observations.csv",
        required_columns=("id_weatherObservation", "id_match", "dateTime"),
        id_columns=("id_weatherObservation",),
    ),
    "build_team_match_participation": canonical_csv(
        "team_match_participation.csv",
        required_columns=("id_teamMatchParticipation", "id_match", "id_team"),
        id_columns=("id_teamMatchParticipation",),
    ),
    "build_elo_history": canonical_csv(
        "elo_history.csv",
        required_columns=("id_eloRecord", "id_team", "dateFrom"),
        id_columns=("id_eloRecord",),
    ),
    "build_players": canonical_csv(
        "players.csv",
        required_columns=("id_player", "knownAs", "fullName"),
        id_columns=("id_player",),
    ),
    "build_player_match_participation": canonical_csv(
        "player_match_participation.csv",
        required_columns=("id_playerMatchParticipation", "id_match", "id_player"),
        id_columns=("id_playerMatchParticipation",),
    ),
    "build_player_competition_season_stats": canonical_csv(
        "player_competition_season_stats.csv",
        required_columns=("id_playerCompetitionSeasonStats", "id_player", "id_competition", "id_season"),
        id_columns=("id_playerCompetitionSeasonStats",),
    ),
    "build_events": canonical_csv(
        "events.csv",
        required_columns=("id_event", "id_match", "id_team"),
        id_columns=("id_event",),
    ),
}


NORMALIZATION_OUTPUTS = (
    ArtifactSpec(
        "data/processed/normalization/player_identities.csv",
        required_columns=("id_player", "source_player_keys"),
        id_columns=("id_player",),
    ),
    ArtifactSpec(
        "data/processed/normalization/player_alias_map.csv",
        required_columns=("source_player_key", "id_player"),
        id_columns=("source_player_key",),
    ),
    ArtifactSpec("data/processed/normalization/audit/player_normalization_report.json", kind="json"),
)


TTL_OUTPUTS: dict[str, ArtifactSpec] = {
    "rdf_competitions": ttl("competitions.ttl"),
    "rdf_seasons": ttl("seasons.ttl"),
    "rdf_teams": ttl("teams.ttl"),
    "rdf_stadiums": ttl("stadiums.ttl"),
    "rdf_matches": ttl("matches.ttl"),
    "rdf_weather_observations": ttl("weather_observations.ttl"),
    "rdf_team_match_participation": ttl("team_match_participation.ttl"),
    "rdf_team_competition_season": ttl("team_competition_season.ttl"),
    "rdf_elo_history": ttl("elo_history.ttl"),
    "rdf_players": ttl("players.ttl"),
    "rdf_player_match_participation": ttl("player_match_participation.ttl"),
    "rdf_player_competition_season_stats": ttl("player_competition_season_stats.ttl"),
    "rdf_events": ttl("events.ttl"),
    "merge_ttl": ttl("full_knowledge_graph.ttl"),
}


RDF_INPUT_BY_TASK = {
    "rdf_competitions": CANONICAL_OUTPUTS["build_competitions"],
    "rdf_seasons": CANONICAL_OUTPUTS["build_seasons"],
    "rdf_teams": CANONICAL_OUTPUTS["build_teams"],
    "rdf_stadiums": CANONICAL_OUTPUTS["build_stadiums"],
    "rdf_matches": CANONICAL_OUTPUTS["build_matches"],
    "rdf_weather_observations": CANONICAL_OUTPUTS["build_weather_observations"],
    "rdf_team_match_participation": CANONICAL_OUTPUTS["build_team_match_participation"],
    "rdf_team_competition_season": CANONICAL_OUTPUTS["build_team_competition_season"],
    "rdf_elo_history": CANONICAL_OUTPUTS["build_elo_history"],
    "rdf_players": CANONICAL_OUTPUTS["build_players"],
    "rdf_player_match_participation": CANONICAL_OUTPUTS["build_player_match_participation"],
    "rdf_player_competition_season_stats": CANONICAL_OUTPUTS["build_player_competition_season_stats"],
    "rdf_events": CANONICAL_OUTPUTS["build_events"],
}


def build_task_registry() -> dict[str, TaskSpec]:
    registry: dict[str, TaskSpec] = {}

    for task_id, artifact in RAW_OUTPUTS.items():
        registry[task_id] = TaskSpec(
            task_id=task_id,
            phase="extract" if task_id != "extract_clubelo_read_team_history" else "transform",
            output_artifacts=(artifact,),
            per_scope=True,
        )

    registry.update(
        {
            "build_competitions": TaskSpec(
                "build_competitions",
                "transform",
                output_artifacts=(CANONICAL_OUTPUTS["build_competitions"],),
                depends_on=("extract_sofascore_read_schedule", "extract_understat_read_schedule"),
            ),
            "build_seasons": TaskSpec(
                "build_seasons",
                "transform",
                output_artifacts=(CANONICAL_OUTPUTS["build_seasons"],),
                depends_on=("extract_sofascore_read_schedule", "extract_understat_read_schedule"),
            ),
            "build_teams": TaskSpec(
                "build_teams",
                "transform",
                output_artifacts=(CANONICAL_OUTPUTS["build_teams"],),
                depends_on=(
                    "extract_sofascore_read_league_table",
                    "extract_sofascore_read_schedule",
                    "extract_understat_read_schedule",
                    "extract_whoscored_read_schedule",
                ),
            ),
            "build_team_competition_season": TaskSpec(
                "build_team_competition_season",
                "transform",
                output_artifacts=(CANONICAL_OUTPUTS["build_team_competition_season"],),
                depends_on=("extract_sofascore_read_league_table",),
            ),
            "extract_clubelo_read_team_history": TaskSpec(
                "extract_clubelo_read_team_history",
                "transform",
                input_artifacts=(
                    CANONICAL_OUTPUTS["build_team_competition_season"],
                    CANONICAL_OUTPUTS["build_teams"],
                ),
                output_artifacts=(RAW_OUTPUTS["extract_clubelo_read_team_history"],),
                depends_on=("build_team_competition_season", "build_teams"),
                per_scope=True,
            ),
            "build_matches": TaskSpec(
                "build_matches",
                "transform",
                output_artifacts=(CANONICAL_OUTPUTS["build_matches"],),
                depends_on=("extract_sofascore_read_schedule",),
            ),
            "build_stadiums": TaskSpec(
                "build_stadiums",
                "transform",
                input_artifacts=(CANONICAL_OUTPUTS["build_matches"],),
                output_artifacts=(CANONICAL_OUTPUTS["build_matches"], CANONICAL_OUTPUTS["build_stadiums"]),
                depends_on=("build_matches",),
            ),
            "build_weather_observations": TaskSpec(
                "build_weather_observations",
                "transform",
                input_artifacts=(CANONICAL_OUTPUTS["build_matches"], CANONICAL_OUTPUTS["build_stadiums"]),
                output_artifacts=(CANONICAL_OUTPUTS["build_weather_observations"],),
                depends_on=("build_matches", "build_stadiums"),
            ),
            "build_team_match_participation": TaskSpec(
                "build_team_match_participation",
                "transform",
                input_artifacts=(CANONICAL_OUTPUTS["build_matches"],),
                output_artifacts=(CANONICAL_OUTPUTS["build_team_match_participation"],),
                depends_on=("build_matches",),
            ),
            "build_elo_history": TaskSpec(
                "build_elo_history",
                "transform",
                output_artifacts=(CANONICAL_OUTPUTS["build_elo_history"],),
                depends_on=("extract_clubelo_read_team_history",),
            ),
            "normalize_players": TaskSpec(
                "normalize_players",
                "transform",
                output_artifacts=NORMALIZATION_OUTPUTS,
                depends_on=(
                    "extract_understat_read_player_match_stats",
                    "extract_understat_read_player_season_stats",
                    "extract_whoscored_read_events",
                    "extract_espn_read_lineup",
                    "build_team_competition_season",
                ),
            ),
            "build_players": TaskSpec(
                "build_players",
                "transform",
                input_artifacts=NORMALIZATION_OUTPUTS[:1],
                output_artifacts=(CANONICAL_OUTPUTS["build_players"],),
                depends_on=("normalize_players",),
            ),
            "build_player_match_participation": TaskSpec(
                "build_player_match_participation",
                "transform",
                input_artifacts=(CANONICAL_OUTPUTS["build_matches"], *NORMALIZATION_OUTPUTS[:2]),
                output_artifacts=(CANONICAL_OUTPUTS["build_player_match_participation"],),
                depends_on=("build_matches", "normalize_players"),
            ),
            "build_player_competition_season_stats": TaskSpec(
                "build_player_competition_season_stats",
                "transform",
                input_artifacts=NORMALIZATION_OUTPUTS[:2],
                output_artifacts=(CANONICAL_OUTPUTS["build_player_competition_season_stats"],),
                depends_on=("extract_understat_read_player_season_stats", "normalize_players"),
            ),
            "build_events": TaskSpec(
                "build_events",
                "transform",
                input_artifacts=(CANONICAL_OUTPUTS["build_matches"], *NORMALIZATION_OUTPUTS[:2]),
                output_artifacts=(CANONICAL_OUTPUTS["build_events"],),
                depends_on=("build_matches", "normalize_players", "extract_whoscored_read_events"),
            ),
        }
    )

    for task_id, csv_input in RDF_INPUT_BY_TASK.items():
        registry[task_id] = TaskSpec(
            task_id=task_id,
            phase="rdf",
            input_artifacts=(csv_input,),
            output_artifacts=(presence_only(TTL_OUTPUTS[task_id]),),
            depends_on=(_producer_for_csv(csv_input),),
        )

    registry["rdf_events"] = TaskSpec(
        task_id="rdf_events",
        phase="rdf",
        input_artifacts=(
            CANONICAL_OUTPUTS["build_events"],
            CANONICAL_OUTPUTS["build_player_match_participation"],
        ),
        output_artifacts=(presence_only(TTL_OUTPUTS["rdf_events"]),),
        depends_on=("build_events", "build_player_match_participation"),
    )

    registry["merge_ttl"] = TaskSpec(
        "merge_ttl",
        "merge",
        input_artifacts=tuple(presence_only(TTL_OUTPUTS[task_id]) for task_id in RDF_INPUT_BY_TASK),
        output_artifacts=(presence_only(TTL_OUTPUTS["merge_ttl"]),),
        depends_on=tuple(RDF_INPUT_BY_TASK),
    )
    registry["validate_player_normalization"] = TaskSpec(
        "validate_player_normalization",
        "validate",
        input_artifacts=(
            CANONICAL_OUTPUTS["build_players"],
            CANONICAL_OUTPUTS["build_player_match_participation"],
            CANONICAL_OUTPUTS["build_player_competition_season_stats"],
            CANONICAL_OUTPUTS["build_events"],
            *NORMALIZATION_OUTPUTS[:2],
        ),
        depends_on=(
            "build_players",
            "build_player_match_participation",
            "build_player_competition_season_stats",
            "build_events",
        ),
    )
    registry["validate_external_context"] = TaskSpec(
        "validate_external_context",
        "validate",
        input_artifacts=(
            CANONICAL_OUTPUTS["build_matches"],
            CANONICAL_OUTPUTS["build_stadiums"],
            CANONICAL_OUTPUTS["build_weather_observations"],
            CANONICAL_OUTPUTS["build_team_match_participation"],
        ),
        depends_on=("build_matches", "build_stadiums", "build_weather_observations", "build_team_match_participation"),
    )
    registry["validate_ttl"] = TaskSpec(
        "validate_ttl",
        "validate",
        input_artifacts=(
            *tuple(presence_only(TTL_OUTPUTS[task_id]) for task_id in RDF_INPUT_BY_TASK),
            presence_only(TTL_OUTPUTS["merge_ttl"]),
        ),
        depends_on=("merge_ttl",),
    )
    registry["load_graphdb"] = TaskSpec(
        "load_graphdb",
        "load",
        input_artifacts=(presence_only(TTL_OUTPUTS["merge_ttl"]),),
        depends_on=("validate_player_normalization", "validate_external_context", "validate_ttl"),
    )

    return registry


def _producer_for_csv(artifact: ArtifactSpec) -> str:
    for task_id, output in CANONICAL_OUTPUTS.items():
        if output.path_template == artifact.path_template:
            return task_id
    raise KeyError(f"No producer declared for {artifact.path_template}")


def validate_artifacts(
    artifacts: tuple[ArtifactSpec, ...],
    *,
    project_root: Path,
    league: str | None = None,
    season: str | None = None,
) -> ValidationResult:
    result = ValidationResult()
    for artifact in artifacts:
        path = artifact.resolve(project_root, league=league, season=season)
        if artifact.kind == "csv":
            validation = validate_csv_file(
                path,
                label=path.name,
                required_columns=artifact.required_columns,
                id_columns=artifact.id_columns,
                allow_empty=artifact.allow_empty,
            )
        elif artifact.kind == "ttl":
            validation = validate_ttl_file(path, label=path.name)
        elif artifact.kind == "json":
            validation = validate_json_file(path, label=path.name)
        elif artifact.kind == "file":
            validation = validate_existing_file(path, label=path.name)
        else:
            validation = ValidationResult(errors=[f"Tipo de artefacto no soportado: {artifact.kind} ({path})"])
        result.merge(validation)
    return result


TASK_REGISTRY = build_task_registry()
