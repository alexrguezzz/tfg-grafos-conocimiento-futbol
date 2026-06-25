from __future__ import annotations

from pathlib import Path
import sys

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(PROJECT_ROOT))

from src.transform.transform_args import parse_no_args  # noqa: E402

from src.pipeline.console_output import print_result  # noqa: E402
from src.transform.player_normalization import (  # noqa: E402
    PLAYER_IDENTITIES_PATH,
    is_valid_player_id_for_full_name,
    unjustified_duplicate_full_name_rows,
)
from src.utils.text_normalization import clean_identifier_text  # noqa: E402


IDENTITY_REQUIRED_COLUMNS = [
    "id_player",
    "full_name",
    "known_as",
    "id_understat",
    "id_whoscored",
    "source_player_keys",
    "competitions",
    "teams",
    "resolution_method",
]
PLAYER_COLUMNS = ["id_player", "knownAs", "fullName", "idUnderstat", "idWhoscored"]


def build_output_path() -> Path:
    output_dir = PROJECT_ROOT / "data" / "processed" / "canonical"
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir / "players.csv"


def clean_text(value: object) -> str:
    if pd.isna(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() == "nan" else text


def build_players() -> pd.DataFrame:
    if not PLAYER_IDENTITIES_PATH.exists():
        raise FileNotFoundError(
            f"No existe {PLAYER_IDENTITIES_PATH}. Ejecuta primero normalize_players.py."
        )

    identities = pd.read_csv(PLAYER_IDENTITIES_PATH, dtype="string").fillna("")
    missing = [column for column in IDENTITY_REQUIRED_COLUMNS if column not in identities.columns]
    if missing:
        raise ValueError(f"Faltan columnas en player_identities.csv: {missing}")

    players = pd.DataFrame(
        {
            "id_player": identities["id_player"],
            "knownAs": identities["known_as"],
            "fullName": identities["full_name"],
            "idUnderstat": identities["id_understat"],
            "idWhoscored": identities["id_whoscored"],
            "source_player_keys": identities["source_player_keys"],
            "competitions": identities["competitions"],
            "teams": identities["teams"],
            "resolution_method": identities["resolution_method"],
        }
    )
    players["knownAs"] = players["knownAs"].map(clean_text)
    players["fullName"] = players["fullName"].map(clean_text)
    invalid_ids = []
    for row in players.itertuples(index=False):
        full_name = clean_text(getattr(row, "fullName", ""))
        expected_id = clean_identifier_text(full_name).lower()
        player_id = clean_text(getattr(row, "id_player", ""))
        if not is_valid_player_id_for_full_name(
            player_id,
            full_name,
            known_as=getattr(row, "knownAs", ""),
            id_understat=getattr(row, "idUnderstat", ""),
            id_whoscored=getattr(row, "idWhoscored", ""),
            source_player_keys=getattr(row, "source_player_keys", ""),
            competitions=getattr(row, "competitions", ""),
            teams=getattr(row, "teams", ""),
            resolution_method=getattr(row, "resolution_method", ""),
        ):
            invalid_ids.append(
                {
                    "id_player": player_id,
                    "knownAs": clean_text(getattr(row, "knownAs", "")),
                    "fullName": full_name,
                    "expected": expected_id or "normalize(knownAs)",
                }
            )
    if invalid_ids:
        raise ValueError(
            "Hay id_player que no cumplen la politica normalize(fullName) o normalize(knownAs) sin sufijos de fuente. "
            f"Ejemplos: {invalid_ids[:20]}"
        )
    duplicated_ids = players["id_player"].duplicated(keep=False)
    if duplicated_ids.any():
        examples = players.loc[duplicated_ids, ["id_player", "knownAs", "fullName"]].head(20).to_dict(orient="records")
        raise ValueError(f"Hay id_player duplicados en player_identities.csv: {examples}")
    unjustified_duplicate_names = unjustified_duplicate_full_name_rows(players)
    if not unjustified_duplicate_names.empty:
        examples = unjustified_duplicate_names[
            ["id_player", "knownAs", "fullName", "idUnderstat", "idWhoscored"]
        ].head(20).to_dict(orient="records")
        raise ValueError(
            "Hay fullName duplicados no justificados por homonimia desambiguada; deben fusionarse o enriquecerse. "
            f"Ejemplos: {examples}"
        )
    return players[PLAYER_COLUMNS].sort_values(by=["knownAs", "fullName", "id_player"]).reset_index(drop=True)


def main() -> None:
    parse_no_args("Construye jugadores canonicos desde artefactos de normalizacion de jugadores.")
    output_path = build_output_path()

    print("Generando catalogo canonico de jugadores...")
    players = build_players()
    players.to_csv(output_path, index=False, encoding="utf-8")
    print_result("Jugadores", len(players), output_path)


if __name__ == "__main__":
    main()

