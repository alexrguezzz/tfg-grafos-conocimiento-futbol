from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))
PIPELINE_MODULE_DIR = PROJECT_ROOT / "src" / "pipeline"
if str(PIPELINE_MODULE_DIR) not in sys.path:
    sys.path.append(str(PIPELINE_MODULE_DIR))

from src.pipeline.standalone_timing import SpanishArgumentParser, parse_args_with_standalone_timing  # noqa: E402
from scope_config import AVAILABLE_LEAGUES, AVAILABLE_SEASONS  # noqa: E402

LEAGUES_HELP = "Ligas disponibles: " + ", ".join(AVAILABLE_LEAGUES)
SEASONS_HELP = "Temporadas disponibles: " + ", ".join(AVAILABLE_SEASONS)


def _deduplicate(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    unique_values: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        unique_values.append(value)
    return unique_values


def parse_leagues_seasons_args(description: str) -> argparse.Namespace:
    parser = SpanishArgumentParser(
        description=description,
        usage=(
            "%(prog)s [-h] --leagues LEAGUE [LEAGUE ...] "
            "--seasons SEASON [SEASON ...]"
        ),
    )
    parser.add_argument(
        "--leagues",
        required=True,
        nargs="+",
        choices=AVAILABLE_LEAGUES,
        metavar="LEAGUE",
        help="Una o varias ligas a procesar. " + LEAGUES_HELP,
    )
    parser.add_argument(
        "--seasons",
        required=True,
        nargs="+",
        choices=AVAILABLE_SEASONS,
        metavar="SEASON",
        help="Una o varias temporadas a procesar. " + SEASONS_HELP,
    )
    args = parse_args_with_standalone_timing(parser)
    args.leagues = _deduplicate(args.leagues)
    args.seasons = _deduplicate(args.seasons)
    return args


def file_scope_fragment(values: Sequence[str]) -> str:
    return "__".join(str(value).replace(" ", "_").replace("/", "-") for value in values)


def build_raw_output_path(
    source: str,
    dataset: str,
    file_prefix: str,
    leagues: Sequence[str],
    seasons: Sequence[str],
) -> Path:
    output_dir = PROJECT_ROOT / "data" / "raw" / source / dataset
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir / f"{file_prefix}_{file_scope_fragment(leagues)}_{file_scope_fragment(seasons)}.csv"


def main() -> None:
    parser = SpanishArgumentParser(description="Utilidades compartidas de argumentos para scripts de extract.")
    parse_args_with_standalone_timing(parser)


if __name__ == "__main__":
    main()
