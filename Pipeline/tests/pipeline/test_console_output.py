from __future__ import annotations

from pathlib import Path

from src.pipeline.console_output import PROJECT_ROOT, format_path, print_audit, print_result


def test_print_audit_skips_zero_count(capsys) -> None:
    print_audit("Pendientes", 0, Path("audit.csv"))

    assert capsys.readouterr().out == ""


def test_print_audit_prints_positive_count_with_path(capsys) -> None:
    print_audit("Pendientes", 2, Path("audit.csv"))

    assert capsys.readouterr().out == "Auditoria: Pendientes=2, Archivo: audit.csv\n"


def test_print_audit_prints_without_path(capsys) -> None:
    print_audit("Pendientes", 2)

    assert capsys.readouterr().out == "Auditoria: Pendientes=2\n"


def test_format_path_makes_project_paths_relative() -> None:
    path = PROJECT_ROOT / "data" / "processed" / "canonical" / "matches.csv"

    assert format_path(path) == "data/processed/canonical/matches.csv"


def test_print_result_uses_relative_project_path(capsys) -> None:
    path = PROJECT_ROOT / "data" / "processed" / "canonical" / "matches.csv"

    print_result("Partidos", 10, path)

    assert capsys.readouterr().out == "\nResultado: Partidos=10, Archivo: data/processed/canonical/matches.csv\n"
