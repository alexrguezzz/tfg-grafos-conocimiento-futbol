from __future__ import annotations

from pathlib import Path
import sys
import codecs
import re
from collections.abc import Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(PROJECT_ROOT))

from src.validation.validation_args import parse_no_args  # noqa: E402
from src.pipeline.console_output import format_path, print_metric, print_validation_ok  # noqa: E402
from src.pipeline.script_result import run_with_optional_task_result  # noqa: E402


TTL_DIR = PROJECT_ROOT / "data" / "ttl"
MERGED_TTL_FILENAME = "full_knowledge_graph.ttl"
MERGE_SEPARATOR_SIZE_BYTES = len(b"\n\n")
FULL_PARSE_MAX_BYTES = 128 * 1024 * 1024
STREAM_CHUNK_BYTES = 64 * 1024 * 1024

TURTLE_BLOCK_SEPARATOR_RE = re.compile(r"(?:\r?\n[ \t]*){2,}")
TURTLE_DIRECTIVE_RE = re.compile(r"^\s*(?:@prefix|@base|prefix|base)\b", re.IGNORECASE)


_ARGS = parse_no_args("Valida la sintaxis de los TTL generados.") if __name__ == "__main__" else None


def build_individual_input_paths() -> list[Path]:
    expected = [
        "competitions.ttl",
        "seasons.ttl",
        "teams.ttl",
        "stadiums.ttl",
        "matches.ttl",
        "weather_observations.ttl",
        "team_match_participation.ttl",
        "team_competition_season.ttl",
        "elo_history.ttl",
        "players.ttl",
        "player_match_participation.ttl",
        "player_competition_season_stats.ttl",
        "events.ttl",
    ]
    return [TTL_DIR / filename for filename in expected]


def build_merged_input_path() -> Path:
    return TTL_DIR / MERGED_TTL_FILENAME


def build_input_paths(*, include_merged: bool = True) -> list[Path]:
    paths = build_individual_input_paths()
    if include_merged:
        paths.append(build_merged_input_path())
    return paths


def _exception_message(exc: Exception) -> str:
    return str(exc) or type(exc).__name__


def _build_syntax_only_graph():
    from rdflib import Graph

    class SyntaxOnlyGraph(Graph):
        def __init__(self) -> None:
            super().__init__()
            self.triple_count = 0

        def add(self, triple):  # type: ignore[no-untyped-def]
            self.triple_count += 1
            return self

    return SyntaxOnlyGraph()


def _parse_ttl_text(text: str, *, path: Path, chunk_number: int | None = None) -> int:
    graph = _build_syntax_only_graph()
    try:
        graph.parse(data=text, format="turtle")
    except Exception as exc:
        location = f" en bloque {chunk_number}" if chunk_number is not None else ""
        raise ValueError(f"{path.name}{location}: {_exception_message(exc)}") from exc
    return graph.triple_count


def _parse_ttl_path(path: Path) -> int:
    graph = _build_syntax_only_graph()
    try:
        graph.parse(path, format="turtle")
    except Exception as exc:
        raise ValueError(f"{path.name}: {_exception_message(exc)}") from exc
    return graph.triple_count


def _remember_turtle_directives(text: str, directives: list[str], seen: set[str]) -> None:
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or not TURTLE_DIRECTIVE_RE.match(stripped):
            continue
        if stripped not in seen:
            directives.append(stripped)
            seen.add(stripped)


def _with_directives(text: str, directives: list[str]) -> str:
    if not directives:
        return text
    return "\n".join(directives) + "\n\n" + text


def _parse_streamed_blocks(
    path: Path,
    *,
    chunk_size_bytes: int = STREAM_CHUNK_BYTES,
) -> int:
    decoder = codecs.getincrementaldecoder("utf-8")()
    directives: list[str] = []
    seen_directives: set[str] = set()
    buffer = ""
    triple_count = 0
    chunk_number = 0
    max_unsplit_buffer_chars = chunk_size_bytes * 4

    with path.open("rb") as handle:
        while raw := handle.read(chunk_size_bytes):
            buffer += decoder.decode(raw)
            parts = TURTLE_BLOCK_SEPARATOR_RE.split(buffer)
            if len(parts) == 1:
                if len(buffer) > max_unsplit_buffer_chars:
                    raise ValueError(
                        f"{path.name}: no se encontro un separador de bloques Turtle "
                        f"en mas de {max_unsplit_buffer_chars} caracteres"
                    )
                continue

            complete_text = "\n\n".join(part for part in parts[:-1] if part.strip())
            buffer = parts[-1]
            if not complete_text.strip():
                continue

            _remember_turtle_directives(complete_text, directives, seen_directives)
            chunk_number += 1
            triple_count += _parse_ttl_text(
                _with_directives(complete_text, directives),
                path=path,
                chunk_number=chunk_number,
            )

    buffer += decoder.decode(b"", final=True)
    if buffer.strip():
        _remember_turtle_directives(buffer, directives, seen_directives)
        chunk_number += 1
        triple_count += _parse_ttl_text(
            _with_directives(buffer, directives),
            path=path,
            chunk_number=chunk_number,
        )

    return triple_count


def validate_ttl_file(
    path: Path,
    *,
    large_file_threshold_bytes: int = FULL_PARSE_MAX_BYTES,
    chunk_size_bytes: int = STREAM_CHUNK_BYTES,
) -> int:
    if not path.exists():
        raise FileNotFoundError(f"No existe el TTL esperado: {path}")
    if path.stat().st_size == 0:
        raise ValueError(f"El TTL esta vacio: {path}")

    if path.stat().st_size > large_file_threshold_bytes:
        return _parse_streamed_blocks(path, chunk_size_bytes=chunk_size_bytes)
    return _parse_ttl_path(path)


def validate_ttl_files(paths: Sequence[Path]) -> dict[Path, int]:
    triple_counts: dict[Path, int] = {}
    for path in paths:
        triple_counts[path] = validate_ttl_file(path)
    return triple_counts


def validate_merged_ttl_file(
    path: Path,
    source_paths: Sequence[Path],
    source_triple_counts: dict[Path, int],
) -> int:
    if not path.exists():
        raise FileNotFoundError(f"No existe el TTL esperado: {path}")
    if path.stat().st_size == 0:
        raise ValueError(f"El TTL esta vacio: {path}")

    expected_size = sum(source_path.stat().st_size for source_path in source_paths)
    expected_size += MERGE_SEPARATOR_SIZE_BYTES * len(source_paths)
    actual_size = path.stat().st_size
    if actual_size != expected_size:
        raise ValueError(
            f"{path.name}: tamano inesperado para el TTL fusionado. "
            f"Esperado={expected_size} bytes, actual={actual_size} bytes"
        )

    return sum(source_triple_counts[source_path] for source_path in source_paths)


def run_validation() -> dict:
    if _ARGS is None:
        parse_no_args("Valida la sintaxis de los TTL generados.")

    print("Validando sintaxis TTL...")
    individual_paths = build_individual_input_paths()
    triple_counts = validate_ttl_files(individual_paths)

    merged_path = build_merged_input_path()
    merged_triple_count = validate_merged_ttl_file(
        merged_path,
        individual_paths,
        triple_counts,
    )
    triple_counts[merged_path] = merged_triple_count

    for path, triple_count in triple_counts.items():
        if path.name == MERGED_TTL_FILENAME:
            print(
                f"Resultado: Tripletas={triple_count}, Archivo={format_path(path)}, "
                "Modo=merge verificado sin reparsear"
            )
        else:
            print(f"Resultado: Tripletas={triple_count}, Archivo={format_path(path)}, Modo=parseado")

    print_validation_ok("TTL")
    print_metric("Archivos TTL", len(triple_counts))
    print_metric("Tripletas knowledge graph", merged_triple_count)
    return {
        "input_files": list(triple_counts),
        "metrics": {
            "ttl_files": len(triple_counts),
            "ttl_files_parsed": len(individual_paths),
            "merged_ttl_parse_skipped": True,
            "total_triples": sum(triple_counts.values()),
            "knowledge_graph_triples": merged_triple_count,
            "triples_by_file": {path.name: count for path, count in triple_counts.items()},
        },
    }


def main() -> None:
    run_with_optional_task_result("validate_ttl", "validate", run_validation)


if __name__ == "__main__":
    main()
