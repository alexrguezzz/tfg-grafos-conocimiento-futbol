from pathlib import Path
import sys
import pandas as pd

if __name__ == "__main__":
    from rdf_args import parse_events_args
    parse_events_args()

from rdflib import Graph, Literal
from rdflib.namespace import RDF, RDFS, XSD

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(PROJECT_ROOT))

from src.rdf.rdf_args import parse_events_args
from src.pipeline.console_output import print_error, print_examples, print_result
from src.utils.namespaces import (
    EX,
    CLASS,
    PROP,
    RESOURCE,
    EVENT,
    TEAM_MATCH_PARTICIPATION,
    PLAYER_MATCH_PARTICIPATION,
    HAS_EVENT,
    BELONGS_TO_MATCH,
    INVOLVES_TEAM_MATCH_PARTICIPATION,
    INVOLVES_PLAYER_MATCH_PARTICIPATION,
    INVOLVES_SECONDARY_PLAYER_MATCH_PARTICIPATION,
    EVENT_PERIOD,
    EVENT_MINUTE,
    EVENT_SECOND,
    EVENT_EXPANDED_MINUTE,
    EVENT_TYPE,
    OUTCOME_TYPE,
    X_COORD,
    Y_COORD,
    END_X,
    END_Y,
    GOAL_MOUTH_Y,
    GOAL_MOUTH_Z,
    BLOCKED_X,
    BLOCKED_Y,
    QUALIFIERS,
    IS_TOUCH,
    IS_SHOT,
    IS_GOAL,
    CARD_TYPE,
    ID_WHOSCORED,
    IS_RELATED_TO_EVENT,
    event_uri,
    match_uri,
    team_match_participation_uri,
    player_match_participation_uri,
)


REQUIRED_COLUMNS = [
    "id_event",
    "id_match",
    "id_team",
    "id_player",
    "period",
    "minute",
    "second",
    "expandedMinute",
    "type",
    "outcomeType",
    "x",
    "y",
    "endX",
    "endY",
    "goalMouthY",
    "goalMouthZ",
    "blockedX",
    "blockedY",
    "qualifiers",
    "isTouch",
    "isShot",
    "isGoal",
    "cardType",
    "idWhoscored",
    "related_event_id",
    "related_player_id",
]


BOOLEAN_COLUMNS = [
    "isTouch",
    "isShot",
    "isGoal",
]


STRING_COLUMNS = [
    "id_event",
    "id_match",
    "id_team",
    "id_player",
    "period",
    "type",
    "outcomeType",
    "qualifiers",
    "cardType",
    "idWhoscored",
    "related_event_id",
    "related_player_id",
]


def build_input_path() -> Path:
    return PROJECT_ROOT / "data" / "processed" / "canonical" / "events.csv"


def build_player_match_participation_path() -> Path:
    return PROJECT_ROOT / "data" / "processed" / "canonical" / "player_match_participation.csv"


def build_output_path() -> Path:
    output_dir = PROJECT_ROOT / "data" / "ttl"
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir / "events.ttl"


def add_literal_if_present(graph: Graph, node, prop, value, datatype, converter=None) -> None:
    if pd.isna(value):
        return
    if isinstance(value, str) and not value.strip():
        return
    out = converter(value) if converter else value
    graph.add((node, prop, Literal(out, datatype=datatype)))


def to_bool_literal(value) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "1", "yes"}:
        return True
    if text in {"false", "0", "no"}:
        return False
    raise ValueError(f"No se pudo convertir a booleano: {value}")


LITERAL_SPECS = [
    ("period", EVENT_PERIOD, XSD.string, str),
    ("minute", EVENT_MINUTE, XSD.integer, int),
    ("second", EVENT_SECOND, XSD.float, float),
    ("expandedMinute", EVENT_EXPANDED_MINUTE, XSD.integer, int),
    ("type", EVENT_TYPE, XSD.string, str),
    ("outcomeType", OUTCOME_TYPE, XSD.string, str),
    ("x", X_COORD, XSD.float, float),
    ("y", Y_COORD, XSD.float, float),
    ("endX", END_X, XSD.float, float),
    ("endY", END_Y, XSD.float, float),
    ("goalMouthY", GOAL_MOUTH_Y, XSD.float, float),
    ("goalMouthZ", GOAL_MOUTH_Z, XSD.float, float),
    ("blockedX", BLOCKED_X, XSD.float, float),
    ("blockedY", BLOCKED_Y, XSD.float, float),
    ("qualifiers", QUALIFIERS, XSD.string, str),
    ("isTouch", IS_TOUCH, XSD.boolean, to_bool_literal),
    ("isShot", IS_SHOT, XSD.boolean, to_bool_literal),
    ("isGoal", IS_GOAL, XSD.boolean, to_bool_literal),
    ("cardType", CARD_TYPE, XSD.string, str),
    ("idWhoscored", ID_WHOSCORED, XSD.string, str),
]


def _is_present(value) -> bool:
    if pd.isna(value):
        return False
    if isinstance(value, str) and not value.strip():
        return False
    return True


def _resource_n3(node) -> str:
    return f"<{str(node)}>"


def _literal_n3(value, datatype, converter=None) -> str:
    out = converter(value) if converter else value
    return Literal(out, datatype=datatype).n3()


def _write_triple(output, subject, predicate, obj) -> int:
    output.write(f"{_resource_n3(subject)} {_resource_n3(predicate)} {_resource_n3(obj)} .\n")
    return 1


def _write_subject_block(output, subject, predicate_objects: list[tuple[object, str]]) -> int:
    if not predicate_objects:
        return 0

    output.write(f"{_resource_n3(subject)}\n")
    last_index = len(predicate_objects) - 1
    for index, (predicate, obj_n3) in enumerate(predicate_objects):
        terminator = " ." if index == last_index else " ;"
        output.write(f"    {_resource_n3(predicate)} {obj_n3}{terminator}\n")
    output.write("\n")
    return len(predicate_objects)


def _append_literal_if_present(predicate_objects: list[tuple[object, str]], predicate, value, datatype, converter=None) -> None:
    if not _is_present(value):
        return
    predicate_objects.append((predicate, _literal_n3(value, datatype, converter)))


def _print_missing_participations(
    missing_primary_participations: set[str],
    missing_secondary_participations: set[str],
) -> None:
    if missing_primary_participations:
        print_error(
            f"{len(missing_primary_participations)} participaciones primarias de eventos no existen "
            "en player_match_participation.csv."
        )
        print_examples(sorted(missing_primary_participations)[:20])
    if missing_secondary_participations:
        print_error(
            f"{len(missing_secondary_participations)} participaciones secundarias de eventos no existen "
            "en player_match_participation.csv."
        )
        print_examples(sorted(missing_secondary_participations)[:20])


def write_graph_streaming(
    input_path: Path,
    output_path: Path,
    valid_player_participation_ids: set[str] | None = None,
    chunksize: int = 50_000,
) -> int:
    header = pd.read_csv(input_path, nrows=0)
    missing_columns = [col for col in REQUIRED_COLUMNS if col not in header.columns]
    if missing_columns:
        raise ValueError(f"Faltan columnas obligatorias: {', '.join(missing_columns)}")

    tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    if tmp_path.exists():
        tmp_path.unlink()

    missing_primary_participations: set[str] = set()
    missing_secondary_participations: set[str] = set()
    emitted_team_participation_types: set[str] = set()
    emitted_player_participation_types: set[str] = set()
    triple_count = 0
    processed_rows = 0

    dtype = {
        **{column: "boolean" for column in BOOLEAN_COLUMNS},
        **{column: "string" for column in STRING_COLUMNS},
    }

    try:
        with tmp_path.open("w", encoding="utf-8", newline="\n") as output:
            for df_events in pd.read_csv(input_path, dtype=dtype, chunksize=chunksize):
                for row in df_events.itertuples(index=False):
                    event_id = str(row.id_event)
                    match_id = str(row.id_match)
                    team_id = str(row.id_team)
                    player_id = row.id_player
                    related_event_id = row.related_event_id

                    event_node = event_uri(event_id)
                    match_node = match_uri(match_id)
                    team_participation_id = f"{match_id}_{team_id}"
                    team_participation_node = team_match_participation_uri(team_participation_id)

                    if team_participation_id not in emitted_team_participation_types:
                        triple_count += _write_triple(
                            output,
                            team_participation_node,
                            RDF.type,
                            TEAM_MATCH_PARTICIPATION,
                        )
                        emitted_team_participation_types.add(team_participation_id)

                    triple_count += _write_triple(output, match_node, HAS_EVENT, event_node)

                    predicate_objects: list[tuple[object, str]] = [
                        (RDF.type, _resource_n3(EVENT)),
                        (RDFS.label, _literal_n3(event_id, XSD.string)),
                        (BELONGS_TO_MATCH, _resource_n3(match_node)),
                        (INVOLVES_TEAM_MATCH_PARTICIPATION, _resource_n3(team_participation_node)),
                    ]

                    if _is_present(player_id):
                        player_id_text = str(player_id)
                        player_participation_id = f"{match_id}_{player_id_text}"
                        player_participation_node = player_match_participation_uri(player_participation_id)
                        if valid_player_participation_ids is None or player_participation_id in valid_player_participation_ids:
                            if player_participation_id not in emitted_player_participation_types:
                                triple_count += _write_triple(
                                    output,
                                    player_participation_node,
                                    RDF.type,
                                    PLAYER_MATCH_PARTICIPATION,
                                )
                                emitted_player_participation_types.add(player_participation_id)
                            predicate_objects.append((
                                INVOLVES_PLAYER_MATCH_PARTICIPATION,
                                _resource_n3(player_participation_node),
                            ))
                        else:
                            missing_primary_participations.add(player_participation_id)

                    related_player_id = row.related_player_id
                    if _is_present(related_player_id):
                        related_player_id_text = str(related_player_id)
                        related_player_participation_id = f"{match_id}_{related_player_id_text}"
                        if (
                            valid_player_participation_ids is None
                            or related_player_participation_id in valid_player_participation_ids
                        ):
                            predicate_objects.append((
                                INVOLVES_SECONDARY_PLAYER_MATCH_PARTICIPATION,
                                _resource_n3(player_match_participation_uri(related_player_participation_id)),
                            ))
                        else:
                            missing_secondary_participations.add(related_player_participation_id)

                    for column, predicate, datatype, converter in LITERAL_SPECS:
                        _append_literal_if_present(
                            predicate_objects,
                            predicate,
                            getattr(row, column),
                            datatype,
                            converter,
                        )

                    if _is_present(related_event_id):
                        related_event_id_text = str(related_event_id).strip()
                        predicate_objects.append((IS_RELATED_TO_EVENT, _resource_n3(event_uri(related_event_id_text))))

                    triple_count += _write_subject_block(output, event_node, predicate_objects)
                    processed_rows += 1

        if missing_primary_participations or missing_secondary_participations:
            _print_missing_participations(missing_primary_participations, missing_secondary_participations)
            raise ValueError("Existen referencias de eventos a participaciones de jugador inexistentes.")

        tmp_path.replace(output_path)
        return triple_count
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink()
        raise


def build_graph(df_events: pd.DataFrame, valid_player_participation_ids: set[str] | None = None) -> Graph:
    if not all(col in df_events.columns for col in REQUIRED_COLUMNS):
        raise ValueError("Faltan columnas obligatorias")

    graph = Graph()
    missing_primary_participations: set[str] = set()
    missing_secondary_participations: set[str] = set()
    for prefix, ns in [
        ("ex", EX),
        ("class", CLASS),
        ("prop", PROP),
        ("resource", RESOURCE),
        ("rdf", RDF),
        ("rdfs", RDFS),
        ("xsd", XSD),
    ]:
        graph.bind(prefix, ns)

    for _, row in df_events.iterrows():
        event_id = str(row["id_event"])
        match_id = str(row["id_match"])
        team_id = str(row["id_team"])
        player_id = row.get("id_player")
        related_event_id = row.get("related_event_id")

        event_node = event_uri(event_id)
        match_node = match_uri(match_id)
        team_participation_node = team_match_participation_uri(f"{match_id}_{team_id}")

        graph.add((event_node, RDF.type, EVENT))
        graph.add((team_participation_node, RDF.type, TEAM_MATCH_PARTICIPATION))
        graph.add((event_node, RDFS.label, Literal(event_id, datatype=XSD.string)))
        graph.add((match_node, HAS_EVENT, event_node))
        graph.add((event_node, BELONGS_TO_MATCH, match_node))
        graph.add((event_node, INVOLVES_TEAM_MATCH_PARTICIPATION, team_participation_node))

        if pd.notna(player_id) and str(player_id).strip() != "":
            player_id_text = str(player_id)
            player_participation_id = f"{match_id}_{player_id_text}"
            player_participation_node = player_match_participation_uri(player_participation_id)
            if valid_player_participation_ids is None or player_participation_id in valid_player_participation_ids:
                graph.add((player_participation_node, RDF.type, PLAYER_MATCH_PARTICIPATION))
                graph.add((event_node, INVOLVES_PLAYER_MATCH_PARTICIPATION, player_participation_node))
            else:
                missing_primary_participations.add(player_participation_id)

        related_player_id = row.get("related_player_id")
        if pd.notna(related_player_id) and str(related_player_id).strip() != "":
            related_player_id_text = str(related_player_id)
            related_player_participation_id = f"{match_id}_{related_player_id_text}"
            if valid_player_participation_ids is None or related_player_participation_id in valid_player_participation_ids:
                graph.add((
                    event_node,
                    INVOLVES_SECONDARY_PLAYER_MATCH_PARTICIPATION,
                    player_match_participation_uri(related_player_participation_id),
                ))
            else:
                missing_secondary_participations.add(related_player_participation_id)

        add_literal_if_present(graph, event_node, EVENT_PERIOD, row["period"], XSD.string, str)
        add_literal_if_present(graph, event_node, EVENT_MINUTE, row["minute"], XSD.integer, int)
        add_literal_if_present(graph, event_node, EVENT_SECOND, row["second"], XSD.float, float)
        add_literal_if_present(graph, event_node, EVENT_EXPANDED_MINUTE, row["expandedMinute"], XSD.integer, int)
        add_literal_if_present(graph, event_node, EVENT_TYPE, row["type"], XSD.string, str)
        add_literal_if_present(graph, event_node, OUTCOME_TYPE, row["outcomeType"], XSD.string, str)
        add_literal_if_present(graph, event_node, X_COORD, row["x"], XSD.float, float)
        add_literal_if_present(graph, event_node, Y_COORD, row["y"], XSD.float, float)
        add_literal_if_present(graph, event_node, END_X, row["endX"], XSD.float, float)
        add_literal_if_present(graph, event_node, END_Y, row["endY"], XSD.float, float)
        add_literal_if_present(graph, event_node, GOAL_MOUTH_Y, row["goalMouthY"], XSD.float, float)
        add_literal_if_present(graph, event_node, GOAL_MOUTH_Z, row["goalMouthZ"], XSD.float, float)
        add_literal_if_present(graph, event_node, BLOCKED_X, row["blockedX"], XSD.float, float)
        add_literal_if_present(graph, event_node, BLOCKED_Y, row["blockedY"], XSD.float, float)
        add_literal_if_present(graph, event_node, QUALIFIERS, row["qualifiers"], XSD.string, str)
        add_literal_if_present(graph, event_node, IS_TOUCH, row["isTouch"], XSD.boolean, to_bool_literal)
        add_literal_if_present(graph, event_node, IS_SHOT, row["isShot"], XSD.boolean, to_bool_literal)
        add_literal_if_present(graph, event_node, IS_GOAL, row["isGoal"], XSD.boolean, to_bool_literal)
        add_literal_if_present(graph, event_node, CARD_TYPE, row["cardType"], XSD.string, str)
        add_literal_if_present(graph, event_node, ID_WHOSCORED, row["idWhoscored"], XSD.string, str)
        if pd.notna(related_event_id):
            related_event_id_text = str(related_event_id).strip()
            if related_event_id_text:
                graph.add((event_node, IS_RELATED_TO_EVENT, event_uri(related_event_id_text)))

    if missing_primary_participations or missing_secondary_participations:
        if missing_primary_participations:
            print_error(
                f"{len(missing_primary_participations)} participaciones primarias de eventos no existen "
                "en player_match_participation.csv."
            )
            print_examples(sorted(missing_primary_participations)[:20])
        if missing_secondary_participations:
            print_error(
                f"{len(missing_secondary_participations)} participaciones secundarias de eventos no existen "
                "en player_match_participation.csv."
            )
            print_examples(sorted(missing_secondary_participations)[:20])
        raise ValueError("Existen referencias de eventos a participaciones de jugador inexistentes.")

    return graph


def main() -> None:
    args = parse_events_args()
    input_path = build_input_path()
    output_path = build_output_path()

    if not input_path.exists():
        raise FileNotFoundError(f"No existe: {input_path}")

    print("Serializando eventos de partido a RDF...")
    participation_path = build_player_match_participation_path()
    valid_player_participation_ids = None
    if participation_path.exists():
        df_participation = pd.read_csv(participation_path, usecols=["id_playerMatchParticipation"])
        valid_player_participation_ids = set(df_participation["id_playerMatchParticipation"].dropna().astype(str))
    chunksize = args.events_rdf_chunk_size
    triple_count = write_graph_streaming(input_path, output_path, valid_player_participation_ids, chunksize=chunksize)
    print_result("Tripletas", triple_count, output_path)


if __name__ == "__main__":
    main()
