from pathlib import Path
import pandas as pd
import sys
from typing import TYPE_CHECKING

from extract_args import build_raw_output_path, parse_leagues_seasons_args

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(PROJECT_ROOT))

from src.pipeline.script_result import run_with_optional_task_result  # noqa: E402
from src.pipeline.console_output import print_result  # noqa: E402

if TYPE_CHECKING:
    import soccerdata as sd


def build_input_team_competition_season_path() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "processed" / "canonical" / "team_competition_season.csv"


def build_input_teams_path() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "processed" / "canonical" / "teams.csv"


def build_output_path(leagues: list[str], seasons: list[str]) -> Path:
    return build_raw_output_path("clubelo", "team_history", "read_team_history", leagues, seasons)


def load_target_teams(leagues: list[str], seasons: list[str]) -> pd.DataFrame:
    from src.utils.text_normalization import canonicalize_team_name, normalize_competition, normalize_season

    tcs_path = build_input_team_competition_season_path()
    teams_path = build_input_teams_path()

    if not tcs_path.exists():
        raise FileNotFoundError(f"No existe: {tcs_path}")
    if not teams_path.exists():
        raise FileNotFoundError(f"No existe: {teams_path}")

    df_tcs = pd.read_csv(tcs_path)
    df_teams = pd.read_csv(teams_path)

    if "id_team" not in df_tcs.columns:
        raise ValueError("Falta la columna obligatoria 'id_team' en team_competition_season.csv")
    if "id_team" not in df_teams.columns or "name" not in df_teams.columns:
        raise ValueError("Faltan columnas obligatorias en teams.csv")

    target_competitions = {normalize_competition(league) for league in leagues}
    target_seasons = {normalize_season(season) for season in seasons}

    if "id_competition" in df_tcs.columns:
        df_tcs = df_tcs[df_tcs["id_competition"].astype(str).isin(target_competitions)]

    if "id_season" in df_tcs.columns:
        df_tcs = df_tcs[df_tcs["id_season"].astype(str).isin(target_seasons)]

    team_lookup = (
        df_teams[["id_team", "name"]]
        .dropna(subset=["id_team"])
        .drop_duplicates(subset=["id_team"])
    )
    df_tcs = df_tcs.merge(team_lookup, on="id_team", how="left")
    fallback_names = df_tcs["id_team"].astype(str).str.replace("_", " ", regex=False).map(canonicalize_team_name)
    df_tcs["name"] = df_tcs["name"].fillna(fallback_names)

    return df_tcs


def fill_missing_id_team(df_team: pd.DataFrame, team_id: str) -> pd.DataFrame:
    prepared = df_team.copy()

    if "id_team" not in prepared.columns:
        if "team_id_canonical" in prepared.columns:
            prepared["id_team"] = prepared["team_id_canonical"]
        else:
            prepared["id_team"] = team_id
    else:
        prepared["id_team"] = prepared["id_team"].astype("string")
        invalid_mask = prepared["id_team"].isna() | prepared["id_team"].astype(str).str.strip().isin(["", "nan", "<NA>"])
        prepared.loc[invalid_mask, "id_team"] = team_id

    return prepared


def fetch_clubelo_team_history(
    clubelo: "sd.ClubElo",
    team_id: str,
    team_name: str,
) -> tuple[str, pd.DataFrame]:
    df_team = clubelo.read_team_history(team_name).reset_index()

    if df_team.empty:
        raise ValueError(f"No se encontraron datos de historial en ClubElo para el equipo '{team_id}'")

    if "index" in df_team.columns and "from" not in df_team.columns:
        df_team = df_team.rename(columns={"index": "from"})

    return team_name, df_team


def run_extraction() -> dict:
    args = parse_leagues_seasons_args("Extrae historico ClubElo de equipos para una o varias ligas y temporadas.")
    import soccerdata as sd

    leagues = args.leagues
    seasons = args.seasons
    output_path = build_output_path(leagues, seasons)

    print(f"\nLeyendo historico Elo de equipos desde ClubElo: {', '.join(leagues)} / {', '.join(seasons)}...\n")
    df_teams = load_target_teams(leagues, seasons)
    clubelo = sd.ClubElo()

    dataframes: list[pd.DataFrame] = []
    missing_teams: list[str] = []
    printed_team_history_header = False

    target_teams = df_teams[["id_team", "name"]].dropna(subset=["id_team"]).drop_duplicates(subset=["id_team"])
    for team in target_teams.itertuples(index=False):
        team_id = str(team.id_team)
        team_name = str(team.name).strip()
        try:
            clubelo_name, df_team = fetch_clubelo_team_history(
                clubelo,
                team_id,
                team_name=team_name,
            )
        except Exception as exc:
            print(f"Aviso: no se encontro historial Elo para '{team_id}'. Se continua. Detalle: {exc}")
            missing_teams.append(team_id)
            continue

        df_team = fill_missing_id_team(df_team, team_id)
        if not printed_team_history_header:
            print()
            printed_team_history_header = True
        print(f"Leyendo historico Elo de: {clubelo_name}")
        dataframes.append(df_team)

    if not dataframes:
        raise ValueError(f"No se pudo extraer Elo para ningun equipo de {', '.join(leagues)} / {', '.join(seasons)}")

    df = pd.concat(dataframes, ignore_index=True)
    df.to_csv(output_path, index=False, encoding="utf-8")
    print_result("Filas", len(df), output_path)
    if missing_teams:
        print(f"Equipos sin historial Elo en ClubElo ({len(missing_teams)}): {', '.join(missing_teams)}")
    warnings = []
    if missing_teams:
        warnings.append(f"{len(missing_teams)} equipo(s) sin historial Elo en ClubElo")
    return {
        "league": leagues[0] if len(leagues) == 1 else None,
        "season": seasons[0] if len(seasons) == 1 else None,
        "input_files": [build_input_team_competition_season_path(), build_input_teams_path()],
        "output_files": [output_path],
        "warnings": warnings,
        "metrics": {
            "rows": len(df),
            "teams_requested": len(target_teams),
            "teams_extracted": len(dataframes),
            "teams_missing": len(missing_teams),
            "missing_teams": missing_teams,
        },
    }


def main() -> None:
    run_with_optional_task_result("extract_clubelo_read_team_history", "transform", run_extraction)


if __name__ == "__main__":
    main()
