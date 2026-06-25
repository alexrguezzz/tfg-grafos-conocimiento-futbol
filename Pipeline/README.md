# Pipeline - ETL y generacion RDF

Esta carpeta contiene el pipeline de datos del proyecto. Extrae informacion de fuentes de futbol, construye CSV canonicos, genera archivos RDF Turtle, valida los artefactos y puede cargar el TTL final en GraphDB.

## Flujo

```text
extract -> transform -> rdf -> merge -> validate -> load
```

- `extract`: lee fuentes externas y guarda CSV raw en `data/raw/`.
- `transform`: reconstruye CSV canonicos en `data/processed/canonical/` y artefactos auxiliares.
- `rdf`: convierte los CSV canonicos a archivos Turtle en `data/ttl/`.
- `merge`: fusiona los Turtle parciales en `data/ttl/full_knowledge_graph.ttl`.
- `validate`: comprueba normalizacion de jugadores, contexto externo y TTL.
- `load`: carga el TTL fusionado en GraphDB.

Fuentes principales usadas por los extractores:

- ESPN.
- MatchHistory.
- Sofascore.
- Understat.
- WhoScored.
- ClubElo, usado durante `transform` cuando ya existen equipos y temporadas canonicas.

## Requisitos

- Python 3.10 o superior.
- Dependencias de ejecucion usadas por los scripts: `pandas`, `requests`, `rdflib` y `soccerdata`.
- Dependencia de pruebas: `pytest`.
- Acceso a red para `extract` y para enriquecimientos externos de estadios, meteorologia o normalizacion.
- GraphDB disponible si se ejecuta la fase `load`.

Instala las dependencias desde esta carpeta:

```bash
python -m pip install -r requirements.txt
```

## Configuracion

Ejecuta los comandos desde esta carpeta:

```bash
cd Pipeline
```

Ligas admitidas:

- `ESP-La Liga`
- `ENG-Premier League`
- `FRA-Ligue 1`
- `GER-Bundesliga`
- `ITA-Serie A`

Temporadas admitidas:

- `2023-2024`
- `2024-2025`
- `2025-2026`

## Archivo `.env`

`Pipeline/.env` es un archivo local. No debe subirse al repositorio porque puede contener claves privadas.

Copia `Pipeline/.env.example` a `Pipeline/.env` solo si necesitas llamadas en directo a Gemini o quieres sobrescribir valores por defecto. La plantilla se versiona porque no contiene secretos; el `.env` real queda ignorado por Git.

El modulo de normalizacion de jugadores lee automaticamente `Pipeline/.env`. Si no existe `GEMINI_API_KEY`, no se consultara Gemini en directo; el pipeline seguira usando reglas y datos locales disponibles, pero esa normalizacion puede quedar limitada.

No escribas valores reales de claves en el README ni en archivos versionados.

## Variables principales

Variables generales:

- `SOCCERDATA_DIR`: directorio de cache de SoccerData. Si no se indica, se usa el valor por defecto de SoccerData.

Variables para normalizacion con Gemini:

- `GEMINI_API_KEY`: clave para llamadas en directo a Gemini.
- `GEMINI_PROGRESS`: muestra progreso de llamadas y decisiones.
- `GEMINI_MODEL`, `GEMINI_FALLBACK_MODELS`: modelo principal y modelos alternativos.
- `GEMINI_RETRY_MODEL`, `GEMINI_RETRY_FALLBACK_MODELS`: modelo y alternativas para reintentos.
- `GEMINI_MODEL_RPM_LIMITS`: limites de peticiones por minuto por modelo, con formato `modelo:limite,modelo:limite`.
- `GEMINI_MAX_LIVE_CALLS`: limite opcional de llamadas en directo.
- `GEMINI_TIMEOUT_SECONDS`, `GEMINI_RETRY_ATTEMPTS`, `GEMINI_RETRY_BASE_SECONDS`: timeouts y reintentos.
- `GEMINI_MIN_SECONDS_BETWEEN_CALLS`: separacion minima entre llamadas.

Variables para enriquecimiento de estadios:

- `STADIUM_ENABLE_REMOTE_LOOKUP`: activa o desactiva busquedas remotas. Por defecto, activado.
- `STADIUM_WIKIDATA_BATCH_SEARCH_ENABLED`: activa o desactiva busquedas batch en Wikidata. Por defecto, activado si las busquedas remotas estan activadas.
- `STADIUM_HTTP_TIMEOUT_SECONDS`, `STADIUM_MAX_HTTP_ATTEMPTS`: timeout y numero de intentos HTTP.
- `STADIUM_REMOTE_TIME_BUDGET_SECONDS`: presupuesto maximo de tiempo para busquedas remotas.
- `STADIUM_WIKIDATA_API_INTERVAL_SECONDS`, `STADIUM_NOMINATIM_INTERVAL_SECONDS`: espera minima entre llamadas a esos servicios.

Variables para carga en GraphDB:

- `GRAPHDB_BASE_URL`: URL base de GraphDB. Por defecto, `http://localhost:7200`.
- `GRAPHDB_REPOSITORY_ID`: repositorio de GraphDB. Por defecto, `TFG_SoccerData`.
- `GRAPHDB_TTL_FILENAME`: TTL a cargar desde `data/ttl/`. Por defecto, `full_knowledge_graph.ttl`.
- `GRAPHDB_USERNAME` y `GRAPHDB_PASSWORD`: credenciales opcionales.
- `GRAPHDB_CONTEXT_GRAPH_URI`: grafo nombrado opcional para la carga.
- `GRAPHDB_CONNECT_TIMEOUT_SECONDS` y `GRAPHDB_READ_TIMEOUT_SECONDS`: timeouts de carga.

El ejecutor inyecta internamente `SOCCERDATA_PIPELINE_LEAGUES` y `SOCCERDATA_PIPELINE_SEASONS` para acotar transformaciones. No hace falta definir esas variables a mano.

El codigo tambien admite variables tecnicas mas finas para reintentos, limites y candidatos de busqueda. Solo suele ser necesario tocarlas si una ejecucion remota esta fallando por rate limits o tiempos de espera.

## Ejecucion

Mostrar el plan sin ejecutar:

```bash
python src/pipeline/run_pipeline.py --phases all --leagues "ESP-La Liga" --seasons 2025-2026 --dry-run
```

Ejecutar el pipeline completo para una liga y temporada:

```bash
python src/pipeline/run_pipeline.py --phases all --leagues "ESP-La Liga" --seasons 2025-2026
```

Ejecutar varias ligas o temporadas:

```bash
python src/pipeline/run_pipeline.py --phases extract transform --leagues "ESP-La Liga" "ENG-Premier League" --seasons 2024-2025 2025-2026
```

Regenerar RDF y validar a partir de CSV canonicos ya existentes:

```bash
python src/pipeline/run_pipeline.py --phases rdf merge validate
```

Cargar el TTL final en GraphDB:

```bash
python src/pipeline/run_pipeline.py --phases load
```

Por defecto, `load` limpia los statements del repositorio antes de cargar. Para conservar datos previos:

```bash
python src/pipeline/run_pipeline.py --phases load --no-clear-before-upload
```

## Reglas de alcance

- `extract`, `transform` y `all` requieren `--leagues` y `--seasons`.
- `validate` acepta alcance opcional; solo lo usan los validadores compatibles.
- `rdf`, `merge` y `load` ignoran `--leagues` y `--seasons`.
- Si se indica `--leagues`, tambien debe indicarse `--seasons`.
- `all` no se puede combinar con otras fases.

## Scripts individuales

El ejecutor principal es `src/pipeline/run_pipeline.py`, pero los scripts tambien se pueden lanzar de forma individual cuando se quiere aislar una tarea:

```bash
python src/extract/extract_sofascore_read_schedule.py --leagues "ESP-La Liga" --seasons 2025-2026
python src/rdf/rdf_events.py --events-rdf-chunk-size 50000
python src/load/load_graphdb.py --no-clear-before-upload
```

## Salidas

Las salidas se crean durante la ejecucion y pueden no existir en una copia limpia:

- `data/raw/`: CSV extraidos de fuentes externas.
- `data/processed/canonical/`: entidades y relaciones canonicas del modelo.
- `data/processed/normalization/`: identidades, alias, cache y auditoria de normalizacion de jugadores.
- `data/processed/context/`: caches y auditorias de estadios y meteorologia.
- `data/ttl/`: archivos Turtle parciales y `full_knowledge_graph.ttl`.
- `logs/pipeline_report.json`: informe de auditoria de la ultima ejecucion gestionada.

El TTL final esperado por la fase `load` es:

```text
data/ttl/full_knowledge_graph.ttl
```

## Pruebas

Instalar dependencias:

```bash
python -m pip install -r requirements.txt
```

Ejecutar la suite:

```bash
python -m pytest
```

La configuracion de pytest esta en `pytest.ini`. Las pruebas escriben temporales bajo `tests/runtime/` y tienen desactivado el cache de pytest.

## Estructura

```text
Pipeline/
|-- src/
|   |-- extract/
|   |-- transform/
|   |-- rdf/
|   |-- validation/
|   |-- load/
|   |-- pipeline/
|   `-- utils/
|-- tests/
|-- .env.example
|-- pytest.ini
|-- requirements.txt
`-- README.md
```

Directorios generados por ejecucion:

```text
Pipeline/
|-- data/
|   |-- raw/
|   |-- processed/
|   `-- ttl/
|-- logs/
`-- tests/runtime/
```

## Limpieza

Se pueden borrar sin perder codigo:

- `__pycache__/`
- `*.pyc`
- `.pytest_cache/`
- `.mypy_cache/`
- `.ruff_cache/`
- `.ipynb_checkpoints/`
- `tests/runtime/`
- `.pytest_tmp_cache/`
- `downloaded_files/`

No borres `data/` ni `logs/` si quieres conservar resultados de una ejecucion.
