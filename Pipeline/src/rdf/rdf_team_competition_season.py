from pathlib import Path
import sys
import pandas as pd

if __name__ == "__main__":
    from rdf_args import parse_no_args
    parse_no_args("Genera RDF para equipos por competicion-temporada.")

from rdflib import Graph, Literal
from rdflib.namespace import RDF, RDFS, XSD

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(PROJECT_ROOT))

from src.rdf.rdf_args import parse_no_args
from src.pipeline.console_output import print_result
from src.utils.namespaces import (
    EX, CLASS, PROP, RESOURCE, TEAM, COMPETITION, SEASON, TEAM_COMPETITION_SEASON,
    CORRESPONDS_TO_TEAM, HAS_TEAM_COMPETITION_SEASON, BELONGS_TO_COMPETITION, BELONGS_TO_SEASON,
    POSITION, MP, W, D, L, GF, GA, GD, PTS, team_uri, competition_uri, season_uri, team_competition_season_uri,
)


REQUIRED_COLUMNS = ["id_teamCompetitionSeason", "id_team", "id_competition",
                    "id_season", "position", "matchesPlayed", "wins", "draws", "losses", "goalsFor", "goalsAgainst", "goalDifference", "points"]


def build_input_path() -> Path:
    return PROJECT_ROOT / "data" / "processed" / "canonical" / "team_competition_season.csv"


def build_output_path() -> Path:
    output_dir = PROJECT_ROOT / "data" / "ttl"
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir / "team_competition_season.ttl"


def add_literal_if_present(graph, node, prop, value, datatype, converter=None):
    if pd.notna(value):
        val = converter(value) if converter else value
        graph.add((node, prop, Literal(val, datatype=datatype)))


def build_graph(df_tcs: pd.DataFrame) -> Graph:
    if not all(col in df_tcs.columns for col in REQUIRED_COLUMNS):
        raise ValueError("Faltan columnas obligatorias")

    graph = Graph()
    for prefix, ns in [("ex", EX), ("class", CLASS), ("prop", PROP), ("resource", RESOURCE),
                       ("rdf", RDF), ("rdfs", RDFS), ("xsd", XSD)]:
        graph.bind(prefix, ns)

    added_teams, added_competitions, added_seasons = set(), set(), set()

    for _, row in df_tcs.iterrows():
        tcs_id, team_id = str(row["id_teamCompetitionSeason"]), str(row["id_team"])
        competition_id, season_id = str(row["id_competition"]), str(row["id_season"])
        
        tcs_node = team_competition_season_uri(tcs_id)
        team_node, competition_node, season_node = team_uri(team_id), competition_uri(competition_id), season_uri(season_id)

        if team_id not in added_teams:
            graph.add((team_node, RDF.type, TEAM))
            added_teams.add(team_id)

        if competition_id not in added_competitions:
            graph.add((competition_node, RDF.type, COMPETITION))
            added_competitions.add(competition_id)

        if season_id not in added_seasons:
            graph.add((season_node, RDF.type, SEASON))
            added_seasons.add(season_id)

        graph.add((tcs_node, RDF.type, TEAM_COMPETITION_SEASON))
        graph.add((tcs_node, RDFS.label, Literal(tcs_id, datatype=XSD.string)))
        graph.add((season_node, HAS_TEAM_COMPETITION_SEASON, tcs_node))
        graph.add((tcs_node, CORRESPONDS_TO_TEAM, team_node))
        graph.add((tcs_node, BELONGS_TO_COMPETITION, competition_node))
        graph.add((tcs_node, BELONGS_TO_SEASON, season_node))

        for col, prop in [("position", POSITION), ("matchesPlayed", MP), ("wins", W), ("draws", D), ("losses", L),
                          ("goalsFor", GF), ("goalsAgainst", GA), ("goalDifference", GD), ("points", PTS)]:
            add_literal_if_present(graph, tcs_node, prop, row[col], XSD.integer, int)

    return graph


def main() -> None:
    parse_no_args("Genera RDF para equipos por competicion-temporada.")
    input_path = build_input_path()
    output_path = build_output_path()
    if not input_path.exists():
        raise FileNotFoundError(f"No existe: {input_path}")
    print("Serializando vinculos equipo-competicion-temporada a RDF...")
    df_tcs = pd.read_csv(input_path)
    graph = build_graph(df_tcs)
    graph.serialize(destination=output_path, format="turtle")
    print_result("Tripletas", len(graph), output_path)


if __name__ == "__main__":
    main()
