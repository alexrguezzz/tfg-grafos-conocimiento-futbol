from __future__ import annotations

from pathlib import Path
import sys

import pandas as pd

if __name__ == "__main__":
    from rdf_args import parse_no_args
    parse_no_args("Genera RDF para estadios canonicos.")

from rdflib import Graph, Literal
from rdflib.namespace import RDF, RDFS, XSD

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(PROJECT_ROOT))

from src.rdf.rdf_args import parse_no_args  # noqa: E402
from src.pipeline.console_output import print_result  # noqa: E402
from src.utils.namespaces import (  # noqa: E402
    EX,
    CLASS,
    PROP,
    RESOURCE,
    STADIUM,
    NAME,
    CITY,
    COUNTRY,
    LATITUDE,
    LONGITUDE,
    ID_WIKIDATA,
    ID_OSM,
    stadium_uri,
)


REQUIRED_COLUMNS = [
    "id_stadium",
    "name",
    "city",
    "country",
    "latitude",
    "longitude",
    "idWikidata",
    "idOsm",
]


def build_input_path() -> Path:
    return PROJECT_ROOT / "data" / "processed" / "canonical" / "stadiums.csv"


def build_output_path() -> Path:
    output_dir = PROJECT_ROOT / "data" / "ttl"
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir / "stadiums.ttl"


def add_literal_if_present(graph: Graph, node, prop, value, datatype, converter=None) -> None:
    if pd.isna(value):
        return
    if isinstance(value, str) and not value.strip():
        return
    out = converter(value) if converter else value
    graph.add((node, prop, Literal(out, datatype=datatype)))


def build_graph(df: pd.DataFrame) -> Graph:
    missing = [column for column in REQUIRED_COLUMNS if column not in df.columns]
    if missing:
        raise ValueError(f"Faltan columnas obligatorias en stadiums.csv: {missing}")

    graph = Graph()
    for prefix, ns in [("ex", EX), ("class", CLASS), ("prop", PROP), ("resource", RESOURCE), ("rdf", RDF), ("rdfs", RDFS), ("xsd", XSD)]:
        graph.bind(prefix, ns)

    for _, row in df.iterrows():
        stadium_id = str(row["id_stadium"])
        node = stadium_uri(stadium_id)
        graph.add((node, RDF.type, STADIUM))
        graph.add((node, RDFS.label, Literal(row["name"], datatype=XSD.string)))
        add_literal_if_present(graph, node, NAME, row["name"], XSD.string, str)
        add_literal_if_present(graph, node, CITY, row["city"], XSD.string, str)
        add_literal_if_present(graph, node, COUNTRY, row["country"], XSD.string, str)
        add_literal_if_present(graph, node, LATITUDE, row["latitude"], XSD.double, float)
        add_literal_if_present(graph, node, LONGITUDE, row["longitude"], XSD.double, float)
        add_literal_if_present(graph, node, ID_WIKIDATA, row["idWikidata"], XSD.string, str)
        add_literal_if_present(graph, node, ID_OSM, row["idOsm"], XSD.string, str)

    return graph


def main() -> None:
    parse_no_args("Genera RDF para estadios canonicos.")
    input_path = build_input_path()
    output_path = build_output_path()
    if not input_path.exists():
        raise FileNotFoundError(f"No existe: {input_path}")
    print("Serializando estadios canonicos a RDF...")
    df = pd.read_csv(input_path, dtype="string")
    graph = build_graph(df)
    graph.serialize(destination=output_path, format="turtle")
    print_result("Tripletas", len(graph), output_path)


if __name__ == "__main__":
    main()
