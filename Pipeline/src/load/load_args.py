from __future__ import annotations

import argparse
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from src.pipeline.standalone_timing import SpanishArgumentParser, parse_args_with_standalone_timing  # noqa: E402


def parse_load_graphdb_args() -> argparse.Namespace:
    parser = SpanishArgumentParser(
        description="Carga el TTL fusionado en GraphDB.",
        usage="%(prog)s [-h] [--no-clear-before-upload]",
    )
    parser.add_argument(
        "--no-clear-before-upload",
        dest="clear_before_upload",
        action="store_false",
        default=True,
        help="No limpia los statements del repositorio antes de cargar el TTL.",
    )
    return parse_args_with_standalone_timing(parser)


def main() -> None:
    parser = SpanishArgumentParser(description="Utilidades compartidas de argumentos para scripts de load.")
    parse_args_with_standalone_timing(parser)


if __name__ == "__main__":
    main()
