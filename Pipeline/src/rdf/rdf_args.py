from __future__ import annotations

import argparse
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))
PIPELINE_MODULE_DIR = PROJECT_ROOT / "src" / "pipeline"
if str(PIPELINE_MODULE_DIR) not in sys.path:
    sys.path.append(str(PIPELINE_MODULE_DIR))

from src.pipeline.standalone_timing import SpanishArgumentParser, parse_args_with_standalone_timing, positive_int  # noqa: E402
from scope_config import DEFAULT_EVENTS_RDF_CHUNK_SIZE  # noqa: E402


def parse_no_args(description: str, epilog: str | None = None) -> argparse.Namespace:
    parser = SpanishArgumentParser(description=description, epilog=epilog)
    return parse_args_with_standalone_timing(parser)


def parse_events_args() -> argparse.Namespace:
    parser = SpanishArgumentParser(description="Genera RDF para eventos canonicos.")
    parser.add_argument(
        "--events-rdf-chunk-size",
        type=positive_int,
        default=DEFAULT_EVENTS_RDF_CHUNK_SIZE,
        metavar="ROWS",
        help=(
            "Numero de filas de eventos que se procesan por bloque en streaming. "
            f"Valor por defecto: {DEFAULT_EVENTS_RDF_CHUNK_SIZE}."
        ),
    )
    return parse_args_with_standalone_timing(parser)


def main() -> None:
    parse_no_args("Utilidades compartidas de argumentos para scripts RDF.")


if __name__ == "__main__":
    main()
