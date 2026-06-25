from pathlib import Path
import sys
import pandas as pd

if __name__ == "__main__":
    from rdf_args import parse_no_args
    parse_no_args("Genera RDF para temporadas canonicas.")

from rdflib import Graph, Literal
from rdflib.namespace import RDF, RDFS, XSD

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(PROJECT_ROOT))

from src.rdf.rdf_args import parse_no_args
from src.pipeline.console_output import print_result
from src.utils.namespaces import EX, CLASS, PROP, RESOURCE, SEASON, NAME, SEASON_ID_UNDERSTAT, season_uri


REQUIRED_COLUMNS = ["id_season", "name", "id_understat"]


def build_input_path() -> Path:
    return PROJECT_ROOT / "data" / "processed" / "canonical" / "seasons.csv"


def build_output_path() -> Path:
    output_dir = PROJECT_ROOT / "data" / "ttl"
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir / "seasons.ttl"


def add_literal_if_present(graph: Graph, node, prop, value, datatype, converter=None) -> None:
    if pd.isna(value):
        return
    if isinstance(value, str) and not value.strip():
        return
    out = converter(value) if converter else value
    graph.add((node, prop, Literal(out, datatype=datatype)))


def clean_label(value: object) -> str:
    if pd.isna(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() == "nan" else text


def build_graph(df: pd.DataFrame) -> Graph:
    if not all(col in df.columns for col in REQUIRED_COLUMNS):
        raise ValueError("Faltan columnas obligatorias en seasons.csv")

    graph = Graph()
    for prefix, ns in [("ex", EX), ("class", CLASS), ("prop", PROP), ("resource", RESOURCE), ("rdf", RDF), ("rdfs", RDFS), ("xsd", XSD)]:
        graph.bind(prefix, ns)

    for _, row in df.iterrows():
        season_id = str(row["id_season"])
        name = clean_label(row.get("name", ""))
        node = season_uri(season_id)
        graph.add((node, RDF.type, SEASON))
        graph.add((node, RDFS.label, Literal(name or season_id, datatype=XSD.string)))
        add_literal_if_present(graph, node, NAME, row["name"], XSD.string, str)
        add_literal_if_present(graph, node, SEASON_ID_UNDERSTAT, row["id_understat"], XSD.string, str)

    return graph


def main() -> None:
    parse_no_args("Genera RDF para temporadas canonicas.")
    input_path = build_input_path()
    output_path = build_output_path()
    if not input_path.exists():
        raise FileNotFoundError(f"No existe: {input_path}")
    print("Serializando temporadas canonicas a RDF...")
    df = pd.read_csv(input_path, dtype={"id_understat": "string"})
    graph = build_graph(df)
    graph.serialize(destination=output_path, format="turtle")
    print_result("Tripletas", len(graph), output_path)


if __name__ == "__main__":
    main()
