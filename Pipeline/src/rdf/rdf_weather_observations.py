from __future__ import annotations

from pathlib import Path
import sys

import pandas as pd

if __name__ == "__main__":
    from rdf_args import parse_no_args
    parse_no_args("Genera RDF para observaciones meteorologicas.")

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
    MATCH,
    WEATHER_OBSERVATION,
    HAS_WEATHER_OBSERVATION,
    WEATHER_DATE_TIME,
    TEMPERATURE,
    PRECIPITATION,
    RAIN,
    WIND_SPEED,
    HUMIDITY,
    match_uri,
    weather_observation_uri,
)


REQUIRED_COLUMNS = [
    "id_weatherObservation",
    "id_match",
    "dateTime",
    "temperature",
    "precipitation",
    "rain",
    "windSpeed",
    "humidity",
]


def build_input_path() -> Path:
    return PROJECT_ROOT / "data" / "processed" / "canonical" / "weather_observations.csv"


def build_output_path() -> Path:
    output_dir = PROJECT_ROOT / "data" / "ttl"
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir / "weather_observations.ttl"


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
        raise ValueError(f"Faltan columnas obligatorias en weather_observations.csv: {missing}")

    graph = Graph()
    for prefix, ns in [("ex", EX), ("class", CLASS), ("prop", PROP), ("resource", RESOURCE), ("rdf", RDF), ("rdfs", RDFS), ("xsd", XSD)]:
        graph.bind(prefix, ns)

    for _, row in df.iterrows():
        weather_id = str(row["id_weatherObservation"])
        match_id = str(row["id_match"])
        weather_node = weather_observation_uri(weather_id)
        match_node = match_uri(match_id)

        graph.add((match_node, RDF.type, MATCH))
        graph.add((weather_node, RDF.type, WEATHER_OBSERVATION))
        graph.add((weather_node, RDFS.label, Literal(weather_id, datatype=XSD.string)))
        graph.add((match_node, HAS_WEATHER_OBSERVATION, weather_node))

        add_literal_if_present(graph, weather_node, WEATHER_DATE_TIME, row["dateTime"], XSD.dateTime, lambda x: pd.to_datetime(x).isoformat())
        add_literal_if_present(graph, weather_node, TEMPERATURE, row["temperature"], XSD.double, float)
        add_literal_if_present(graph, weather_node, PRECIPITATION, row["precipitation"], XSD.double, float)
        add_literal_if_present(graph, weather_node, RAIN, row["rain"], XSD.double, float)
        add_literal_if_present(graph, weather_node, WIND_SPEED, row["windSpeed"], XSD.double, float)
        add_literal_if_present(graph, weather_node, HUMIDITY, row["humidity"], XSD.double, float)

    return graph


def main() -> None:
    parse_no_args("Genera RDF para observaciones meteorologicas.")
    input_path = build_input_path()
    output_path = build_output_path()
    if not input_path.exists():
        raise FileNotFoundError(f"No existe: {input_path}")
    print("Serializando observaciones meteorologicas a RDF...")
    df = pd.read_csv(input_path, dtype="string")
    graph = build_graph(df)
    graph.serialize(destination=output_path, format="turtle")
    print_result("Tripletas", len(graph), output_path)


if __name__ == "__main__":
    main()
