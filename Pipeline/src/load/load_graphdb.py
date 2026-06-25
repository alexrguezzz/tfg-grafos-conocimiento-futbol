from pathlib import Path
import sys
import os

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(PROJECT_ROOT))

import requests

from src.pipeline.console_output import format_path
from src.load.load_args import parse_load_graphdb_args


# Configuration
GRAPHDB_BASE_URL = os.getenv("GRAPHDB_BASE_URL", "http://localhost:7200")
REPOSITORY_ID = os.getenv("GRAPHDB_REPOSITORY_ID", "TFG_SoccerData")
TTL_FILENAME = os.getenv("GRAPHDB_TTL_FILENAME", "full_knowledge_graph.ttl")
CONNECT_TIMEOUT_SECONDS = float(os.getenv("GRAPHDB_CONNECT_TIMEOUT_SECONDS", "30"))
READ_TIMEOUT_SECONDS = None
if os.getenv("GRAPHDB_READ_TIMEOUT_SECONDS") is not None:
    READ_TIMEOUT_SECONDS = float(os.getenv("GRAPHDB_READ_TIMEOUT_SECONDS", "0"))

USERNAME = os.getenv("GRAPHDB_USERNAME")
PASSWORD = os.getenv("GRAPHDB_PASSWORD")
CONTEXT_GRAPH_URI = os.getenv("GRAPHDB_CONTEXT_GRAPH_URI")
SEPARATOR = "=" * 72


def build_ttl_path() -> Path:
    return PROJECT_ROOT / "data" / "ttl" / TTL_FILENAME


def build_statements_url() -> str:
    return f"{GRAPHDB_BASE_URL}/repositories/{REPOSITORY_ID}/statements"


def build_auth():
    if USERNAME and PASSWORD:
        return (USERNAME, PASSWORD)
    return None


def clear_existing_data() -> None:
    url = build_statements_url()
    params = {}

    if CONTEXT_GRAPH_URI:
        params["context"] = f"<{CONTEXT_GRAPH_URI}>"

    print()
    print(SEPARATOR)
    print("Limpiando datos previos en GraphDB...")
    print(SEPARATOR)

    response = requests.delete(
        url,
        params=params,
        auth=build_auth(),
        timeout=120,
    )

    if response.status_code not in (200, 204):
        raise RuntimeError(
            f"Error al limpiar datos en GraphDB.\n"
            f"Status code: {response.status_code}\n"
            f"Respuesta: {response.text}"
        )

    print("Limpieza completada correctamente.")
    print()


def upload_ttl_file(ttl_path: Path, connect_timeout: float, read_timeout: float | None) -> None:
    if not ttl_path.exists():
        raise FileNotFoundError(f"No existe el archivo TTL esperado: {ttl_path}")

    url = build_statements_url()
    params = {}

    if CONTEXT_GRAPH_URI:
        params["context"] = f"<{CONTEXT_GRAPH_URI}>"

    headers = {"Content-Type": "text/turtle"}

    print(SEPARATOR)
    print("Cargando TTL en GraphDB...")
    print(SEPARATOR)
    print(f"Repositorio: {REPOSITORY_ID}")
    print(f"Archivo: {format_path(ttl_path)}")
    if CONTEXT_GRAPH_URI:
        print(f"Grafo nombrado: {CONTEXT_GRAPH_URI}")
    else:
        print("Grafo por defecto")
    print()

    with ttl_path.open("rb") as file_handle:
        response = requests.post(
            url,
            params=params,
            headers=headers,
            data=file_handle,
            auth=build_auth(),
            timeout=(connect_timeout, read_timeout),
        )

    if response.status_code not in (200, 204):
        raise RuntimeError(
            f"Error al cargar datos en GraphDB.\n"
            f"Status code: {response.status_code}\n"
            f"Respuesta: {response.text}"
        )

    print("Carga completada correctamente.")
    print()


def main() -> None:
    args = parse_load_graphdb_args()
    ttl_path = build_ttl_path()
    if args.clear_before_upload:
        clear_existing_data()
    upload_ttl_file(
        ttl_path,
        connect_timeout=CONNECT_TIMEOUT_SECONDS,
        read_timeout=READ_TIMEOUT_SECONDS,
    )


if __name__ == "__main__":
    main()
