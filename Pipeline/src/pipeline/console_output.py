from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def format_path(path: Path | str) -> str:
    path = Path(path)
    if not path.is_absolute():
        return path.as_posix()

    try:
        return path.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()
    except ValueError:
        return str(path)


def print_result(label: str, count: int, path: Path) -> None:
    print()
    print(f"Resultado: {label}={count}, Archivo: {format_path(path)}")


def print_audit(label: str, count: int | None = None, path: Path | None = None) -> None:
    if count == 0:
        return

    if count is None:
        message = f"Auditoria: {label}"
    else:
        message = f"Auditoria: {label}={count}"

    if path is not None:
        message = f"{message}, Archivo: {format_path(path)}"

    print(message)


def print_output_file(path: Path) -> None:
    print()
    print(f"Resultado: Archivo={format_path(path)}")


def print_metric(label: str, value: object) -> None:
    print(f"Resultado: {label}={value}")


def print_validation_ok(scope: str) -> None:
    print()
    print(f"Resultado: Validacion OK, Ambito: {scope}")


def print_warning(message: str) -> None:
    print(f"Aviso: {message}")


def print_error(message: str) -> None:
    print(f"Error: {message}")


def print_examples(examples: Iterable[object]) -> None:
    examples = list(examples)
    if not examples:
        return
    print("Ejemplos:")
    for example in examples:
        print(f"  - {example}")
