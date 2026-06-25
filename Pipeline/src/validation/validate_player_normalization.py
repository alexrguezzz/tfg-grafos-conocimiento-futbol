from __future__ import annotations

import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(PROJECT_ROOT))

from src.validation.validation_args import parse_no_args  # noqa: E402

_ARGS = parse_no_args("Valida salidas canonicas de normalizacion de jugadores.") if __name__ == "__main__" else None

import pandas as pd  # noqa: E402

from src.pipeline.console_output import (  # noqa: E402
    print_audit,
    print_examples,
    print_metric,
    print_validation_ok,
    print_warning,
)
from src.transform.player_normalization import (  # noqa: E402
    PLAYER_ALIAS_MAP_PATH,
    PLAYER_IDENTITIES_PATH,
    PLAYER_REPORT_PATH,
    PLAYER_REVIEW_QUEUE_PATH,
    configure_name_quality_from_observations,
    is_valid_player_id_for_full_name,
    is_poor_display_name,
    unjustified_duplicate_full_name_rows,
)
from src.pipeline.script_result import run_with_optional_task_result  # noqa: E402
from src.utils.text_normalization import clean_identifier_text  # noqa: E402
from src.utils.season_scope import filter_target_seasons, path_has_target_scope  # noqa: E402


CANONICAL_DIR = PROJECT_ROOT / "data" / "processed" / "canonical"
RAW_WHOSCORED_EVENTS_DIR = PROJECT_ROOT / "data" / "raw" / "whoscored" / "events"


def read_csv(path: Path, **kwargs) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"No existe: {path}")
    return pd.read_csv(path, dtype="string", **kwargs)


def assert_no_missing(label: str, missing: set[str]) -> None:
    if missing:
        sample = sorted(missing)[:20]
        raise ValueError(f"{label}: {len(missing)} referencia(s) inexistente(s). Ejemplos: {sample}")


def non_empty_set(series: pd.Series) -> set[str]:
    return {
        clean_text(value)
        for value in series.dropna()
        if clean_text(value)
    }


def clean_text(value: object) -> str:
    if pd.isna(value):
        return ""
    text = str(value).strip()
    return "" if not text or text.lower() in {"nan", "<na>"} else text


def split_multi_value(value: object) -> list[str]:
    text = clean_text(value)
    return [
        part.strip()
        for part in text.split("|")
        if part.strip() and part.strip().lower() not in {"nan", "<na>"}
    ]


def assert_columns(label: str, df: pd.DataFrame, columns: set[str]) -> None:
    missing = columns - set(df.columns)
    if missing:
        raise ValueError(f"Faltan columnas en {label}: {sorted(missing)}")


def non_empty_mask(series: pd.Series) -> pd.Series:
    return series.apply(lambda value: bool(clean_text(value)))


def example_rows(df: pd.DataFrame, mask: pd.Series, columns: list[str]) -> list[dict[str, object]]:
    available_columns = [column for column in columns if column in df.columns]
    return df.loc[mask, available_columns].head(20).to_dict(orient="records")


def numeric_range_error(
    label: str,
    df: pd.DataFrame,
    column: str,
    *,
    example_columns: list[str],
    minimum: float | None = None,
    maximum: float | None = None,
    integer: bool = False,
) -> str | None:
    present = non_empty_mask(df[column])
    values = pd.to_numeric(df[column], errors="coerce")
    invalid_numeric = present & values.isna()
    if invalid_numeric.any():
        return (
            f"{label}: {column} debe ser numerico. "
            f"Ejemplos: {example_rows(df, invalid_numeric, [*example_columns, column])}"
        )

    valid_range = present & values.notna()
    range_parts: list[str] = []
    if minimum is not None:
        valid_range = valid_range & values.ge(minimum)
        range_parts.append(f">= {minimum:g}")
    if maximum is not None:
        valid_range = valid_range & values.le(maximum)
        range_parts.append(f"<= {maximum:g}")
    invalid_range = present & values.notna() & ~valid_range
    if invalid_range.any():
        expected = " y ".join(range_parts)
        return (
            f"{label}: {column} fuera de rango ({expected}). "
            f"Ejemplos: {example_rows(df, invalid_range, [*example_columns, column])}"
        )

    if integer:
        non_integer = present & values.notna() & values.mod(1).abs().gt(1e-9)
        if non_integer.any():
            return (
                f"{label}: {column} debe ser entero. "
                f"Ejemplos: {example_rows(df, non_integer, [*example_columns, column])}"
            )
    return None


def numeric_suspicion_warning(
    label: str,
    df: pd.DataFrame,
    column: str,
    *,
    example_columns: list[str],
    above: float,
) -> str | None:
    present = non_empty_mask(df[column])
    values = pd.to_numeric(df[column], errors="coerce")
    suspicious = present & values.notna() & values.gt(above)
    if not suspicious.any():
        return None
    return (
        f"{label}: {column} tiene valores sospechosos (> {above:g}). "
        f"Ejemplos: {example_rows(df, suspicious, [*example_columns, column])}"
    )


TRUE_VALUES = {"true", "1", "yes", "y", "si", "sí"}
FALSE_VALUES = {"false", "0", "no", "n"}


def boolean_parse_error(label: str, df: pd.DataFrame, column: str, *, example_columns: list[str]) -> str | None:
    present = non_empty_mask(df[column])
    normalized = df[column].astype("string").str.strip().str.lower()
    invalid = present & ~normalized.isin(TRUE_VALUES | FALSE_VALUES)
    if not invalid.any():
        return None
    return (
        f"{label}: {column} debe ser booleano parseable. "
        f"Ejemplos: {example_rows(df, invalid, [*example_columns, column])}"
    )


def substitution_minute_error(label: str, df: pd.DataFrame, column: str, *, example_columns: list[str]) -> str | None:
    present = non_empty_mask(df[column])
    normalized = df[column].astype("string").str.strip().str.lower()
    start_value = normalized.eq("start")
    numeric_candidate = present & ~start_value
    values = pd.to_numeric(df[column], errors="coerce")
    invalid_numeric = numeric_candidate & values.isna()
    if invalid_numeric.any():
        return (
            f"{label}: {column} debe ser minuto numerico o 'Start'. "
            f"Ejemplos: {example_rows(df, invalid_numeric, [*example_columns, column])}"
        )
    invalid_range = numeric_candidate & values.notna() & values.lt(0)
    if invalid_range.any():
        return (
            f"{label}: {column} debe ser >= 0 cuando contiene minutos. "
            f"Ejemplos: {example_rows(df, invalid_range, [*example_columns, column])}"
        )
    return None


def allowed_value_error(
    label: str,
    df: pd.DataFrame,
    column: str,
    *,
    allowed_values: set[str],
    example_columns: list[str],
) -> str | None:
    present = non_empty_mask(df[column])
    normalized = df[column].astype("string").str.strip().str.lower()
    invalid = present & ~normalized.isin(allowed_values)
    if not invalid.any():
        return None
    return (
        f"{label}: {column} contiene valores no esperados ({sorted(allowed_values)}). "
        f"Ejemplos: {example_rows(df, invalid, [*example_columns, column])}"
    )


def add_warning(warnings: list[str], message: str | None) -> None:
    if not message:
        return
    warnings.append(message)
    print_warning(message)


def validate_event_count(events: pd.DataFrame) -> None:
    if not PLAYER_REPORT_PATH.exists():
        return
    with PLAYER_REPORT_PATH.open(encoding="utf-8") as handle:
        report = json.load(handle)
    raw_expected = int(report.get("raw_whoscored_event_rows") or 0)
    expected = expected_canonical_event_count()
    if expected is None:
        expected = raw_expected
    if expected and len(events) != expected:
        raise ValueError(
            "El numero de eventos canonicos no coincide con los eventos raw de WhoScored: "
            f"events.csv={len(events)}, raw_con_partido_canonico={expected}, raw_total={raw_expected}"
        )
    if raw_expected and expected and raw_expected != expected:
        print_warning(
            f"{raw_expected - expected} eventos raw de WhoScored no pertenecen a partidos "
            "completados canonicos y no se esperan en events.csv."
        )


def expected_canonical_event_count() -> int | None:
    raw_files = sorted(
        path
        for path in RAW_WHOSCORED_EVENTS_DIR.glob("read_events_*.csv")
        if path_has_target_scope(path)
    )
    matches_path = CANONICAL_DIR / "matches.csv"
    if not raw_files or not matches_path.exists():
        return None

    raw_frames = [
        pd.read_csv(path, usecols=lambda column: column in {"game_id", "league", "season"})
        for path in raw_files
    ]
    raw_events = pd.concat(raw_frames, ignore_index=True)
    raw_events = filter_target_seasons(raw_events)
    if raw_events.empty or "game_id" not in raw_events.columns:
        return 0

    raw_game_ids = pd.to_numeric(raw_events["game_id"], errors="coerce")
    matches = pd.read_csv(matches_path, usecols=lambda column: column in {"idWhoscored", "matchStatus"})
    if "matchStatus" not in matches.columns:
        matches["matchStatus"] = "completed"
    active_matches = matches[
        ~matches["matchStatus"].astype("string").str.strip().str.lower().eq("abandoned")
    ].copy()
    match_game_ids = pd.to_numeric(active_matches["idWhoscored"], errors="coerce").dropna().astype(int)
    if match_game_ids.empty:
        return 0

    return int(raw_game_ids.dropna().astype(int).isin(set(match_game_ids)).sum())


def validate_event_numeric_ranges(events: pd.DataFrame, warnings: list[str]) -> None:
    assert_columns(
        "events.csv",
        events,
        {
            "id_event",
            "id_match",
            "minute",
            "second",
            "expandedMinute",
            "x",
            "y",
            "endX",
            "endY",
            "goalMouthY",
            "goalMouthZ",
            "blockedX",
            "blockedY",
        },
    )
    errors: list[str] = []
    for column in ["minute", "expandedMinute"]:
        error = numeric_range_error(
            "events.csv",
            events,
            column,
            example_columns=["id_event", "id_match"],
            minimum=0,
            integer=True,
        )
        if error:
            errors.append(error)
        add_warning(
            warnings,
            numeric_suspicion_warning(
                "events.csv",
                events,
                column,
                example_columns=["id_event", "id_match"],
                above=140,
            ),
        )

    error = numeric_range_error(
        "events.csv",
        events,
        "second",
        example_columns=["id_event", "id_match"],
        minimum=0,
        maximum=59,
    )
    if error:
        errors.append(error)

    for column in ["x", "y", "endX", "endY", "goalMouthY", "goalMouthZ", "blockedX", "blockedY"]:
        error = numeric_range_error(
            "events.csv",
            events,
            column,
            example_columns=["id_event", "id_match", "type"],
            minimum=0,
            maximum=100,
        )
        if error:
            errors.append(error)

    if errors:
        raise ValueError(" | ".join(errors))


def validate_player_participation_numeric_ranges(pmp: pd.DataFrame, warnings: list[str]) -> None:
    assert_columns(
        "player_match_participation.csv",
        pmp,
        {
            "id_playerMatchParticipation",
            "id_match",
            "id_team",
            "id_player",
            "participationStatus",
            "isCaptain",
            "subIn",
            "subOut",
            "minutes",
        },
    )
    errors: list[str] = []

    error = allowed_value_error(
        "player_match_participation.csv",
        pmp,
        "participationStatus",
        allowed_values={"titular", "suplente", "no jugado", "no disponible"},
        example_columns=["id_playerMatchParticipation", "id_match", "id_player"],
    )
    if error:
        errors.append(error)

    error = boolean_parse_error(
        "player_match_participation.csv",
        pmp,
        "isCaptain",
        example_columns=["id_playerMatchParticipation", "id_match", "id_player"],
    )
    if error:
        errors.append(error)

    error = substitution_minute_error(
        "player_match_participation.csv",
        pmp,
        "subIn",
        example_columns=["id_playerMatchParticipation", "id_match", "id_player"],
    )
    if error:
        errors.append(error)
    add_warning(
        warnings,
        numeric_suspicion_warning(
            "player_match_participation.csv",
            pmp,
            "subIn",
            example_columns=["id_playerMatchParticipation", "id_match", "id_player"],
            above=140,
        ),
    )

    for column in ["subOut", "minutes"]:
        error = numeric_range_error(
            "player_match_participation.csv",
            pmp,
            column,
            example_columns=["id_playerMatchParticipation", "id_match", "id_player"],
            minimum=0,
        )
        if error:
            errors.append(error)
        add_warning(
            warnings,
            numeric_suspicion_warning(
                "player_match_participation.csv",
                pmp,
                column,
                example_columns=["id_playerMatchParticipation", "id_match", "id_player"],
                above=140,
            ),
        )

    non_negative_columns = [
        "appearances",
        "foulsCommitted",
        "foulsSuffered",
        "ownGoals",
        "redCards",
        "yellowCards",
        "goalsConceded",
        "saves",
        "goalAssists",
        "shotsOnTarget",
        "totalGoals",
        "totalShots",
        "offsides",
        "xg",
        "xg_chain",
        "xg_buildup",
        "xa",
        "keyPasses",
    ]
    for column in non_negative_columns:
        if column not in pmp.columns:
            continue
        error = numeric_range_error(
            "player_match_participation.csv",
            pmp,
            column,
            example_columns=["id_playerMatchParticipation", "id_match", "id_player"],
            minimum=0,
        )
        if error:
            errors.append(error)

    if errors:
        raise ValueError(" | ".join(errors))


def main() -> dict[str, object]:
    if _ARGS is None:
        parse_no_args("Valida salidas canonicas de normalizacion de jugadores.")

    print("Validando normalizacion de jugadores...")
    warnings: list[str] = []
    players = read_csv(CANONICAL_DIR / "players.csv")
    tcs = read_csv(CANONICAL_DIR / "team_competition_season.csv")
    pmp = read_csv(CANONICAL_DIR / "player_match_participation.csv")
    stats = read_csv(CANONICAL_DIR / "player_competition_season_stats.csv")
    events = read_csv(CANONICAL_DIR / "events.csv")
    identities = read_csv(PLAYER_IDENTITIES_PATH)
    alias_map = read_csv(PLAYER_ALIAS_MAP_PATH)
    review_queue = read_csv(PLAYER_REVIEW_QUEUE_PATH)
    configure_name_quality_from_observations(alias_map)
    identity_info_by_id = (
        identities.set_index("id_player").to_dict(orient="index")
        if "id_player" in identities.columns
        else {}
    )

    required_player_columns = {
        "id_player",
        "knownAs",
        "fullName",
        "idUnderstat",
        "idWhoscored",
    }
    missing_columns = required_player_columns - set(players.columns)
    if missing_columns:
        raise ValueError(f"Faltan columnas en players.csv: {sorted(missing_columns)}")

    legacy_prefixed_ids = players["id_player"].astype(str).str.startswith("player_")
    if legacy_prefixed_ids.any():
        examples = players.loc[legacy_prefixed_ids, "id_player"].head(20).tolist()
        raise ValueError(f"Hay id_player con prefijo legacy 'player_': {examples}")

    duplicated_player_ids = players["id_player"].duplicated(keep=False)
    if duplicated_player_ids.any():
        examples = players.loc[duplicated_player_ids, ["id_player", "knownAs", "fullName"]].head(20).to_dict(orient="records")
        raise ValueError(f"Hay id_player duplicados en players.csv: {examples}")

    unjustified_duplicate_names = unjustified_duplicate_full_name_rows(
        players,
        identity_info_by_id=identity_info_by_id,
    )
    if not unjustified_duplicate_names.empty:
        examples = unjustified_duplicate_names[
            ["id_player", "knownAs", "fullName", "idUnderstat", "idWhoscored"]
        ].head(20).to_dict(orient="records")
        raise ValueError(
            "Hay fullName duplicados no justificados por homonimia desambiguada; deben fusionarse o enriquecerse. "
            f"Ejemplos: {examples}"
        )
    non_empty_names = players["fullName"].fillna("").astype(str).str.strip().ne("")

    player_ids = non_empty_set(players["id_player"])
    identity_ids = non_empty_set(identities["id_player"])
    assert_no_missing("players.csv frente a player_identities.csv", player_ids - identity_ids)
    assert_no_missing("player_match_participation.id_player", non_empty_set(pmp["id_player"]) - player_ids)
    assert_no_missing("player_competition_season_stats.id_player", non_empty_set(stats["id_player"]) - player_ids)
    assert_no_missing("events.id_player", non_empty_set(events["id_player"]) - player_ids)
    assert_no_missing("events.related_player_id", non_empty_set(events["related_player_id"]) - player_ids)

    valid_pmp_ids = non_empty_set(pmp["id_playerMatchParticipation"])
    primary_mask = events["id_player"].notna() & events["id_player"].astype(str).str.strip().ne("")
    primary_pmp_ids = non_empty_set(
        events.loc[primary_mask, "id_match"].fillna("") + "_" + events.loc[primary_mask, "id_player"].fillna("")
    )
    secondary_mask = events["related_player_id"].notna() & events["related_player_id"].astype(str).str.strip().ne("")
    secondary_pmp_ids = non_empty_set(
        events.loc[secondary_mask, "id_match"].fillna("") + "_" + events.loc[secondary_mask, "related_player_id"].fillna("")
    )
    assert_no_missing("events -> player_match_participation primario", primary_pmp_ids - valid_pmp_ids)
    assert_no_missing("events -> player_match_participation secundario", secondary_pmp_ids - valid_pmp_ids)
    validate_event_numeric_ranges(events, warnings)
    validate_player_participation_numeric_ranges(pmp, warnings)

    poor_full_names = [
        name
        for name in players["fullName"].dropna().astype(str)
        if name.strip() and is_poor_display_name(name)
    ]
    if poor_full_names:
        raise ValueError(f"Hay full_name pobres o abreviados: {sorted(set(poor_full_names))[:20]}")

    invalid_full_name_ids = []
    for row in players.itertuples(index=False):
        player_id = clean_text(getattr(row, "id_player", ""))
        identity_info = identity_info_by_id.get(player_id, {})
        full_name = clean_text(getattr(row, "fullName", "")) or clean_text(identity_info.get("full_name", ""))
        known_as = clean_text(getattr(row, "knownAs", "")) or clean_text(identity_info.get("known_as", ""))
        full_name_slug = clean_identifier_text(full_name).lower()
        if not is_valid_player_id_for_full_name(
            player_id,
            full_name,
            known_as=known_as,
            id_understat=clean_text(getattr(row, "idUnderstat", "")),
            id_whoscored=clean_text(getattr(row, "idWhoscored", "")),
            source_player_keys=identity_info.get("source_player_keys", ""),
            competitions=identity_info.get("competitions", ""),
            teams=identity_info.get("teams", ""),
            resolution_method=identity_info.get("resolution_method", ""),
        ):
            invalid_full_name_ids.append(
                {
                    "id_player": player_id,
                    "knownAs": known_as,
                    "fullName": full_name,
                    "expected": full_name_slug or "normalize(known_as)",
                }
            )
    if invalid_full_name_ids:
        raise ValueError(
            "Hay id_player que no cumplen la politica normalize(full_name) o normalize(known_as) sin sufijos de fuente. "
            f"Ejemplos: {invalid_full_name_ids[:20]}"
        )

    if not review_queue.empty:
        examples = review_queue.head(20).to_dict(orient="records")
        print_audit("Jugadores pendientes extremos", len(review_queue), PLAYER_REVIEW_QUEUE_PATH)
        print_examples(examples)

    pending_identities = identities[identities.get("needs_review", "").astype(str).str.lower().eq("true")]
    if not pending_identities.empty:
        examples = pending_identities[["id_player", "known_as", "full_name", "resolution_method"]].head(20).to_dict(orient="records")
        print_audit("Identidades de jugador marcadas como needs_review=true", len(pending_identities), PLAYER_IDENTITIES_PATH)
        print_examples(examples)

    pending_aliases = alias_map[alias_map.get("needs_review", "").astype(str).str.lower().eq("true")]
    if not pending_aliases.empty:
        examples = pending_aliases[["source_player_key", "id_player", "known_as", "full_name", "method"]].head(20).to_dict(orient="records")
        print_audit("Aliases de jugador marcados como needs_review=true", len(pending_aliases), PLAYER_ALIAS_MAP_PATH)
        print_examples(examples)

    duplicated_source_ids = []
    for row in players.itertuples(index=False):
        understat_ids = split_multi_value(getattr(row, "idUnderstat", ""))
        whoscored_ids = split_multi_value(getattr(row, "idWhoscored", ""))
        if len(understat_ids) > 1 or len(whoscored_ids) > 1:
            duplicated_source_ids.append(
                {
                    "id_player": getattr(row, "id_player", ""),
                    "knownAs": getattr(row, "knownAs", ""),
                    "fullName": getattr(row, "fullName", ""),
                    "idUnderstat": " | ".join(understat_ids),
                    "idWhoscored": " | ".join(whoscored_ids),
                }
            )
    if duplicated_source_ids:
        raise ValueError(
            "Hay jugadores canonicos con varios IDs de la misma fuente. "
            f"Ejemplos: {duplicated_source_ids[:20]}"
        )

    source_key_conflicts = alias_map.groupby("source_player_key")["id_player"].nunique()
    conflicts = source_key_conflicts[source_key_conflicts > 1]
    if not conflicts.empty:
        raise ValueError(f"Hay source_player_key asignados a varios jugadores: {conflicts.head(20).to_dict()}")

    same_match_multi_team = (
        pmp.groupby(["id_match", "id_player"])["id_team"].nunique().reset_index(name="team_count")
    )
    invalid_match_team = same_match_multi_team[same_match_multi_team["team_count"] > 1]
    if not invalid_match_team.empty:
        raise ValueError(
            "Hay jugadores canonicos asociados a varios equipos en el mismo partido. "
            f"Ejemplos: {invalid_match_team.head(20).to_dict(orient='records')}"
        )

    duplicated_stats = stats["id_playerCompetitionSeasonStats"].duplicated(keep=False)
    if duplicated_stats.any():
        raise ValueError(
            "Hay id_playerCompetitionSeasonStats duplicados. "
            f"Filas afectadas: {int(duplicated_stats.sum())}"
        )

    tcs_ids = non_empty_set(tcs["id_teamCompetitionSeason"])
    stats_tcs_ids = non_empty_set(
        stats["id_competition"].fillna("")
        + "_"
        + stats["id_season"].fillna("")
        + "_"
        + stats["id_team"].fillna("")
    )
    assert_no_missing("player_competition_season_stats -> team_competition_season", stats_tcs_ids - tcs_ids)

    validate_event_count(events)

    print_validation_ok("Normalizacion de jugadores")
    print_metric("Jugadores", len(players))
    print_metric("Participaciones", len(pmp))
    print_metric("Stats temporada", len(stats))
    print_metric("Eventos", len(events))
    return {"warnings": warnings}


if __name__ == "__main__":
    run_with_optional_task_result("validate_player_normalization", "validate", main)
