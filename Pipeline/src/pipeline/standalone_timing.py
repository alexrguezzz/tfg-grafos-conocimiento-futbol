from __future__ import annotations

import argparse
import atexit
import os
import sys
import time
from typing import Any


PIPELINE_MANAGED_STEP_ENV = "SOCCERDATA_PIPELINE_MANAGED_STEP"

_started_at: float | None = None
_printed = False
_error_seen = False
_previous_excepthook: Any = None


class SpanishArgumentParser(argparse.ArgumentParser):
    def __init__(self, *args, **kwargs) -> None:
        kwargs["add_help"] = False
        super().__init__(*args, **kwargs)
        self.add_argument(
            "-h",
            "--help",
            action="help",
            default=argparse.SUPPRESS,
            help="muestra este mensaje de ayuda y sale",
        )
        self._positionals.title = "argumentos posicionales"
        self._optionals.title = "opciones"

    def format_usage(self) -> str:
        return self._translate_usage(super().format_usage())

    def format_help(self) -> str:
        return self._translate_usage(super().format_help())

    @staticmethod
    def _translate_usage(text: str) -> str:
        return text.replace("usage:", "uso:", 1)


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("El valor debe ser mayor que 0.")
    return parsed


def _enabled() -> bool:
    return not os.getenv(PIPELINE_MANAGED_STEP_ENV)


def start_standalone_timer() -> None:
    global _started_at, _previous_excepthook
    if not _enabled() or _started_at is not None:
        return

    _started_at = time.perf_counter()
    _previous_excepthook = sys.excepthook
    sys.excepthook = _record_exception
    atexit.register(_finish_at_exit)


def cancel_standalone_timer() -> None:
    global _printed
    _printed = True


def finish_standalone_timer(status: str = "OK", *, elapsed: float | None = None) -> None:
    global _printed
    if not _enabled() or _printed:
        return

    if elapsed is None:
        if _started_at is None:
            return
        elapsed = time.perf_counter() - _started_at

    _printed = True
    normalized_status = status.upper()
    print()
    if normalized_status == "ERROR":
        print(f"PASO [ERROR] fallido en {elapsed:.2f}s")
    elif normalized_status == "WARNING":
        print(f"PASO [AVISO] finalizado en {elapsed:.2f}s")
    else:
        print(f"PASO [{normalized_status}] finalizado en {elapsed:.2f}s")


def parse_args_with_standalone_timing(parser: argparse.ArgumentParser) -> argparse.Namespace:
    start_standalone_timer()
    try:
        return parser.parse_args()
    except SystemExit as exc:
        code = exc.code
        if code in (None, 0):
            cancel_standalone_timer()
        else:
            finish_standalone_timer("ERROR")
        raise


def _record_exception(exc_type, exc_value, traceback) -> None:  # type: ignore[no-untyped-def]
    global _error_seen
    _error_seen = True
    hook = _previous_excepthook or sys.__excepthook__
    hook(exc_type, exc_value, traceback)


def _finish_at_exit() -> None:
    if _printed:
        return
    finish_standalone_timer("ERROR" if _error_seen else "OK")
