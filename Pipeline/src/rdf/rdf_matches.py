from pathlib import Path
import sys
import pandas as pd

if __name__ == "__main__":
    from rdf_args import parse_no_args
    parse_no_args("Genera RDF para partidos canonicos.")

from rdflib import Graph, Literal
from rdflib.namespace import RDF, RDFS, XSD

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(PROJECT_ROOT))

from src.rdf.rdf_args import parse_no_args
from src.pipeline.console_output import print_result
from src.utils.namespaces import (
    EX, CLASS, PROP, RESOURCE, COMPETITION, SEASON, MATCH,
    HAS_MATCH, BELONGS_TO_COMPETITION, BELONGS_TO_SEASON, MATCH_NAME, WEEK, MATCH_DATE, MATCH_DATETIME,
    MATCH_STATUS, HOME_SCORE, AWAY_SCORE, FTR, HTHG, HTAG, HTR, ATTENDANCE,
    ID_SOFASCORE, ID_UNDERSTAT, ID_WHOSCORED, PLAYED_AT_STADIUM, STADIUM,
    competition_uri, season_uri, match_uri, stadium_uri,
)


REQUIRED_COLUMNS = ["id_match", "id_competition", "id_season", "name",
                    "matchDay", "date", "dateTime", "matchStatus", "homeScore", "awayScore", "finalResult",
                    "halftimeHomeScore", "halftimeAwayScore", "halftimeResult",
                    "venue", "id_stadium", "attendance", "idSofascore", "idUnderstat", "idWhoscored"]


def build_input_path() -> Path:
    return PROJECT_ROOT / "data" / "processed" / "canonical" / "matches.csv"


def build_output_path() -> Path:
    output_dir = PROJECT_ROOT / "data" / "ttl"
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir / "matches.ttl"




def add_literal_if_present(graph, node, prop, row_value, datatype, converter=None):
    if pd.isna(row_value):
        return
    if isinstance(row_value, str) and not row_value.strip():
        return
    val = converter(row_value) if converter else row_value
    graph.add((node, prop, Literal(val, datatype=datatype)))


def clean_label(value: object) -> str:
    if pd.isna(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() == "nan" else text


def build_graph(df_matches: pd.DataFrame) -> Graph:
    if not all(col in df_matches.columns for col in REQUIRED_COLUMNS):
        raise ValueError("Faltan columnas obligatorias")

    graph = Graph()
    for prefix, ns in [("ex", EX), ("class", CLASS), ("prop", PROP), ("resource", RESOURCE),
                       ("rdf", RDF), ("rdfs", RDFS), ("xsd", XSD)]:
        graph.bind(prefix, ns)

    added_competitions, added_seasons = set(), set()
    added_stadiums: set[str] = set()

    for _, row in df_matches.iterrows():
        match_id, competition_id, season_id = str(row["id_match"]), str(row["id_competition"]), str(row["id_season"])
        match_name = clean_label(row.get("name", ""))
        match_node, competition_node, season_node = match_uri(match_id), competition_uri(competition_id), season_uri(season_id)

        if competition_id not in added_competitions:
            graph.add((competition_node, RDF.type, COMPETITION))
            added_competitions.add(competition_id)

        if season_id not in added_seasons:
            graph.add((season_node, RDF.type, SEASON))
            added_seasons.add(season_id)

        graph.add((match_node, RDF.type, MATCH))
        graph.add((match_node, RDFS.label, Literal(match_name or match_id, datatype=XSD.string)))
        graph.add((competition_node, HAS_MATCH, match_node))
        graph.add((match_node, BELONGS_TO_COMPETITION, competition_node))
        graph.add((match_node, BELONGS_TO_SEASON, season_node))
        stadium_id = clean_label(row.get("id_stadium", ""))
        if stadium_id:
            stadium_node = stadium_uri(stadium_id)
            if stadium_id not in added_stadiums:
                graph.add((stadium_node, RDF.type, STADIUM))
                added_stadiums.add(stadium_id)
            graph.add((match_node, PLAYED_AT_STADIUM, stadium_node))

        add_literal_if_present(graph, match_node, MATCH_NAME, row["name"], XSD.string)
        add_literal_if_present(graph, match_node, WEEK, row["matchDay"], XSD.integer, int)
        add_literal_if_present(graph, match_node, MATCH_DATE, row["date"], XSD.date,
                              lambda x: pd.to_datetime(x).strftime("%Y-%m-%d"))
        add_literal_if_present(graph, match_node, MATCH_DATETIME, row["dateTime"], XSD.dateTime,
                      lambda x: pd.to_datetime(x).isoformat())
        add_literal_if_present(graph, match_node, MATCH_STATUS, row["matchStatus"], XSD.string, str)
        add_literal_if_present(graph, match_node, HOME_SCORE, row["homeScore"], XSD.integer, int)
        add_literal_if_present(graph, match_node, AWAY_SCORE, row["awayScore"], XSD.integer, int)
        add_literal_if_present(graph, match_node, FTR, row["finalResult"], XSD.string, str)
        add_literal_if_present(graph, match_node, HTHG, row["halftimeHomeScore"], XSD.integer, int)
        add_literal_if_present(graph, match_node, HTAG, row["halftimeAwayScore"], XSD.integer, int)
        add_literal_if_present(graph, match_node, HTR, row["halftimeResult"], XSD.string, str)
        add_literal_if_present(graph, match_node, ATTENDANCE, row["attendance"], XSD.integer, int)
        add_literal_if_present(graph, match_node, ID_SOFASCORE, row["idSofascore"], XSD.string, str)
        add_literal_if_present(graph, match_node, ID_UNDERSTAT, row["idUnderstat"], XSD.string, str)
        add_literal_if_present(graph, match_node, ID_WHOSCORED, row["idWhoscored"], XSD.string, str)

    return graph


def main() -> None:
    parse_no_args("Genera RDF para partidos canonicos.")
    input_path = build_input_path()
    output_path = build_output_path()
    if not input_path.exists():
        raise FileNotFoundError(f"No existe: {input_path}")
    print("Serializando partidos canonicos a RDF...")
    df_matches = pd.read_csv(
        input_path,
        dtype={"id_stadium": "string", "idSofascore": "string", "idUnderstat": "string", "idWhoscored": "string"},
    )
    graph = build_graph(df_matches)
    graph.serialize(destination=output_path, format="turtle")
    print_result("Tripletas", len(graph), output_path)


if __name__ == "__main__":
    main()
