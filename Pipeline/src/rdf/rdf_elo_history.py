from pathlib import Path
import sys
import pandas as pd

if __name__ == "__main__":
    from rdf_args import parse_no_args
    parse_no_args("Genera RDF para el historico Elo.")

from rdflib import Graph, Literal
from rdflib.namespace import RDF, RDFS, XSD

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(PROJECT_ROOT))

from src.rdf.rdf_args import parse_no_args
from src.pipeline.console_output import print_result
from src.utils.namespaces import (
    EX, CLASS, PROP, RESOURCE, TEAM, ELO_RECORD, HAS_ELO_RECORD, CORRESPONDS_TO_TEAM,
    DATE_FROM, DATE_TO, RANK, LEVEL, ELO, team_uri, elo_record_uri,
)


REQUIRED_COLUMNS = ["id_eloRecord", "id_team", "dateFrom", "dateTo", "rank", "level", "elo"]


def build_input_path() -> Path:
    return PROJECT_ROOT / "data" / "processed" / "canonical" / "elo_history.csv"


def build_output_path() -> Path:
    output_dir = PROJECT_ROOT / "data" / "ttl"
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir / "elo_history.ttl"


def build_graph(df_elo: pd.DataFrame) -> Graph:
    if not all(col in df_elo.columns for col in REQUIRED_COLUMNS):
        raise ValueError("Faltan columnas obligatorias")

    graph = Graph()
    for prefix, ns in [("ex", EX), ("class", CLASS), ("prop", PROP), ("resource", RESOURCE), ("rdf", RDF), ("rdfs", RDFS), ("xsd", XSD)]:
        graph.bind(prefix, ns)

    added_teams = set()

    for _, row in df_elo.iterrows():
        elo_id, team_id = str(row["id_eloRecord"]), str(row["id_team"])
        elo_node, team_node = elo_record_uri(elo_id), team_uri(team_id)

        if team_id not in added_teams:
            graph.add((team_node, RDF.type, TEAM))
            added_teams.add(team_id)

        graph.add((elo_node, RDF.type, ELO_RECORD))
        graph.add((elo_node, RDFS.label, Literal(elo_id, datatype=XSD.string)))
        graph.add((team_node, HAS_ELO_RECORD, elo_node))
        graph.add((elo_node, CORRESPONDS_TO_TEAM, team_node))

        if pd.notna(row["dateFrom"]):
            graph.add((elo_node, DATE_FROM, Literal(pd.to_datetime(row["dateFrom"]).strftime("%Y-%m-%d"), datatype=XSD.date)))
        if pd.notna(row["dateTo"]):
            graph.add((elo_node, DATE_TO, Literal(pd.to_datetime(row["dateTo"]).strftime("%Y-%m-%d"), datatype=XSD.date)))
        if pd.notna(row["rank"]):
            graph.add((elo_node, RANK, Literal(int(row["rank"]), datatype=XSD.integer)))
        if pd.notna(row["level"]):
            graph.add((elo_node, LEVEL, Literal(int(row["level"]), datatype=XSD.integer)))
        if pd.notna(row["elo"]):
            graph.add((elo_node, ELO, Literal(float(row["elo"]), datatype=XSD.double)))

    return graph


def main() -> None:
    parse_no_args("Genera RDF para el historico Elo.")
    input_path = build_input_path()
    output_path = build_output_path()
    if not input_path.exists():
        raise FileNotFoundError(f"No existe: {input_path}")
    print("Serializando historico Elo a RDF...")
    df_elo = pd.read_csv(input_path)
    graph = build_graph(df_elo)
    graph.serialize(destination=output_path, format="turtle")
    print_result("Tripletas", len(graph), output_path)


if __name__ == "__main__":
    main()
