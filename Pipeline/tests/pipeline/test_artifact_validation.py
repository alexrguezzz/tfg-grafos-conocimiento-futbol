from __future__ import annotations

import pytest

from artifact_validation import validate_csv_file, validate_json_file, validate_ttl_file


def test_validate_csv_file_rejects_missing_required_columns(runtime_dir) -> None:
    path = runtime_dir / "players.csv"
    path.write_text("id_player\nplayer_1\n", encoding="utf-8")

    result = validate_csv_file(
        path,
        required_columns=("id_player", "knownAs"),
        id_columns=("id_player",),
    )

    assert not result.ok
    assert any("faltan columnas obligatorias" in error for error in result.errors)


def test_validate_csv_file_rejects_duplicate_ids(runtime_dir) -> None:
    path = runtime_dir / "players.csv"
    path.write_text("id_player,knownAs\nplayer_1,A\nplayer_1,B\n", encoding="utf-8")

    result = validate_csv_file(
        path,
        required_columns=("id_player", "knownAs"),
        id_columns=("id_player",),
    )

    assert not result.ok
    assert any("duplicado" in error for error in result.errors)


def test_validate_json_file_rejects_invalid_json(runtime_dir) -> None:
    path = runtime_dir / "report.json"
    path.write_text("{invalid json", encoding="utf-8")

    result = validate_json_file(path)

    assert not result.ok
    assert any("No se pudo leer" in error for error in result.errors)


def test_validate_ttl_file_rejects_invalid_turtle(runtime_dir) -> None:
    pytest.importorskip("rdflib")
    path = runtime_dir / "broken.ttl"
    path.write_text("@prefix ex: <http://example.com/> .\nex:s ex:p .\n", encoding="utf-8")

    result = validate_ttl_file(path)

    assert not result.ok
    assert any("No se pudo parsear" in error for error in result.errors)
