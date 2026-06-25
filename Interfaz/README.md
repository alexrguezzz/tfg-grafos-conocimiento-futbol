# Interfaz - Visualizador de datos

Esta carpeta contiene una aplicacion web en Flask para explorar datos RDF mediante consultas SPARQL contra un repositorio de GraphDB configurado.

La aplicacion permite seleccionar competicion y temporada al entrar, aplicar filtros globales, consultar partidos, equipos y jugadores, y comparar entidades dentro del contexto filtrado.

## Tecnologias

- Python 3.10 o superior.
- Flask.
- Requests.
- GraphDB como fuente RDF.
- SPARQL para la capa de consulta principal.

## Requisitos

- Tener Python disponible en `PATH`.
- Instalar las dependencias de `requirements.txt`.
- Tener accesible un repositorio GraphDB con los datos RDF esperados por la aplicacion.

Endpoint por defecto de GraphDB:

```text
http://127.0.0.1:7200/repositories/TFG_SoccerData
```

## Configuracion

Variables de entorno opcionales:

- `GRAPHDB_ENDPOINT`: endpoint del repositorio en GraphDB.
- `GRAPHDB_QUERY_TIMEOUT`: timeout de consultas SPARQL en segundos. Por defecto, `12`.
- `GRAPHDB_STATEMENT_TIMEOUT`: timeout para lecturas directas de statements en segundos. Por defecto, `2`.
- `SECRET_KEY`: clave de sesion de Flask.
- `DATAGOL_HOST`: host local de Flask. Por defecto, `127.0.0.1`.
- `DATAGOL_PORT`: puerto inicial de Flask. Por defecto, `5000`.

La aplicacion no carga automaticamente archivos `.env`; define estas variables en el entorno del proceso si quieres cambiar los valores por defecto. Los valores base estan definidos en `config.py`, `app.py` y `services/query.py`.

## Ejecucion

### Opcion 1: script incluido

1. Abre una terminal en esta carpeta.
2. Ejecuta `run.bat`.
3. Abre la URL que muestre la consola.

El script actualiza `pip`, instala dependencias y arranca la aplicacion.

### Opcion 2: arranque manual

```bash
python -m pip install -r requirements.txt
python app.py
```

`python app.py` intenta arrancar en `127.0.0.1:5000`. Si ese puerto esta ocupado, busca el siguiente puerto libre dentro de un rango corto y abre automaticamente el navegador.

Para desarrollo tambien puedes arrancar Flask directamente:

```bash
python -m flask --app app run --host 127.0.0.1 --port 5001
```

## Flujo de uso

Al entrar por primera vez, la aplicacion consulta GraphDB y solicita una seleccion inicial de liga y temporada entre los pares disponibles en los datos cargados. Esa seleccion se guarda en la sesion del navegador y acota las opciones disponibles en los filtros globales.

La aplicacion no impone una lista cerrada de ligas o temporadas: cualquier competicion y temporada que aparezca enlazada en GraphDB se ofrece como opcion. Las cinco ligas principales tienen logo propio; el resto se muestra sin imagen.

## Funcionalidad implementada

- Navbar con vistas: Inicio, Competicion, Partidos, Equipos, Jugadores y Comparador.
- Filtros globales por competicion, temporada, jornadas y fechas.
- Buscador global de equipos y jugadores con navegacion directa a sus fichas.
- Persistencia visual del tema claro/oscuro en navegador.
- Onboarding inicial para acotar liga y temporada.
- Ordenacion consistente de partidos por jornada y fecha cuando aplica.
- Tratamiento de jugadores no disponibles, con motivo cuando existe en los datos.
- Mensajes explicitos cuando no hay datos disponibles o falla la consulta.

## Rutas principales

- `/`: inicio.
- `/competition`: clasificacion y resumen de competicion.
- `/matches`: listado de partidos.
- `/match`: detalle de partido.
- `/teams`: listado y ficha de equipo.
- `/players`: listado y ficha de jugador.
- `/compare`: comparador de equipos o jugadores.
- `/scorers`: ranking de goleadores.
- `/assists`: ranking de asistentes.
- `/search/suggestions`: sugerencias del buscador global.
- `/selection` y `/selection/options`: seleccion inicial de competicion y temporada.

## Vistas

### Inicio

- KPIs generales de partidos, equipos, jugadores, goles, tarjetas y ultimo partido.
- Resumen global dentro del contexto filtrado.
- Tablas y graficos compactos para lectura rapida.

### Competicion

- Tabla de clasificacion por competicion y temporada.
- Posicion, puntos, partidos jugados, victorias, empates, derrotas y balance goleador.
- Vista orientada a temporada completa.

### Partidos

- Listado de partidos ocupando todo el ancho disponible.
- Orden por jornada y, dentro de cada jornada, por fecha y hora de mas antiguo a mas reciente.
- Filtros adicionales de jornada y rango de fechas.
- Enlace al detalle de cada partido.

### Detalle de partido

- Cabecera con marcador en una sola linea, fecha, jornada, local, visitante, estadio y asistencia.
- Linea de tiempo interactiva: los eventos se muestran al pasar el raton o hacer click.
- Estadisticas comparadas de equipos.
- Estadisticas de jugadores ordenadas por titulares, suplentes y resto.
- Mapa de eventos y disparos sobre el campo.
- Panel de destino de disparo en porteria cuando hay coordenadas disponibles.

### Equipos

- KPIs de equipo dentro de la temporada seleccionada.
- Elo en una fecha antes del grafico de evolucion Elo.
- Historial de clasificacion y partidos del equipo.
- Plantilla filtrada por la temporada elegida.
- Conteo de jugadores de la temporada seleccionada.

### Jugadores

- Listado de jugadores ajustado al alcance de filtros cuando corresponde.
- Ficha de jugador con KPIs de la temporada seleccionada.
- Estadisticas historicas del jugador sin filtrar por temporada.
- Partidos historicos del jugador sin filtrar, ordenados de mas recientes a mas antiguos.
- Filtros internos por estado: todos, titular, suplente, no jugado y no disponible.
- Motivo visible en los registros de no disponible cuando el dato existe.

### Comparador

- Comparacion entre equipos o jugadores segun el modo seleccionado.
- KPIs y paneles de evolucion para analizar rendimiento dentro del contexto filtrado.

## Estructura

```text
Interfaz/
|-- app.py
|-- config.py
|-- requirements.txt
|-- run.bat
|-- routes/
|-- services/
|-- static/
`-- templates/
```

Carpetas principales:

- `routes`: registro de rutas y construccion de vistas.
- `services`: filtros, consultas, onboarding, utilidades y componentes UI.
- `templates`: plantillas HTML y parciales.
- `static`: logos, imagen de marca y recursos visuales.

## Recursos graficos

Assets esperados para las ligas:

- `static/images/leagues/la-liga.svg`
- `static/images/leagues/premier-league.svg`
- `static/images/leagues/bundesliga.svg`
- `static/images/leagues/ligue-1.svg`
- `static/images/leagues/serie-a.svg`

Iconos de eventos:

- `static/images/events/gol.svg`
- `static/images/events/penalti-marcado.svg`
- `static/images/events/penalti-fallado.svg`
- `static/images/events/penalti-parado.svg`
- `static/images/events/sustitucion.svg`
- `static/images/events/tarjeta-amarilla.svg`
- `static/images/events/tarjeta-roja.svg`
- `static/images/events/tarjeta-segunda-amarilla.svg`

Logo de marca:

- `static/images/brand/datagol-logo.png`

## Limpieza

Se pueden borrar sin perder codigo:

- `__pycache__/`
- `*.pyc`
- `.pytest_cache/`
- `.mypy_cache/`
- `.ruff_cache/`
- `.ipynb_checkpoints/`

## Notas

- El servidor Flask arranca con `debug=False` cuando se usa `python app.py`.
- `app.py` abre automaticamente el navegador al iniciar la aplicacion en ejecucion local.
- Si GraphDB no esta disponible, las consultas fallan y la interfaz muestra el error correspondiente.
