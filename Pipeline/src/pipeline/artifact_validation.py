from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import json
import sys

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors

    def merge(self, other: "ValidationResult") -> None:
        self.errors.extend(other.errors)
        self.warnings.extend(other.warnings)
        self.metrics.update(other.metrics)


def validate_csv_file(
    path: Path,
    *,
    label: str | None = None,
    required_columns: tuple[str, ...] = (),
    id_columns: tuple[str, ...] = (),
    allow_empty: bool = False,
) -> ValidationResult:
    result = ValidationResult()
    artifact_label = label or path.name

    if not path.exists():
        result.errors.append(f"No existe el CSV esperado: {path}")
        return result
    if path.stat().st_size == 0:
        result.errors.append(f"CSV vacio sin cabecera: {path}")
        return result

    try:
        df = pd.read_csv(path, dtype="string")
    except Exception as exc:
        result.errors.append(f"No se pudo leer {artifact_label}: {exc}")
        return result

    row_count = len(df)
    result.metrics[f"{artifact_label}.rows"] = row_count
    if row_count == 0 and not allow_empty:
        result.errors.append(f"{artifact_label} no contiene filas")

    missing_columns = [column for column in required_columns if column not in df.columns]
    if missing_columns:
        result.errors.append(f"{artifact_label}: faltan columnas obligatorias: {missing_columns}")

    for column in id_columns:
        if column not in df.columns:
            continue
        text_values = df[column].fillna("").astype(str).str.strip()
        missing_ids = int(text_values.isin(["", "nan", "<NA>", "<na>"]).sum())
        if missing_ids:
            result.errors.append(f"{artifact_label}: {missing_ids} valor(es) vacios en {column}")
        duplicate_count = int(text_values[text_values != ""].duplicated(keep=False).sum())
        if duplicate_count:
            result.errors.append(f"{artifact_label}: {duplicate_count} fila(s) con {column} duplicado")

    return result


def validate_json_file(path: Path, *, label: str | None = None) -> ValidationResult:
    result = ValidationResult()
    artifact_label = label or path.name

    if not path.exists():
        result.errors.append(f"No existe el JSON esperado: {path}")
        return result
    if path.stat().st_size == 0:
        result.errors.append(f"JSON vacio: {path}")
        return result
    try:
        with path.open(encoding="utf-8") as handle:
            json.load(handle)
    except Exception as exc:
        result.errors.append(f"No se pudo leer {artifact_label}: {exc}")
    return result


def validate_existing_file(path: Path, *, label: str | None = None) -> ValidationResult:
    result = ValidationResult()
    artifact_label = label or path.name

    if not path.exists():
        result.errors.append(f"No existe el archivo esperado: {path}")
        return result
    if path.stat().st_size == 0:
        result.errors.append(f"Archivo vacio: {path}")
        return result

    result.metrics[f"{artifact_label}.bytes"] = path.stat().st_size
    return result


def validate_ttl_file(path: Path, *, label: str | None = None) -> ValidationResult:
    result = ValidationResult()
    artifact_label = label or path.name

    if not path.exists():
        result.errors.append(f"No existe el TTL esperado: {path}")
        return result
    if path.stat().st_size == 0:
        result.errors.append(f"TTL vacio: {path}")
        return result

    try:
        from src.validation.validate_ttl import validate_ttl_file as parse_ttl_file

        triple_count = parse_ttl_file(path)
    except Exception as exc:
        error_detail = str(exc) or type(exc).__name__
        result.errors.append(f"No se pudo parsear {artifact_label}: {error_detail}")
        return result

    result.metrics[f"{artifact_label}.triples"] = triple_count
    if triple_count <= 0:
        result.errors.append(f"{artifact_label} no contiene tripletas")
    return result
