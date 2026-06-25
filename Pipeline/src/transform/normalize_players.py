from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(PROJECT_ROOT))

from src.transform.transform_args import parse_no_args  # noqa: E402

from src.pipeline.console_output import print_audit, print_result  # noqa: E402
from src.transform.player_normalization import (  # noqa: E402
    PLAYER_ALIAS_MAP_PATH,
    PLAYER_IDENTITIES_PATH,
    PLAYER_REPORT_PATH,
    PLAYER_REVIEW_QUEUE_PATH,
    normalize_players,
)


def main() -> None:
    parse_no_args("Construye artefactos de normalizacion de jugadores desde observaciones raw, acotados si el alcance del pipeline esta activo.")
    print("Normalizando identidades canonicas de jugadores...")
    identities, alias_map, review_queue, report = normalize_players()
    print("\nArtefactos principales:")
    print_result("Jugadores canonicos", len(identities), PLAYER_IDENTITIES_PATH)
    print_result("Alias/contextos para mapeo", len(alias_map), PLAYER_ALIAS_MAP_PATH)
    pending_review = len(review_queue)
    if pending_review:
        print("\nArtefactos de auditoria:")
        print_audit("Casos pendientes de revision", pending_review, PLAYER_REVIEW_QUEUE_PATH)
        print_audit("Reporte", None, PLAYER_REPORT_PATH)

    summary_parts = [
        f"antes={report['players_before_source_keys']}",
        f"despues={report['players_after']}",
        f"gemini={report['gemini_calls']}",
        f"cache={report['gemini_cache_hits']}",
        f"enriquecimiento_nombres={report.get('name_enrichment_gemini_calls', 0)}",
        f"reintentos_enriquecimiento={report.get('name_enrichment_retry_calls', 0)}",
        f"reparacion_duplicados={report.get('name_collision_repair_calls', 0)}",
    ]
    if pending_review:
        summary_parts.append(f"pendientes={pending_review}")
    print("Resumen: " + ", ".join(summary_parts))


if __name__ == "__main__":
    main()

