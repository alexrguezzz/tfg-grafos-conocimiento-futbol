from pathlib import Path
import sys
import pandas as pd

if __name__ == "__main__":
    from rdf_args import parse_no_args
    parse_no_args("Genera RDF para estadisticas de jugador por competicion-temporada.")

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
    TEAM_COMPETITION_SEASON,
    PLAYER_COMPETITION_SEASON_STATS,
    HAS_PLAYER_COMPETITION_SEASON_STATS,
    BELONGS_TO_TEAM_COMPETITION_SEASON,
    CORRESPONDS_TO_PLAYER,
    PLAYER_MATCHES,
    MINUTES,
    PLAYER_GOALS,
    XG,
    NP_GOALS,
    NP_XG,
    PLAYER_ASSISTS,
    XA,
    PLAYER_SHOTS,
    KEY_PASSES,
    YELLOW_CARDS,
    RED_CARDS,
    SEASON_XG_CHAIN,
    SEASON_XG_BUILDUP,
    player_uri,
    team_competition_season_uri,
    player_competition_season_stats_uri,
)


REQUIRED_COLUMNS = [
    "id_playerCompetitionSeasonStats",
    "id_player",
    "id_team",
    "id_competition",
    "id_season",
]

NUMERIC_MAPPINGS = [
    ("matches", PLAYER_MATCHES, XSD.integer, int),
    ("minutes", MINUTES, XSD.integer, int),
    ("goals", PLAYER_GOALS, XSD.integer, int),
    ("xg", XG, XSD.double, float),
    ("nonPenaltyGoals", NP_GOALS, XSD.integer, int),
    ("nonPenaltyXg", NP_XG, XSD.double, float),
    ("assists", PLAYER_ASSISTS, XSD.integer, int),
    ("xa", XA, XSD.double, float),
    ("shots", PLAYER_SHOTS, XSD.integer, int),
    ("keyPasses", KEY_PASSES, XSD.integer, int),
    ("yellowCards", YELLOW_CARDS, XSD.integer, int),
    ("redCards", RED_CARDS, XSD.integer, int),
    ("xgChain", SEASON_XG_CHAIN, XSD.double, float),
    ("xgBuildup", SEASON_XG_BUILDUP, XSD.double, float),
]


def build_input_path() -> Path:
    return PROJECT_ROOT / "data" / "processed" / "canonical" / "player_competition_season_stats.csv"


def build_output_path() -> Path:
    output_dir = PROJECT_ROOT / "data" / "ttl"
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir / "player_competition_season_stats.ttl"


def add_literal_if_present(graph: Graph, node, prop, value, datatype, converter=None) -> None:
    if pd.notna(value):
        out = converter(value) if converter else value
        graph.add((node, prop, Literal(out, datatype=datatype)))


def build_graph(df: pd.DataFrame) -> Graph:
    if not all(col in df.columns for col in REQUIRED_COLUMNS):
        raise ValueError("Faltan columnas obligatorias")

    graph = Graph()
    for prefix, ns in [("ex", EX), ("class", CLASS), ("prop", PROP), ("resource", RESOURCE), ("rdf", RDF), ("rdfs", RDFS), ("xsd", XSD)]:
        graph.bind(prefix, ns)

    for _, row in df.iterrows():
        stats_id = str(row["id_playerCompetitionSeasonStats"])
        player_id = str(row["id_player"])
        team_id = str(row["id_team"])
        competition_id = str(row["id_competition"])
        season_id = str(row["id_season"])

        stats_node = player_competition_season_stats_uri(stats_id)
        player_node = player_uri(player_id)
        tcs_node = team_competition_season_uri(f"{competition_id}_{season_id}_{team_id}")

        graph.add((player_node, RDF.type, PLAYER))
        graph.add((tcs_node, RDF.type, TEAM_COMPETITION_SEASON))

        graph.add((stats_node, RDF.type, PLAYER_COMPETITION_SEASON_STATS))
        graph.add((stats_node, RDFS.label, Literal(stats_id, datatype=XSD.string)))
        graph.add((tcs_node, HAS_PLAYER_COMPETITION_SEASON_STATS, stats_node))
        graph.add((stats_node, BELONGS_TO_TEAM_COMPETITION_SEASON, tcs_node))
        graph.add((stats_node, CORRESPONDS_TO_PLAYER, player_node))

        for column, prop, datatype, converter in NUMERIC_MAPPINGS:
            if column in df.columns:
                add_literal_if_present(graph, stats_node, prop, row[column], datatype, converter)

    return graph


def main() -> None:
    parse_no_args("Genera RDF para estadisticas de jugador por competicion-temporada.")
    input_path = build_input_path()
    output_path = build_output_path()
    if not input_path.exists():
        raise FileNotFoundError(f"No existe: {input_path}")
    print("Serializando estadisticas de jugador por temporada a RDF...")
    df = pd.read_csv(input_path)
    graph = build_graph(df)
    graph.serialize(destination=output_path, format="turtle")
    print_result("Tripletas", len(graph), output_path)


if __name__ == "__main__":
    main()
