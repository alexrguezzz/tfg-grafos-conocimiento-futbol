from pathlib import Path
import ast
import pandas as pd
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(PROJECT_ROOT))

from src.transform.transform_args import parse_no_args  # noqa: E402

from src.pipeline.console_output import print_audit, print_examples, print_result, print_warning  # noqa: E402
from src.pipeline.script_result import run_with_optional_task_result  # noqa: E402
from src.transform.player_normalization import map_player_ids, map_source_ids_to_player_ids
from src.utils.text_normalization import normalize_team
from src.utils.season_scope import filter_target_seasons, path_has_target_scope


EVENT_COLUMNS = [
    "id_event",
    "id_match",
    "id_team",
    "id_player",
    "period",
    "minute",
    "second",
    "expandedMinute",
    "type",
    "outcomeType",
    "x",
    "y",
    "endX",
    "endY",
    "goalMouthY",
    "goalMouthZ",
    "blockedX",
    "blockedY",
    "qualifiers",
    "isTouch",
    "isShot",
    "isGoal",
    "cardType",
    "idWhoscored",
    "related_event_id",
    "related_player_id",
]


def parse_source_id(value) -> str | None:
    if pd.isna(value):
        return None

    text = str(value).strip()
    if not text or text.lower() == "nan":
        return None

    try:
        return str(int(float(text)))
    except Exception:
        return text


def has_event_id_column(df: pd.DataFrame) -> bool:
    return "event_id" in df.columns


def build_unique_source_event_ids(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    # Keep event_id as the provider event number used by related-event links,
    # and include the raw WhoScored row id in source_event_id for uniqueness.
    if has_event_id_column(df):
        df["source_event_number"] = df["event_id"].apply(parse_source_id).astype("string")
    else:
        print_warning("raw events de WhoScored sin event_id; se usara la secuencia legacy dentro de cada partido.")
        df["source_event_number"] = df["legacy_event_sequence"].astype("string")

    missing_source_event_ids = int(df["source_event_number"].isna().sum())
    if missing_source_event_ids:
        raise ValueError(
            f"Hay {missing_source_event_ids} eventos WhoScored sin identificador de evento; "
            "no se puede construir id_event"
        )

    if "id" in df.columns:
        raw_event_ids = df["id"].apply(parse_source_id).astype("string")
    else:
        raw_event_ids = pd.Series(pd.NA, index=df.index, dtype="string")

    missing_raw_event_ids = int(raw_event_ids.isna().sum())
    if missing_raw_event_ids:
        raise ValueError(
            f"Hay {missing_raw_event_ids} eventos WhoScored sin id raw; "
            "no se puede construir id_event con event_id + id de WhoScored"
        )

    df["source_event_id"] = (
        df["source_event_number"].astype(str)
        + "_"
        + raw_event_ids.astype(str)
    ).astype("string")

    duplicate_mask = df.duplicated(subset=["game_id", "source_event_id", "team"], keep=False)
    if duplicate_mask.any():
        duplicate_count = int(duplicate_mask.sum())
        duplicate_rows = df.loc[duplicate_mask].copy()
        examples = duplicate_rows[["game", "game_id", "source_event_number", "source_event_id", "team", "type", "player"]]
        raise ValueError(
            "Existen eventos duplicados para la misma tripleta (game_id, event_id + id WhoScored, team). "
            f"Filas afectadas: {duplicate_count}. Ejemplos: {examples.head(10).to_dict(orient='records')}"
        )

    return df

def build_event_ids(match_ids: pd.Series, source_event_ids: pd.Series, id_teams: pd.Series) -> pd.Series:
    return match_ids.astype(str) + "_event_" + id_teams.astype(str) + "_" + source_event_ids.astype(str)


def build_input_paths() -> list[Path]:
    events_dir = PROJECT_ROOT / "data" / "raw" / "whoscored" / "events"
    return sorted(path for path in events_dir.glob("read_events_*.csv") if path_has_target_scope(path))


def build_matches_path() -> Path:
    return PROJECT_ROOT / "data" / "processed" / "canonical" / "matches.csv"


def build_output_path() -> Path:
    output_dir = PROJECT_ROOT / "data" / "processed" / "canonical"
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir / "events.csv"


def as_bool_or_none(value) -> bool | None:
    if pd.isna(value):
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "1", "yes"}:
        return True
    if text in {"false", "0", "no"}:
        return False
    return None


def to_float_or_none(value) -> float | None:
    if pd.isna(value):
        return None
    try:
        return float(value)
    except Exception:
        return None


def to_int_or_none(value) -> int | None:
    if pd.isna(value):
        return None
    try:
        return int(float(value))
    except Exception:
        return None


def parse_qualifiers(value) -> list[dict]:
    if pd.isna(value):
        return []

    text = str(value).strip()
    if not text or text.lower() == "nan":
        return []

    try:
        parsed = ast.literal_eval(text)
    except Exception as exc:
        raise ValueError(f"No se pudieron parsear qualifiers: {text[:120]}") from exc

    if not isinstance(parsed, list):
        raise ValueError(f"Qualifiers debe ser una lista: {text[:120]}")

    return [item for item in parsed if isinstance(item, dict)]


def qualifier_name(item: dict) -> str | None:
    qualifier_type = item.get("type")
    if not isinstance(qualifier_type, dict):
        return None
    name = qualifier_type.get("displayName")
    return str(name).strip() if name is not None and str(name).strip() else None


def has_qualifier(qualifiers: list[dict], names: set[str]) -> bool:
    return any(qualifier_name(item) in names for item in qualifiers)


def qualifier_value(qualifiers: list[dict], name: str):
    for item in qualifiers:
        if qualifier_name(item) == name:
            return item.get("value")
    return None


def body_part_from_qualifiers(qualifiers: list[dict]) -> str | None:
    body_part_qualifiers = {
        "RightFoot": "right_foot",
        "LeftFoot": "left_foot",
        "Head": "head",
        "OtherBodyPart": "other_body_part",
    }
    for item in qualifiers:
        body_part = body_part_qualifiers.get(qualifier_name(item))
        if body_part:
            return body_part
    return None


def build_match_lookup(df_matches: pd.DataFrame) -> pd.DataFrame:
    required = ["id_match", "idWhoscored"]
    missing = [col for col in required if col not in df_matches.columns]
    if missing:
        raise ValueError(f"Faltan columnas obligatorias en matches.csv: {missing}")

    columns = [*required]
    if "matchStatus" in df_matches.columns:
        columns.append("matchStatus")
    lookup = df_matches[columns].copy()
    if "matchStatus" not in lookup.columns:
        lookup["matchStatus"] = "completed"
    lookup["game_id"] = pd.to_numeric(lookup["idWhoscored"], errors="coerce")
    lookup = lookup.dropna(subset=["game_id"]) 
    lookup["game_id"] = lookup["game_id"].astype(int)
    return lookup[["game_id", "id_match", "matchStatus"]].drop_duplicates(subset=["game_id"], keep="last")


def normalize_events(df: pd.DataFrame) -> pd.DataFrame:
    required = [
        "game_id", "team", "player", "player_id", "period", "minute", "second",
        "expanded_minute", "type", "outcome_type", "x", "y", "end_x", "end_y",
        "goal_mouth_y", "goal_mouth_z", "blocked_x", "blocked_y", "qualifiers",
        "is_touch", "is_shot", "is_goal", "card_type", "related_event_id", "related_player_id",
    ]
    missing = [col for col in required if col not in df.columns]
    if missing:
        raise ValueError(f"Faltan columnas en eventos raw: {missing}")

    df = df.copy()
    df["game_id"] = pd.to_numeric(df["game_id"], errors="coerce")
    df = df.dropna(subset=["game_id"]).copy()
    df["game_id"] = df["game_id"].astype(int)
    df["legacy_event_sequence"] = df.groupby("game_id").cumcount() + 1

    df["id_team"] = df["team"].astype(str).apply(normalize_team)
    df["id_player"] = map_player_ids(
        df,
        source="whoscored",
        player_col="player",
        source_player_id_col="player_id",
        team_col="team",
        competition_col="league",
        season_col="season",
    )
    df["period"] = df["period"].astype("string")
    df["minute"] = df["minute"].apply(to_int_or_none)
    df["second"] = df["second"].apply(to_float_or_none)
    df["expandedMinute"] = df["expanded_minute"].apply(to_int_or_none)
    df["x"] = df["x"].apply(to_float_or_none)
    df["y"] = df["y"].apply(to_float_or_none)
    df["endX"] = df["end_x"].apply(to_float_or_none)
    df["endY"] = df["end_y"].apply(to_float_or_none)
    df["goalMouthY"] = df["goal_mouth_y"].apply(to_float_or_none)
    df["goalMouthZ"] = df["goal_mouth_z"].apply(to_float_or_none)
    df["blockedX"] = df["blocked_x"].apply(to_float_or_none)
    df["blockedY"] = df["blocked_y"].apply(to_float_or_none)
    df["isTouch"] = df["is_touch"].apply(as_bool_or_none)
    df["isShot"] = df["is_shot"].apply(as_bool_or_none)
    df["isGoal"] = df["is_goal"].apply(as_bool_or_none)
    df["qualifiers"] = df["qualifiers"].astype("string")
    df["type"] = df["type"].astype("string")
    df["outcomeType"] = df["outcome_type"].astype("string")
    df["cardType"] = df["card_type"].astype("string")
    parsed_qualifiers = df["qualifiers"].apply(parse_qualifiers)
    is_pass = df["type"].astype("string").str.strip().str.lower().eq("pass")
    pass_flags = {
        "is_cross": {"Cross"},
        "is_long_ball": {"Longball"},
        "is_through_ball": {"Throughball"},
        "is_throw_in": {"ThrowIn"},
        "is_corner_taken": {"CornerTaken"},
        "is_free_kick": {"FreekickTaken", "IndirectFreekickTaken"},
        "is_goal_kick": {"GoalKick"},
        "is_shot_assist": {"ShotAssist"},
    }
    for column, qualifier_names in pass_flags.items():
        df[column] = [
            has_qualifier(qualifiers, qualifier_names) if pass_mask else None
            for pass_mask, qualifiers in zip(is_pass, parsed_qualifiers)
        ]

    df["pass_length"] = [
        to_float_or_none(qualifier_value(qualifiers, "Length")) if pass_mask else None
        for pass_mask, qualifiers in zip(is_pass, parsed_qualifiers)
    ]
    df["body_part"] = [
        body_part_from_qualifiers(qualifiers) if is_shot is True else None
        for is_shot, qualifiers in zip(df["isShot"], parsed_qualifiers)
    ]
    df = build_unique_source_event_ids(df)
    df["idWhoscored"] = df["source_event_id"].astype("string")
    df["player_source_id"] = df["player_id"].apply(parse_source_id).astype("string")
    df["related_source_event_number"] = df["related_event_id"].apply(parse_source_id).astype("string")
    df["related_player_source_id"] = df["related_player_id"].apply(parse_source_id).astype("string")
    df["related_player_id"] = map_source_ids_to_player_ids("whoscored", df["related_player_source_id"])

    return df


def report_unresolved_related_event_ids(df: pd.DataFrame, unresolved_mask: pd.Series) -> int:
    unresolved_count = int(unresolved_mask.sum())
    if unresolved_count == 0:
        return 0

    print_warning(f"{unresolved_count} related_event_id no se pudieron mapear a un id_event canonico.")

    context_columns = [
        col
        for col in [
            "id_match",
            "game",
            "game_id",
            "id_event",
            "source_event_number",
            "related_source_event_number",
            "related_player_source_id",
        ]
        if col in df.columns
    ]
    unresolved = df.loc[unresolved_mask, context_columns].head(20)
    examples = []
    for _, row in unresolved.iterrows():
        match_label = row.get("game", pd.NA)
        if pd.isna(match_label) or str(match_label).strip() == "":
            match_label = row.get("id_match", row.get("game_id", "unknown_match"))

        examples.append(
            f"Partido: {match_label}; "
            f"id_match={row.get('id_match', pd.NA)}; "
            f"id_event={row.get('id_event', pd.NA)}; "
            f"source_event_number={row.get('source_event_number', pd.NA)}; "
            f"related_event_id_raw={row.get('related_source_event_number', pd.NA)}; "
            f"related_player_id_raw={row.get('related_player_source_id', pd.NA)}"
        )
    print_examples(examples)

    if unresolved_count > len(unresolved):
        print(f"  - ... {unresolved_count - len(unresolved)} caso(s) mas no mostrados.")

    return unresolved_count


def attach_related_event_ids(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["related_event_id"] = pd.NA
    df.attrs["unresolved_related_event_id_count"] = 0

    if "id_match" not in df.columns:
        raise ValueError("Se requiere id_match para mapear related_event_id")

    related_mask = df["related_source_event_number"].notna()
    if not related_mask.any():
        return df

    target_by_player = (
        df[["id_match", "source_event_number", "player_source_id", "id_event"]]
        .dropna(subset=["source_event_number", "player_source_id"])
        .drop_duplicates(subset=["id_match", "source_event_number", "player_source_id"], keep=False)
        .copy()
    )
    target_by_player["related_source_event_number"] = target_by_player["source_event_number"]
    target_by_player["related_player_source_id"] = target_by_player["player_source_id"]
    target_by_player["mapped_related_event_id"] = target_by_player["id_event"]
    target_by_player = target_by_player[
        ["id_match", "related_source_event_number", "related_player_source_id", "mapped_related_event_id"]
    ]

    related_rows = df.loc[
        related_mask,
        ["id_match", "related_source_event_number", "related_player_source_id"],
    ].copy()
    related_rows["_row_index"] = related_rows.index

    mapped = related_rows.merge(
        target_by_player,
        on=["id_match", "related_source_event_number", "related_player_source_id"],
        how="left",
    )
    mapped = mapped.dropna(subset=["mapped_related_event_id"])
    if not mapped.empty:
        df.loc[mapped["_row_index"].to_numpy(), "related_event_id"] = mapped["mapped_related_event_id"].to_numpy()

    unresolved_index = df.index[related_mask & df["related_event_id"].isna()]
    if len(unresolved_index) == 0:
        return df

    target_by_event_number = (
        df[["id_match", "source_event_number", "id_event"]]
        .dropna(subset=["source_event_number"])
        .drop_duplicates(subset=["id_match", "source_event_number"], keep=False)
        .copy()
    )
    target_by_event_number["related_source_event_number"] = target_by_event_number["source_event_number"]
    target_by_event_number["mapped_related_event_id"] = target_by_event_number["id_event"]
    target_by_event_number = target_by_event_number[
        ["id_match", "related_source_event_number", "mapped_related_event_id"]
    ]

    fallback_rows = df.loc[
        unresolved_index,
        ["id_match", "related_source_event_number"],
    ].copy()
    fallback_rows["_row_index"] = fallback_rows.index
    fallback_mapped = fallback_rows.merge(
        target_by_event_number,
        on=["id_match", "related_source_event_number"],
        how="left",
    )
    fallback_mapped = fallback_mapped.dropna(subset=["mapped_related_event_id"])
    if not fallback_mapped.empty:
        df.loc[fallback_mapped["_row_index"].to_numpy(), "related_event_id"] = (
            fallback_mapped["mapped_related_event_id"].to_numpy()
        )

    unresolved_count = report_unresolved_related_event_ids(
        df, related_mask & df["related_event_id"].isna()
    )
    df.attrs["unresolved_related_event_id_count"] = unresolved_count

    return df


def build_events(raw_files: list[Path], matches_path: Path) -> pd.DataFrame:
    if not raw_files:
        raise FileNotFoundError("No existen archivos raw de eventos WhoScored")
    if not matches_path.exists():
        raise FileNotFoundError(f"No existe matches.csv: {matches_path}")

    raw_frames = [pd.read_csv(path) for path in raw_files]
    raw_events = pd.concat(raw_frames, ignore_index=True)
    raw_events = filter_target_seasons(raw_events)
    if raw_events.empty:
        raise FileNotFoundError("No existen archivos raw de eventos WhoScored para las temporadas objetivo")
    raw_events = normalize_events(raw_events)

    df_matches = pd.read_csv(matches_path)
    match_lookup = build_match_lookup(df_matches)

    abandoned_match_mask = match_lookup["matchStatus"].astype("string").str.strip().str.lower().eq("abandoned")
    abandoned_game_ids = set(match_lookup.loc[abandoned_match_mask, "game_id"].dropna().astype(int))
    abandoned_raw_mask = raw_events["game_id"].isin(abandoned_game_ids)
    abandoned_raw_event_count = int(abandoned_raw_mask.sum())
    abandoned_raw_game_count = int(raw_events.loc[abandoned_raw_mask, "game_id"].nunique())
    if abandoned_raw_event_count:
        print_warning(
            f"{abandoned_raw_event_count} eventos raw de WhoScored pertenecen a "
            f"{abandoned_raw_game_count} partido(s) abandonado(s) y se excluyen de events.csv."
        )

    raw_events_for_build = raw_events.loc[~abandoned_raw_mask].copy()
    active_match_lookup = match_lookup.loc[~abandoned_match_mask, ["game_id", "id_match"]].copy()

    canonical_game_ids = set(active_match_lookup["game_id"].dropna().astype(int))
    unmapped_raw_mask = ~raw_events_for_build["game_id"].isin(canonical_game_ids)
    unmapped_raw_event_count = int(unmapped_raw_mask.sum())
    unmapped_raw_game_count = int(raw_events_for_build.loc[unmapped_raw_mask, "game_id"].nunique())
    if unmapped_raw_event_count:
        print_warning(
            f"{unmapped_raw_event_count} eventos raw de WhoScored pertenecen a "
            f"{unmapped_raw_game_count} partido(s) sin partido canonico en matches.csv."
        )
        context_columns = [
            col
            for col in ["league", "season", "game_id", "game"]
            if col in raw_events_for_build.columns
        ]
        examples = (
            raw_events_for_build.loc[unmapped_raw_mask, context_columns]
            .drop_duplicates()
            .head(20)
            .to_dict(orient="records")
        )
        print_examples(examples)

    df = raw_events_for_build.merge(active_match_lookup, on="game_id", how="inner")
    if df.empty:
        raise ValueError("No se pudo mapear ningun evento a id_match usando idWhoscored")

    df = df.reset_index(drop=True)
    # Comprobar duplicados por (id_match, source_event_id, id_team)
    duplicate_source_event_ids = df.duplicated(subset=["id_match", "source_event_id", "id_team"], keep=False)
    if duplicate_source_event_ids.any():
        duplicate_count = int(duplicate_source_event_ids.sum())
        raise ValueError(
            "Hay eventos duplicados para la misma tripleta (id_match, source_event_id, id_team). "
            f"Filas afectadas: {duplicate_count}"
        )

    df["id_event"] = build_event_ids(df["id_match"], df["source_event_id"], df["id_team"])
    duplicate_event_ids = df.duplicated(subset=["id_event"], keep=False)
    if duplicate_event_ids.any():
        duplicate_count = int(duplicate_event_ids.sum())
        raise ValueError(f"Hay id_event duplicados. Filas afectadas: {duplicate_count}")

    df = attach_related_event_ids(df)
    unresolved_related_event_id_count = int(df.attrs.get("unresolved_related_event_id_count", 0))

    events = df[
        [
            "id_event",
            "id_match",
            "id_team",
            "id_player",
            "period",
            "minute",
            "second",
            "expandedMinute",
            "type",
            "outcomeType",
            "x",
            "y",
            "endX",
            "endY",
            "goalMouthY",
            "goalMouthZ",
            "blockedX",
            "blockedY",
            "qualifiers",
            "is_cross",
            "is_long_ball",
            "is_through_ball",
            "is_throw_in",
            "is_corner_taken",
            "is_free_kick",
            "is_goal_kick",
            "pass_length",
            "is_shot_assist",
            "body_part",
            "isTouch",
            "isShot",
            "isGoal",
            "cardType",
            "idWhoscored",
            "related_event_id",
            "related_player_id",
        ]
    ].copy()

    events = events[EVENT_COLUMNS].reset_index(drop=True)

    valid_event_ids = set(events["id_event"].astype(str))
    invalid_related_mask = events["related_event_id"].notna() & ~events["related_event_id"].isin(valid_event_ids)
    if invalid_related_mask.any():
        unresolved_related_event_id_count += report_unresolved_related_event_ids(events, invalid_related_mask)
        events.loc[invalid_related_mask, "related_event_id"] = pd.NA

    events.attrs["unresolved_related_event_id_count"] = unresolved_related_event_id_count
    events.attrs["unmapped_raw_event_count"] = unmapped_raw_event_count
    events.attrs["unmapped_raw_game_count"] = unmapped_raw_game_count
    events.attrs["abandoned_raw_event_count"] = abandoned_raw_event_count
    events.attrs["abandoned_raw_game_count"] = abandoned_raw_game_count

    return events

def run_build() -> dict:
    parse_no_args("Construye eventos canonicos desde eventos raw de WhoScored, acotados si el alcance del pipeline esta activo.")
    output_path = build_output_path()
    input_paths = build_input_paths()
    matches_path = build_matches_path()

    print("Consolidando eventos de partido en formato canonico...")
    events = build_events(input_paths, matches_path)
    events.to_csv(output_path, index=False, encoding="utf-8")
    print_result("Eventos", len(events), output_path)
    unresolved_related = int(events.attrs.get("unresolved_related_event_id_count", 0))
    print_audit("related_event_id no mapeados", unresolved_related)
    warnings = []
    if unresolved_related:
        warnings.append(f"{unresolved_related} related_event_id no mapeados")
    unmapped_raw_events = int(events.attrs.get("unmapped_raw_event_count", 0))
    unmapped_raw_games = int(events.attrs.get("unmapped_raw_game_count", 0))
    print_audit("Eventos raw WhoScored descartados sin partido canonico", unmapped_raw_events)
    if unmapped_raw_events:
        warnings.append(
            f"{unmapped_raw_events} eventos raw de WhoScored descartados "
            f"por no tener partido canonico ({unmapped_raw_games} partido(s))"
        )
    abandoned_raw_events = int(events.attrs.get("abandoned_raw_event_count", 0))
    abandoned_raw_games = int(events.attrs.get("abandoned_raw_game_count", 0))
    print_audit("Eventos raw WhoScored excluidos por partido abandonado", abandoned_raw_events)
    if abandoned_raw_events:
        warnings.append(
            f"{abandoned_raw_events} eventos raw de WhoScored excluidos "
            f"por partido abandonado ({abandoned_raw_games} partido(s))"
        )
    return {
        "input_files": [*input_paths, matches_path],
        "output_files": [output_path],
        "warnings": warnings,
        "metrics": {
            "rows": len(events),
            "raw_event_files": len(input_paths),
            "unresolved_related_event_id_count": unresolved_related,
            "unmapped_raw_event_rows": unmapped_raw_events,
            "unmapped_raw_games": unmapped_raw_games,
            "abandoned_raw_event_rows": abandoned_raw_events,
            "abandoned_raw_games": abandoned_raw_games,
        },
    }


def main() -> None:
    run_with_optional_task_result("build_events", "transform", run_build)


if __name__ == "__main__":
    main()

