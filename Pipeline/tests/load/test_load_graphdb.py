from __future__ import annotations

from pathlib import Path

import pytest

from src.load import load_graphdb


class FakeResponse:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


def test_build_statements_url_uses_repository(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(load_graphdb, "GRAPHDB_BASE_URL", "http://graphdb.example")
    monkeypatch.setattr(load_graphdb, "REPOSITORY_ID", "repo")

    assert load_graphdb.build_statements_url() == "http://graphdb.example/repositories/repo/statements"


def test_upload_ttl_file_posts_turtle_stream(monkeypatch: pytest.MonkeyPatch, runtime_dir: Path) -> None:
    ttl_path = runtime_dir / "full_knowledge_graph.ttl"
    ttl_path.write_bytes(b"@prefix ex: <http://example.com/> .\n")
    calls = []

    def fake_post(url, params, headers, data, auth, timeout):
        calls.append(
            {
                "url": url,
                "params": params,
                "headers": headers,
                "body": data.read(),
                "auth": auth,
                "timeout": timeout,
            }
        )
        return FakeResponse(204)

    monkeypatch.setattr(load_graphdb.requests, "post", fake_post)
    monkeypatch.setattr(load_graphdb, "build_statements_url", lambda: "http://graphdb/statements")
    monkeypatch.setattr(load_graphdb, "build_auth", lambda: ("user", "pass"))
    monkeypatch.setattr(load_graphdb, "CONTEXT_GRAPH_URI", None)

    load_graphdb.upload_ttl_file(ttl_path, connect_timeout=1.0, read_timeout=None)

    assert calls == [
        {
            "url": "http://graphdb/statements",
            "params": {},
            "headers": {"Content-Type": "text/turtle"},
            "body": b"@prefix ex: <http://example.com/> .\n",
            "auth": ("user", "pass"),
            "timeout": (1.0, None),
        }
    ]


def test_upload_ttl_file_targets_named_graph(monkeypatch: pytest.MonkeyPatch, runtime_dir: Path) -> None:
    ttl_path = runtime_dir / "full_knowledge_graph.ttl"
    ttl_path.write_text("@prefix ex: <http://example.com/> .\n", encoding="utf-8")
    calls = []

    def fake_post(url, params, headers, data, auth, timeout):
        calls.append(params)
        return FakeResponse(200)

    monkeypatch.setattr(load_graphdb.requests, "post", fake_post)
    monkeypatch.setattr(load_graphdb, "build_statements_url", lambda: "http://graphdb/statements")
    monkeypatch.setattr(load_graphdb, "CONTEXT_GRAPH_URI", "http://example.com/graph")

    load_graphdb.upload_ttl_file(ttl_path, connect_timeout=1.0, read_timeout=2.0)

    assert calls == [{"context": "<http://example.com/graph>"}]


def test_clear_existing_data_raises_on_graphdb_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_delete(url, params, auth, timeout):
        return FakeResponse(500, "failure")

    monkeypatch.setattr(load_graphdb.requests, "delete", fake_delete)
    monkeypatch.setattr(load_graphdb, "build_statements_url", lambda: "http://graphdb/statements")
    monkeypatch.setattr(load_graphdb, "CONTEXT_GRAPH_URI", None)

    with pytest.raises(RuntimeError, match="Error al limpiar datos"):
        load_graphdb.clear_existing_data()
