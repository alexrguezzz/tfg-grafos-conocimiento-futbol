from __future__ import annotations

import pandas as pd
import pytest

pytest.importorskip("rdflib")

from rdflib import Graph

from src.rdf.rdf_events import REQUIRED_COLUMNS, write_graph_streaming


def _event_row(*, player_id: str = "player_1", related_player_id: str = "", related_event_id: str = "") -> dict[str, object]:
    row = {column: "" for column in REQUIRED_COLUMNS}
    row.update(
        {
            "id_event": "event_1",
            "id_match": "match_1",
            "id_team": "team_a",
            "id_player": player_id,
            "period": "FirstHalf",
            "minute": 1,
            "second": 0,
            "expandedMinute": 1,
            "type": "Pass",
            "outcomeType": "Successful",
            "x": 50.0,
            "y": 50.0,
            "endX": 60.0,
            "endY": 60.0,
            "qualifiers": "[]",
            "isTouch": "true",
            "isShot": "false",
            "isGoal": "false",
            "cardType": "",
            "idWhoscored": "1",
            "related_event_id": related_event_id,
            "related_player_id": related_player_id,
        }
    )
    return row


def _write_events_csv(path, row: dict[str, object]) -> None:
    pd.DataFrame([row], columns=REQUIRED_COLUMNS).to_csv(path, index=False, encoding="utf-8")


def test_rdf_events_fails_when_primary_player_participation_is_missing(runtime_dir) -> None:
    input_path = runtime_dir / "events.csv"
    output_path = runtime_dir / "events.ttl"
    _write_events_csv(input_path, _event_row(player_id="player_1"))

    with pytest.raises(ValueError, match="participaciones de jugador inexistentes"):
        write_graph_streaming(
            input_path,
            output_path,
            valid_player_participation_ids={"match_1_other_player"},
            chunksize=10,
        )

    assert not output_path.exists()
    assert not output_path.with_suffix(".ttl.tmp").exists()


def test_rdf_events_writes_parseable_ttl_when_participation_exists(runtime_dir) -> None:
    input_path = runtime_dir / "events.csv"
    output_path = runtime_dir / "events.ttl"
    _write_events_csv(input_path, _event_row(player_id="player_1"))

    triple_count = write_graph_streaming(
        input_path,
        output_path,
        valid_player_participation_ids={"match_1_player_1"},
        chunksize=10,
    )

    assert triple_count > 0
    assert output_path.exists()
    parsed = Graph()
    parsed.parse(output_path, format="turtle")
    assert len(parsed) == triple_count
