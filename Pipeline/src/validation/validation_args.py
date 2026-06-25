from __future__ import annotations

import argparse
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from src.pipeline.standalone_timing import SpanishArgumentParser, parse_args_with_standalone_timing  # noqa: E402


def parse_no_args(description: str) -> argparse.Namespace:
    parser = SpanishArgumentParser(description=description)
    return parse_args_with_standalone_timing(parser)


def main() -> None:
    parse_no_args("Utilidades compartidas de argumentos para scripts de validacion.")


if __name__ == "__main__":
    main()
