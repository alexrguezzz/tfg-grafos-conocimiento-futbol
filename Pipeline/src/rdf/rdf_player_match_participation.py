from pathlib import Path
import sys
import pandas as pd

if __name__ == "__main__":
    from rdf_args import parse_no_args
    parse_no_args("Genera RDF para participaciones de jugadores por partido.")

from rdflib import Graph, Literal
from rdflib.namespace import RDF, RDFS, XSD

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(PROJECT_ROOT))

from src.rdf.rdf_args import parse_no_args
from src.pipeline.console_output import print_result
from src.utils.namespaces import (
    EX,
    CLASS,
    PROP,
    RESOURCE,
    PLAYER,
    TEAM_MATCH_PARTICIPATION,
    PLAYER_MATCH_PARTICIPATION,
    HAS_PLAYER_MATCH_PARTICIPATION,
    BELONGS_TO_TEAM_MATCH_PARTICIPATION,
    CORRESPONDS_TO_PLAYER,
    PARTICIPATION_STATUS,
    POSITION,
    IS_CAPTAIN,
    SUB_IN,
    SUB_OUT,
    APPEARANCES,
    FOULS_COMMITTED,
    FOULS_SUFFERED,
    OWN_GOALS,
    RED_CARDS,
    YELLOW_CARDS,
    GOALS_CONCEDED,
    SAVES,
    GOAL_ASSISTS,
    SHOTS_ON_TARGET,
    TOTAL_GOALS,
    TOTAL_SHOTS,
    OFFSIDES,
    MINUTES,
    XG,
    XG_CHAIN,
    XG_BUILDUP,
    XA,
    KEY_PASSES,
    REASON,
    STATUS,
    player_uri,
    team_match_participation_uri,
    player_match_participation_uri,
)


REQUIRED_COLUMNS = ["id_playerMatchParticipation", "id_match", "id_team", "id_player"]

NUMERIC_MAPPINGS = [
    ("subOut", SUB_OUT, XSD.integer, int),
    ("appearances", APPEARANCES, XSD.integer, int),
    ("foulsCommitted", FOULS_COMMITTED, XSD.integer, int),
    ("foulsSuffered", FOULS_SUFFERED, XSD.integer, int),
    ("ownGoals", OWN_GOALS, XSD.integer, int),
    ("redCards", RED_CARDS, XSD.integer, int),
    ("yellowCards", YELLOW_CARDS, XSD.integer, int),
    ("goalsConceded", GOALS_CONCEDED, XSD.integer, int),
    ("saves", SAVES, XSD.integer, int),
    ("goalAssists", GOAL_ASSISTS, XSD.integer, int),
    ("shotsOnTarget", SHOTS_ON_TARGET, XSD.integer, int),
    ("totalGoals", TOTAL_GOALS, XSD.integer, int),
    ("totalShots", TOTAL_SHOTS, XSD.integer, int),
    ("offsides", OFFSIDES, XSD.integer, int),
    ("minutes", MINUTES, XSD.integer, int),
    ("xg", XG, XSD.double, float),
    ("xg_chain", XG_CHAIN, XSD.double, float),
    ("xg_buildup", XG_BUILDUP, XSD.double, float),
    ("xa", XA, XSD.double, float),
    ("keyPasses", KEY_PASSES, XSD.integer, int),
]

STRING_MAPPINGS = [
    ("participationStatus", PARTICIPATION_STATUS),
    ("position", POSITION),
    ("subIn", SUB_IN),
    ("reason", REASON),
    ("status", STATUS),
]


def to_bool_literal(value) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "1", "yes"}:
        return True
    if text in {"false", "0", "no"}:
        return False
    raise ValueError(f"No se pudo convertir a booleano: {value}")


BOOLEAN_MAPPINGS = [
    ("isCaptain", IS_CAPTAIN),
]


def build_input_path() -> Path:
    return PROJECT_ROOT / "data" / "processed" / "canonical" / "player_match_participation.csv"


def build_output_path() -> Path:
    output_dir = PROJECT_ROOT / "data" / "ttl"
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir / "player_match_participation.ttl"


def add_literal_if_present(graph: Graph, node, prop, value, datatype, converter=None) -> None:
    if pd.isna(value):
        return
    if isinstance(value, str) and not value.strip():
        return
    out = converter(value) if converter else value
    graph.add((node, prop, Literal(out, datatype=datatype)))


def build_graph(df: pd.DataFrame) -> Graph:
    if not all(col in df.columns for col in REQUIRED_COLUMNS):
        raise ValueError("Faltan columnas obligatorias")

    graph = Graph()
    for prefix, ns in [("ex", EX), ("class", CLASS), ("prop", PROP), ("resource", RESOURCE), ("rdf", RDF), ("rdfs", RDFS), ("xsd", XSD)]:
        graph.bind(prefix, ns)

    added_players = set()

    for _, row in df.iterrows():
        participation_id = str(row["id_playerMatchParticipation"])
        match_id = str(row["id_match"])
        team_id = str(row["id_team"])
        player_id = str(row["id_player"])

        participation_node = player_match_participation_uri(participation_id)
        player_node = player_uri(player_id)
        team_participation_node = team_match_participation_uri(f"{match_id}_{team_id}")

        if player_id not in added_players:
            graph.add((player_node, RDF.type, PLAYER))
            added_players.add(player_id)

        graph.add((team_participation_node, RDF.type, TEAM_MATCH_PARTICIPATION))
        graph.add((participation_node, RDF.type, PLAYER_MATCH_PARTICIPATION))
        graph.add((participation_node, RDFS.label, Literal(participation_id, datatype=XSD.string)))
        graph.add((team_participation_node, HAS_PLAYER_MATCH_PARTICIPATION, participation_node))
        graph.add((participation_node, BELONGS_TO_TEAM_MATCH_PARTICIPATION, team_participation_node))
        graph.add((participation_node, CORRESPONDS_TO_PLAYER, player_node))

        for column, prop in STRING_MAPPINGS:
            if column in df.columns:
                add_literal_if_present(graph, participation_node, prop, row[column], XSD.string, str)

        for column, prop, datatype, converter in NUMERIC_MAPPINGS:
            if column in df.columns:
                add_literal_if_present(graph, participation_node, prop, row[column], datatype, converter)

        for column, prop in BOOLEAN_MAPPINGS:
            if column in df.columns:
                add_literal_if_present(graph, participation_node, prop, row[column], XSD.boolean, to_bool_literal)

    return graph


def main() -> None:
    parse_no_args("Genera RDF para participaciones de jugadores por partido.")
    input_path = build_input_path()
    output_path = build_output_path()
    if not input_path.exists():
        raise FileNotFoundError(f"No existe: {input_path}")
    print("Serializando participaciones de jugadores a RDF...")
    df = pd.read_csv(input_path)
    graph = build_graph(df)
    graph.serialize(destination=output_path, format="turtle")
    print_result("Tripletas", len(graph), output_path)


if __name__ == "__main__":
    main()
