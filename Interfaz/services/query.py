from __future__ import annotations

import os
from threading import local

import requests


QUERY_TIMEOUT_SECONDS = float(os.getenv("GRAPHDB_QUERY_TIMEOUT", "12"))
STATEMENT_TIMEOUT_SECONDS = float(os.getenv("GRAPHDB_STATEMENT_TIMEOUT", "2"))
_THREAD_LOCAL = local()


def session() -> requests.Session:
    current = getattr(_THREAD_LOCAL, "session", None)
    if current is None:
        current = requests.Session()
        _THREAD_LOCAL.session = current
    return current


def run_query(endpoint: str, query: str, timeout: float | None = None) -> list[dict[str, str]]:
    response = session().post(
        endpoint,
        data={"query": query},
        headers={"Accept": "application/sparql-results+json"},
        timeout=timeout or QUERY_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()

    rows: list[dict[str, str]] = []
    for binding in payload.get("results", {}).get("bindings", []):
        row: dict[str, str] = {}
        for key, value in binding.items():
            row[key] = value.get("value", "")
        rows.append(row)
    return rows


def get_statement_lines(
    endpoint: str,
    *,
    subject_iri: str,
    predicate_iri: str,
    timeout: float | None = None,
) -> list[str]:
    statements_endpoint = endpoint.rstrip("/") + "/statements"
    response = session().get(
        statements_endpoint,
        params={
            "subj": f"<{subject_iri}>",
            "pred": f"<{predicate_iri}>",
        },
        headers={"Accept": "application/n-triples"},
        timeout=timeout or STATEMENT_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return [line.strip() for line in response.text.splitlines() if line.strip()]
