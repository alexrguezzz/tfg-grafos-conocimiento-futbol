from pathlib import Path
import sys
import shutil

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(PROJECT_ROOT))

from src.rdf.rdf_args import parse_no_args
from src.pipeline.console_output import print_output_file


def build_input_paths() -> list[Path]:
    ttl_dir = PROJECT_ROOT / "data" / "ttl"

    return [
        ttl_dir / "competitions.ttl",
        ttl_dir / "seasons.ttl",
        ttl_dir / "teams.ttl",
        ttl_dir / "stadiums.ttl",
        ttl_dir / "matches.ttl",
        ttl_dir / "weather_observations.ttl",
        ttl_dir / "team_match_participation.ttl",
        ttl_dir / "team_competition_season.ttl",
        ttl_dir / "elo_history.ttl",
        ttl_dir / "players.ttl",
        ttl_dir / "player_match_participation.ttl",
        ttl_dir / "player_competition_season_stats.ttl",
        ttl_dir / "events.ttl",
    ]


def build_output_path() -> Path:
    output_dir = PROJECT_ROOT / "data" / "ttl"
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir / "full_knowledge_graph.ttl"


def main() -> None:
    parse_no_args("Fusiona los TTL generados en un unico grafo de conocimiento.")
    input_paths = build_input_paths()
    output_path = build_output_path()

    print("Uniendo archivos TTL...")

    for path in input_paths:
        if not path.exists():
            raise FileNotFoundError(
                f"No existe el archivo TTL esperado: {path}"
            )

    with output_path.open("wb") as output_file:
        for path in input_paths:
            print(f"Anadiendo: {path.name}")
            with path.open("rb") as input_file:
                shutil.copyfileobj(input_file, output_file, length=1024 * 1024)
            output_file.write(b"\n\n")

    print_output_file(output_path)


if __name__ == "__main__":
    main()
