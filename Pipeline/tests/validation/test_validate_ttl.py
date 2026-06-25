from __future__ import annotations

import pytest

from src.validation.validate_ttl import (
    build_input_paths,
    validate_merged_ttl_file,
    validate_ttl_file,
)


def test_validate_ttl_file_accepts_valid_turtle(runtime_dir) -> None:
    pytest.importorskip("rdflib")
    path = runtime_dir / "valid.ttl"
    path.write_text(
        "@prefix ex: <http://example.com/> .\n"
        "ex:subject ex:predicate ex:object .\n",
        encoding="utf-8",
    )

    assert validate_ttl_file(path) == 1


def test_validate_ttl_covers_individual_ttls_and_full_graph() -> None:
    names = {path.name for path in build_input_paths()}

    assert "events.ttl" in names
    assert "player_match_participation.ttl" in names
    assert "full_knowledge_graph.ttl" in names
    assert len(names) == 14


def test_validate_merged_ttl_file_accepts_expected_concatenation_size(runtime_dir) -> None:
    first = runtime_dir / "first.ttl"
    second = runtime_dir / "second.ttl"
    merged = runtime_dir / "full_knowledge_graph.ttl"
    first.write_text("@prefix ex: <http://example.com/> .\nex:a ex:p ex:b .\n", encoding="utf-8")
    second.write_text("@prefix ex: <http://example.com/> .\nex:c ex:p ex:d .\n", encoding="utf-8")
    merged.write_bytes(first.read_bytes() + b"\n\n" + second.read_bytes() + b"\n\n")

    assert validate_merged_ttl_file(merged, [first, second], {first: 1, second: 1}) == 2


def test_validate_merged_ttl_file_rejects_unexpected_size(runtime_dir) -> None:
    source = runtime_dir / "source.ttl"
    merged = runtime_dir / "full_knowledge_graph.ttl"
    source.write_text("@prefix ex: <http://example.com/> .\nex:a ex:p ex:b .\n", encoding="utf-8")
    merged.write_bytes(source.read_bytes())

    with pytest.raises(ValueError, match="tamano inesperado"):
        validate_merged_ttl_file(merged, [source], {source: 1})
