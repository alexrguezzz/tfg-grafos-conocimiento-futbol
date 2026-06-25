from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(PROJECT_ROOT))

from src.validation.validation_args import parse_no_args  # noqa: E402

_ARGS = parse_no_args("Valida salidas canonicas de contexto externo.") if __name__ == "__main__" else None

import pandas as pd  # noqa: E402

from src.pipeline.console_output import print_metric, print_validation_ok, print_warning  # noqa: E402
from src.pipeline.script_result import run_with_optional_task_result  # noqa: E402


CANONICAL_DIR = PROJECT_ROOT / "data" / "processed" / "canonical"


def read_csv(path: Path, **kwargs) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"No existe: {path}")
    return pd.read_csv(path, dtype="string", **kwargs)


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


def assert_columns(label: str, df: pd.DataFrame, columns: set[str]) -> None:
    missing = columns - set(df.columns)
    if missing:
        raise ValueError(f"Faltan columnas en {label}: {sorted(missing)}")


def assert_no_duplicates(label: str, df: pd.DataFrame, column: str) -> None:
    duplicated = df[column].duplicated(keep=False)
    if duplicated.any():
        examples = df.loc[duplicated, column].head(20).tolist()
        raise ValueError(f"{label}: valores duplicados en {column}: {examples}")


def missing_values_error(label: str, df: pd.DataFrame, columns: list[str], example_columns: list[str]) -> str | None:
    missing_masks = {
        column: ~non_empty_mask(df[column])
        for column in columns
    }
    missing_by_column = {
        column: int(mask.sum())
        for column, mask in missing_masks.items()
        if int(mask.sum()) > 0
    }
    if not missing_by_column:
        return None

    row_mask = pd.Series(False, index=df.index)
    for mask in missing_masks.values():
        row_mask = row_mask | mask
    available_examples = [column for column in example_columns if column in df.columns]
    examples = df.loc[row_mask, available_examples].head(20).to_dict(orient="records")
    return f"{label}: valores vacios en columnas obligatorias: {missing_by_column}. Ejemplos: {examples}"


def non_empty_mask(series: pd.Series) -> pd.Series:
    return series.apply(lambda value: bool(clean_text(value))).astype(bool)


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

    out_of_range = present & values.notna()
    range_parts: list[str] = []
    if minimum is not None:
        out_of_range = out_of_range & values.ge(minimum)
        range_parts.append(f">= {minimum:g}")
    if maximum is not None:
        out_of_range = out_of_range & values.le(maximum)
        range_parts.append(f"<= {maximum:g}")
    invalid_range = present & values.notna() & ~out_of_range
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
    below: float | None = None,
    above: float | None = None,
) -> str | None:
    present = non_empty_mask(df[column])
    values = pd.to_numeric(df[column], errors="coerce")
    suspicious = pd.Series(False, index=df.index)
    conditions: list[str] = []
    if below is not None:
        suspicious = suspicious | (present & values.notna() & values.lt(below))
        conditions.append(f"< {below:g}")
    if above is not None:
        suspicious = suspicious | (present & values.notna() & values.gt(above))
        conditions.append(f"> {above:g}")
    if not suspicious.any():
        return None
    return (
        f"{label}: {column} tiene valores sospechosos ({' o '.join(conditions)}). "
        f"Ejemplos: {example_rows(df, suspicious, [*example_columns, column])}"
    )


def datetime_parse_error(label: str, df: pd.DataFrame, column: str, *, example_columns: list[str]) -> str | None:
    present = non_empty_mask(df[column])
    parsed = pd.to_datetime(df[column], errors="coerce", utc=True)
    invalid = present & parsed.isna()
    if not invalid.any():
        return None
    return (
        f"{label}: {column} debe ser una fecha parseable. "
        f"Ejemplos: {example_rows(df, invalid, [*example_columns, column])}"
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


def parse_bool_value(value: object) -> bool | None:
    text = clean_text(value).lower()
    if text in TRUE_VALUES:
        return True
    if text in FALSE_VALUES:
        return False
    return None


def add_warning(warnings: list[str], message: str | None) -> None:
    if not message:
        return
    warnings.append(message)
    print_warning(message)


def assert_coordinate_range(stadiums: pd.DataFrame) -> None:
    latitude = pd.to_numeric(stadiums["latitude"], errors="coerce")
    longitude = pd.to_numeric(stadiums["longitude"], errors="coerce")

    present_lat = non_empty_mask(stadiums["latitude"])
    present_lon = non_empty_mask(stadiums["longitude"])
    invalid_numeric = (present_lat & latitude.isna()) | (present_lon & longitude.isna())
    if invalid_numeric.any():
        examples = stadiums.loc[invalid_numeric, ["id_stadium", "latitude", "longitude"]].head(20).to_dict(orient="records")
        raise ValueError(f"Hay coordenadas de estadio no numericas: {examples}")

    invalid_lat = latitude.notna() & ~latitude.between(-90, 90)
    invalid_lon = longitude.notna() & ~longitude.between(-180, 180)
    if invalid_lat.any() or invalid_lon.any():
        examples = stadiums.loc[invalid_lat | invalid_lon, ["id_stadium", "latitude", "longitude"]].head(20).to_dict(orient="records")
        raise ValueError(f"Hay coordenadas de estadio fuera de rango: {examples}")


def main() -> dict[str, object]:
    if _ARGS is None:
        parse_no_args("Valida salidas canonicas de contexto externo.")

    print("Validando contexto externo...")
    warnings: list[str] = []
    matches = read_csv(CANONICAL_DIR / "matches.csv")
    stadiums = read_csv(CANONICAL_DIR / "stadiums.csv")
    weather = read_csv(CANONICAL_DIR / "weather_observations.csv")
    team_participation = read_csv(CANONICAL_DIR / "team_match_participation.csv")

    assert_columns(
        "matches.csv",
        matches,
        {"id_match", "id_home_team", "id_away_team", "dateTime", "homeScore", "awayScore", "venue", "id_stadium", "matchStatus"},
    )
    assert_columns(
        "stadiums.csv",
        stadiums,
        {"id_stadium", "name", "city", "country", "latitude", "longitude", "idWikidata", "idOsm"},
    )
    assert_columns(
        "weather_observations.csv",
        weather,
        {
            "id_weatherObservation",
            "id_match",
            "dateTime",
            "temperature",
            "precipitation",
            "rain",
            "windSpeed",
            "humidity",
        },
    )
    assert_columns(
        "team_match_participation.csv",
        team_participation,
        {"id_teamMatchParticipation", "id_match", "id_team", "isHome"},
    )

    assert_no_duplicates("stadiums.csv", stadiums, "id_stadium")
    assert_no_duplicates("weather_observations.csv", weather, "id_weatherObservation")
    assert_no_duplicates("weather_observations.csv", weather, "id_match")
    assert_no_duplicates("team_match_participation.csv", team_participation, "id_teamMatchParticipation")
    assert_coordinate_range(stadiums)

    allowed_match_statuses = {"completed", "abandoned"}
    invalid_status_mask = ~matches["matchStatus"].astype("string").str.strip().str.lower().isin(allowed_match_statuses)
    if invalid_status_mask.any():
        examples = matches.loc[invalid_status_mask, ["id_match", "matchStatus"]].head(20).to_dict(orient="records")
        raise ValueError(f"matches.csv: matchStatus invalido. Ejemplos: {examples}")

    completeness_errors: list[str] = []
    match_missing_values = missing_values_error(
        "matches.csv",
        matches,
        ["id_match", "id_home_team", "id_away_team", "dateTime", "matchStatus"],
        ["id_match", "id_home_team", "id_away_team", "dateTime", "matchStatus"],
    )
    if match_missing_values:
        completeness_errors.append(match_missing_values)

    same_team_mask = (
        matches["id_home_team"].apply(clean_text).ne("")
        & matches["id_home_team"].apply(clean_text).eq(matches["id_away_team"].apply(clean_text))
    )
    if same_team_mask.any():
        completeness_errors.append(
            "matches.csv: id_home_team e id_away_team no pueden ser iguales. "
            f"Ejemplos: {example_rows(matches, same_team_mask, ['id_match', 'id_home_team', 'id_away_team'])}"
        )

    for column in ["homeScore", "awayScore", "halftimeHomeScore", "halftimeAwayScore"]:
        if column in matches.columns:
            error = numeric_range_error(
                "matches.csv",
                matches,
                column,
                example_columns=["id_match", "matchStatus"],
                minimum=0,
                integer=True,
            )
            if error:
                completeness_errors.append(error)

    completed_matches = matches["matchStatus"].astype("string").str.strip().str.lower().eq("completed")
    for column in ["homeScore", "awayScore"]:
        missing_completed_score = completed_matches & ~non_empty_mask(matches[column])
        if missing_completed_score.any():
            completeness_errors.append(
                f"matches.csv: partidos completados sin {column}. "
                f"Ejemplos: {example_rows(matches, missing_completed_score, ['id_match', 'matchStatus', column])}"
            )

    for column in ["date", "dateTime"]:
        if column in matches.columns:
            error = datetime_parse_error("matches.csv", matches, column, example_columns=["id_match"])
            if error:
                completeness_errors.append(error)

    if "attendance" in matches.columns:
        error = numeric_range_error(
            "matches.csv",
            matches,
            "attendance",
            example_columns=["id_match", "venue"],
            minimum=0,
            integer=True,
        )
        if error:
            completeness_errors.append(error)

    weather_missing_values = missing_values_error(
        "weather_observations.csv",
        weather,
        [
            "id_weatherObservation",
            "id_match",
            "dateTime",
            "temperature",
            "precipitation",
            "rain",
            "windSpeed",
            "humidity",
        ],
        ["id_weatherObservation", "id_match", "dateTime"],
    )
    if weather_missing_values:
        completeness_errors.append(weather_missing_values)

    for column, minimum, maximum in [
        ("temperature", -50, 60),
        ("precipitation", 0, None),
        ("rain", 0, None),
        ("windSpeed", 0, None),
        ("humidity", 0, 100),
    ]:
        error = numeric_range_error(
            "weather_observations.csv",
            weather,
            column,
            example_columns=["id_weatherObservation", "id_match", "dateTime"],
            minimum=minimum,
            maximum=maximum,
        )
        if error:
            completeness_errors.append(error)

    error = datetime_parse_error(
        "weather_observations.csv",
        weather,
        "dateTime",
        example_columns=["id_weatherObservation", "id_match"],
    )
    if error:
        completeness_errors.append(error)

    add_warning(
        warnings,
        numeric_suspicion_warning(
            "weather_observations.csv",
            weather,
            "temperature",
            example_columns=["id_weatherObservation", "id_match", "dateTime"],
            below=-20,
            above=45,
        ),
    )
    add_warning(
        warnings,
        numeric_suspicion_warning(
            "weather_observations.csv",
            weather,
            "precipitation",
            example_columns=["id_weatherObservation", "id_match", "dateTime"],
            above=100,
        ),
    )
    add_warning(
        warnings,
        numeric_suspicion_warning(
            "weather_observations.csv",
            weather,
            "rain",
            example_columns=["id_weatherObservation", "id_match", "dateTime"],
            above=100,
        ),
    )
    add_warning(
        warnings,
        numeric_suspicion_warning(
            "weather_observations.csv",
            weather,
            "windSpeed",
            example_columns=["id_weatherObservation", "id_match", "dateTime"],
            above=120,
        ),
    )

    stadium_missing_values = missing_values_error(
        "stadiums.csv",
        stadiums,
        ["name", "city", "country", "latitude", "longitude"],
        ["id_stadium", "name", "city", "country", "latitude", "longitude", "idWikidata", "idOsm"],
    )
    if stadium_missing_values:
        completeness_errors.append(stadium_missing_values)

    team_participation_missing_values = missing_values_error(
        "team_match_participation.csv",
        team_participation,
        ["id_teamMatchParticipation", "id_match", "id_team", "isHome"],
        ["id_teamMatchParticipation", "id_match", "id_team", "isHome"],
    )
    if team_participation_missing_values:
        completeness_errors.append(team_participation_missing_values)

    active_matches = matches[
        ~matches["matchStatus"].astype("string").str.strip().str.lower().eq("abandoned")
    ].copy()
    stadium_ids = non_empty_set(stadiums["id_stadium"])
    active_match_stadium_ids = non_empty_set(active_matches["id_stadium"])
    missing_stadium_ids = active_match_stadium_ids - stadium_ids
    if missing_stadium_ids:
        completeness_errors.append(
            f"Hay id_stadium de matches.csv sin estadio en stadiums.csv: {sorted(missing_stadium_ids)[:20]}"
        )

    missing_active_stadium_mask = active_matches["id_stadium"].apply(lambda value: not clean_text(value))
    if missing_active_stadium_mask.any():
        examples = (
            active_matches.loc[missing_active_stadium_mask, ["id_match", "venue", "id_stadium"]]
            .head(20)
            .to_dict(orient="records")
        )
        add_warning(
            warnings,
            "Hay partidos sin id_stadium; no se exigira observacion meteorologica para ellos. "
            f"Ejemplos: {examples}",
        )

    valid_stadium_mask = active_matches["id_stadium"].apply(lambda value: clean_text(value) in stadium_ids)
    weather_required_matches = active_matches.loc[valid_stadium_mask].copy()

    match_ids = non_empty_set(weather_required_matches["id_match"])
    all_match_ids = non_empty_set(matches["id_match"])
    weather_match_ids = non_empty_set(weather["id_match"])
    missing_matches = weather_match_ids - all_match_ids
    if missing_matches:
        completeness_errors.append(
            f"weather_observations.id_match inexistente en matches.csv: {sorted(missing_matches)[:20]}"
        )
    matches_without_weather = match_ids - weather_match_ids
    if matches_without_weather:
        completeness_errors.append(
            "Hay partidos sin observacion meteorologica. "
            f"Ejemplos: {sorted(matches_without_weather)[:20]}"
        )

    team_participation_match_ids = non_empty_set(team_participation["id_match"])
    missing_team_participation_matches = team_participation_match_ids - all_match_ids
    if missing_team_participation_matches:
        completeness_errors.append(
            "team_match_participation.id_match inexistente en matches.csv: "
            f"{sorted(missing_team_participation_matches)[:20]}"
        )

    duplicate_team_match = team_participation.duplicated(subset=["id_match", "id_team"], keep=False)
    duplicate_team_match = duplicate_team_match & non_empty_mask(team_participation["id_match"]) & non_empty_mask(team_participation["id_team"])
    if duplicate_team_match.any():
        completeness_errors.append(
            "team_match_participation.csv: un mismo equipo aparece varias veces en el mismo partido. "
            f"Ejemplos: {example_rows(team_participation, duplicate_team_match, ['id_teamMatchParticipation', 'id_match', 'id_team'])}"
        )

    error = boolean_parse_error(
        "team_match_participation.csv",
        team_participation,
        "isHome",
        example_columns=["id_teamMatchParticipation", "id_match", "id_team"],
    )
    if error:
        completeness_errors.append(error)

    active_match_ids = non_empty_set(active_matches["id_match"])
    active_team_participation = team_participation[team_participation["id_match"].apply(clean_text).isin(active_match_ids)].copy()
    teams_per_active_match = (
        active_team_participation.groupby("id_match", dropna=False)["id_team"]
        .agg(lambda values: len({clean_text(value) for value in values if clean_text(value)}))
    )
    invalid_team_counts = teams_per_active_match[teams_per_active_match.ne(2)]
    matches_without_team_participation = active_match_ids - set(teams_per_active_match.index.astype(str))
    if matches_without_team_participation or not invalid_team_counts.empty:
        examples = [
            {"id_match": match_id, "team_count": int(count)}
            for match_id, count in invalid_team_counts.head(20).items()
        ]
        examples.extend({"id_match": match_id, "team_count": 0} for match_id in sorted(matches_without_team_participation)[:20])
        completeness_errors.append(
            "team_match_participation.csv: cada partido no abandonado debe tener exactamente 2 equipos. "
            f"Ejemplos: {examples[:20]}"
        )

    if error is None:
        active_team_participation["_is_home_bool"] = active_team_participation["isHome"].apply(parse_bool_value)
        home_counts = active_team_participation.groupby("id_match", dropna=False)["_is_home_bool"].sum()
        invalid_home_counts = home_counts[home_counts.ne(1)]
        if not invalid_home_counts.empty:
            examples = [
                {"id_match": match_id, "home_team_rows": int(count)}
                for match_id, count in invalid_home_counts.head(20).items()
            ]
            completeness_errors.append(
                "team_match_participation.csv: cada partido no abandonado debe tener exactamente un equipo local. "
                f"Ejemplos: {examples}"
            )

    if completeness_errors:
        raise ValueError(" | ".join(completeness_errors))

    print_validation_ok("Contexto externo")
    print_metric("Estadios", len(stadiums))
    print_metric("Observaciones meteorologicas", len(weather))
    print_metric("Participaciones de equipo", len(team_participation))
    return {"warnings": warnings}


if __name__ == "__main__":
    run_with_optional_task_result("validate_external_context", "validate", main)
