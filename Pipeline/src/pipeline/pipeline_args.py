from __future__ import annotations

import argparse
from collections.abc import Sequence

from standalone_timing import SpanishArgumentParser, positive_int
from scope_config import AVAILABLE_LEAGUES, AVAILABLE_SEASONS, DEFAULT_EVENTS_RDF_CHUNK_SIZE

AVAILABLE_PHASES = ["extract", "transform", "rdf", "merge", "validate", "load", "all"]
PHASES_REQUIRING_SCOPE = {"extract", "transform", "all"}
PHASES_USING_SCOPE = {"extract", "transform", "validate"}
PHASES_IGNORING_SCOPE = {"rdf", "merge", "load"}

LEAGUES_HELP = "Ligas disponibles: " + ", ".join(AVAILABLE_LEAGUES)
SEASONS_HELP = "Temporadas disponibles: " + ", ".join(AVAILABLE_SEASONS)
PHASES_HELP = "Fases disponibles: " + ", ".join(AVAILABLE_PHASES)
SCOPE_RULES = """Reglas de alcance:
  - --leagues y --seasons son obligatorios para extract, transform y all.
  - Son opcionales para validate; solo los validadores compatibles usan el alcance.
  - rdf, merge y load los ignoran.
  - Si se indica uno, tambien debe indicarse el otro."""


def scope_required_for_phases(phases: Sequence[str]) -> bool:
    return any(phase in PHASES_REQUIRING_SCOPE for phase in phases)


def build_pipeline_arg_parser() -> argparse.ArgumentParser:
    parser = SpanishArgumentParser(
        description="Ejecuta una o varias fases del pipeline SoccerData.",
        allow_abbrev=False,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        usage=(
            "%(prog)s [-h] --phases PHASE [PHASE ...] "
            "[--leagues LEAGUE [LEAGUE ...] --seasons SEASON [SEASON ...]] "
            "[--events-rdf-chunk-size ROWS] "
            "[--no-clear-before-upload] [--dry-run]"
        ),
        epilog=SCOPE_RULES,
    )
    parser.add_argument(
        "--phases",
        metavar="PHASE",
        required=True,
        nargs="+",
        choices=AVAILABLE_PHASES,
        help="Una o varias fases a ejecutar. " + PHASES_HELP + ". Usa 'all' solo para ejecutar el pipeline completo.",
    )
    parser.add_argument(
        "--leagues",
        metavar="LEAGUE",
        nargs="+",
        help=(
            "Una o varias ligas para usar como alcance del pipeline. "
            "Obligatorio para extract, transform y all; opcional para validate. "
            + LEAGUES_HELP
        ),
    )
    parser.add_argument(
        "--seasons",
        metavar="SEASON",
        nargs="+",
        help=(
            "Una o varias temporadas para usar como alcance del pipeline. "
            "Obligatorio para extract, transform y all; opcional para validate. "
            + SEASONS_HELP
        ),
    )
    parser.add_argument(
        "--events-rdf-chunk-size",
        type=positive_int,
        default=DEFAULT_EVENTS_RDF_CHUNK_SIZE,
        metavar="ROWS",
        help=(
            "Numero de filas de eventos que se procesan por bloque RDF en streaming. "
            f"Valor por defecto: {DEFAULT_EVENTS_RDF_CHUNK_SIZE}."
        ),
    )
    parser.add_argument(
        "--no-clear-before-upload",
        dest="clear_before_upload",
        action="store_false",
        default=True,
        help="No limpia los statements del repositorio GraphDB antes de cargar el TTL en la fase load.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Muestra el plan de ejecucion seleccionado sin ejecutar scripts.",
    )
    return parser


def parse_pipeline_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = build_pipeline_arg_parser()
    args = parser.parse_args(argv)

    if "all" in args.phases and len(args.phases) > 1:
        parser.error("'all' no se puede combinar con otras fases. Usa '--phases all' sin mas fases.")

    args.leagues = args.leagues or []
    args.seasons = args.seasons or []

    if bool(args.leagues) != bool(args.seasons):
        parser.error("--leagues y --seasons deben indicarse juntos.")

    if scope_required_for_phases(args.phases) and not args.leagues:
        parser.error("--leagues y --seasons son obligatorios al ejecutar extract, transform o all.")

    invalid_leagues = [league for league in args.leagues if league not in AVAILABLE_LEAGUES]
    if invalid_leagues:
        parser.error(f"Liga(s) no validas: {', '.join(invalid_leagues)}. {LEAGUES_HELP}")

    invalid_seasons = [season for season in args.seasons if season not in AVAILABLE_SEASONS]
    if invalid_seasons:
        parser.error(f"Temporada(s) no validas: {', '.join(invalid_seasons)}. {SEASONS_HELP}")

    return args


def main() -> None:
    parser = SpanishArgumentParser(
        description="Explica las reglas de alcance de argumentos del pipeline SoccerData.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=SCOPE_RULES,
    )
    parser.parse_args()
    print(SCOPE_RULES)


if __name__ == "__main__":
    main()
