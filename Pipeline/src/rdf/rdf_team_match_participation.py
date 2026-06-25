from pathlib import Path
import sys
import pandas as pd

if __name__ == "__main__":
    from rdf_args import parse_no_args
    parse_no_args("Genera RDF para participaciones de equipos por partido.")

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
    TEAM,
    MATCH,
    TEAM_MATCH_PARTICIPATION,
    TEAM_COMPETITION_SEASON,
    HAS_TEAM_MATCH_PARTICIPATION,
    BELONGS_TO_MATCH,
    BELONGS_TO_TEAM_COMPETITION_SEASON,
    CORRESPONDS_TO_TEAM,
    IS_HOME,
    FOULS_COMMITTED,
    YELLOW_CARDS,
    RED_CARDS,
    OFFSIDES,
    WON_CORNERS,
    SAVES,
    POSSESSION_PCT,
    TOTAL_SHOTS,
    SHOTS_ON_TARGET,
    PENALTY_KICK_GOALS,
    PENALTY_KICK_SHOTS,
    ACCURATE_PASSES,
    TOTAL_PASSES,
    ACCURATE_CROSSES,
    TOTAL_CROSSES,
    TOTAL_LONG_BALLS,
    ACCURATE_LONG_BALLS,
    BLOCKED_SHOTS,
    EFFECTIVE_TACKLES,
    TOTAL_TACKLES,
    INTERCEPTIONS,
    TOTAL_CLEARANCE,
    XG,
    NP_XG,
    NP_XG_DIFFERENCE,
    PPDA,
    DEEP_COMPLETIONS,
    match_uri,
    team_uri,
    team_match_participation_uri,
    team_competition_season_uri,
)


REQUIRED_COLUMNS = ["id_teamMatchParticipation", "id_match", "id_team", "isHome"]

NUMERIC_MAPPINGS = [
    ("foulsCommitted", FOULS_COMMITTED, XSD.integer, int),
    ("yellowCards", YELLOW_CARDS, XSD.integer, int),
    ("redCards", RED_CARDS, XSD.integer, int),
    ("offsides", OFFSIDES, XSD.integer, int),
    ("wonCorners", WON_CORNERS, XSD.integer, int),
    ("saves", SAVES, XSD.integer, int),
    ("possessionPct", POSSESSION_PCT, XSD.double, float),
    ("totalShots", TOTAL_SHOTS, XSD.integer, int),
    ("shotsOnTarget", SHOTS_ON_TARGET, XSD.integer, int),
    ("penaltyKickGoals", PENALTY_KICK_GOALS, XSD.integer, int),
    ("penaltyKickShots", PENALTY_KICK_SHOTS, XSD.integer, int),
    ("accuratePasses", ACCURATE_PASSES, XSD.integer, int),
    ("totalPasses", TOTAL_PASSES, XSD.integer, int),
    ("accurateCrosses", ACCURATE_CROSSES, XSD.integer, int),
    ("totalCrosses", TOTAL_CROSSES, XSD.integer, int),
    ("totalLongBalls", TOTAL_LONG_BALLS, XSD.integer, int),
    ("accurateLongBalls", ACCURATE_LONG_BALLS, XSD.integer, int),
    ("blockedShots", BLOCKED_SHOTS, XSD.integer, int),
    ("effectiveTackles", EFFECTIVE_TACKLES, XSD.integer, int),
    ("totalTackles", TOTAL_TACKLES, XSD.integer, int),
    ("interceptions", INTERCEPTIONS, XSD.integer, int),
    ("totalClearance", TOTAL_CLEARANCE, XSD.integer, int),
    ("xg", XG, XSD.double, float),
    ("nonPenaltyXg", NP_XG, XSD.double, float),
    ("nonPenaltyXgDifference", NP_XG_DIFFERENCE, XSD.double, float),
    ("ppda", PPDA, XSD.double, float),
    ("deepCompletions", DEEP_COMPLETIONS, XSD.integer, int),
]


def build_input_path() -> Path:
    return PROJECT_ROOT / "data" / "processed" / "canonical" / "team_match_participation.csv"


def build_matches_path() -> Path:
    return PROJECT_ROOT / "data" / "processed" / "canonical" / "matches.csv"


def build_output_path() -> Path:
    output_dir = PROJECT_ROOT / "data" / "ttl"
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir / "team_match_participation.ttl"


def add_literal_if_present(graph: Graph, node, prop, value, datatype, converter=None) -> None:
    if pd.notna(value):
        out = converter(value) if converter else value
        graph.add((node, prop, Literal(out, datatype=datatype)))


def build_graph(df_participation: pd.DataFrame) -> Graph:
    if not all(col in df_participation.columns for col in REQUIRED_COLUMNS):
        raise ValueError("Faltan columnas obligatorias")

    graph = Graph()
    for prefix, ns in [("ex", EX), ("class", CLASS), ("prop", PROP), ("resource", RESOURCE), ("rdf", RDF), ("rdfs", RDFS), ("xsd", XSD)]:
        graph.bind(prefix, ns)

    added_teams = set()

    for _, row in df_participation.iterrows():
        participation_id = str(row["id_teamMatchParticipation"])
        match_id = str(row["id_match"])
        team_id = str(row["id_team"])
        is_home = str(row["isHome"]).lower() in ["true", "1", "yes"]

        participation_node = team_match_participation_uri(participation_id)
        match_node = match_uri(match_id)
        team_node = team_uri(team_id)

        if team_id not in added_teams:
            graph.add((team_node, RDF.type, TEAM))
            added_teams.add(team_id)

        graph.add((match_node, RDF.type, MATCH))
        graph.add((participation_node, RDF.type, TEAM_MATCH_PARTICIPATION))
        graph.add((participation_node, RDFS.label, Literal(participation_id, datatype=XSD.string)))
        graph.add((match_node, HAS_TEAM_MATCH_PARTICIPATION, participation_node))
        graph.add((participation_node, BELONGS_TO_MATCH, match_node))
        graph.add((participation_node, CORRESPONDS_TO_TEAM, team_node))
        graph.add((participation_node, IS_HOME, Literal(is_home, datatype=XSD.boolean)))

        if {"id_competition", "id_season"}.issubset(df_participation.columns):
            competition_id = row.get("id_competition")
            season_id = row.get("id_season")
            if pd.notna(competition_id) and pd.notna(season_id):
                tcs_id = f"{competition_id}_{season_id}_{team_id}"
                tcs_node = team_competition_season_uri(tcs_id)
                graph.add((tcs_node, RDF.type, TEAM_COMPETITION_SEASON))
                graph.add((tcs_node, HAS_TEAM_MATCH_PARTICIPATION, participation_node))
                graph.add((participation_node, BELONGS_TO_TEAM_COMPETITION_SEASON, tcs_node))

        for column, prop, datatype, converter in NUMERIC_MAPPINGS:
            if column in df_participation.columns:
                add_literal_if_present(graph, participation_node, prop, row[column], datatype, converter)

    return graph


def main() -> None:
    parse_no_args("Genera RDF para participaciones de equipos por partido.")
    input_path = build_input_path()
    output_path = build_output_path()
    if not input_path.exists():
        raise FileNotFoundError(f"No existe: {input_path}")
    print("Serializando participaciones de equipos a RDF...")
    df_participation = pd.read_csv(input_path)
    matches_path = build_matches_path()
    if matches_path.exists():
        df_matches = pd.read_csv(matches_path, usecols=["id_match", "id_competition", "id_season"])
        df_participation = df_participation.merge(df_matches, on="id_match", how="left")
    graph = build_graph(df_participation)
    graph.serialize(destination=output_path, format="turtle")
    print_result("Tripletas", len(graph), output_path)


if __name__ == "__main__":
    main()
