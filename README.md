# tfg-grafos-conocimiento-futbol

Repositorio del TFG sobre grafos de conocimiento aplicados a datos de futbol.
El proyecto construye un grafo RDF a partir de fuentes externas de futbol y
ofrece una aplicacion web para consultarlo desde GraphDB.

## Componentes

- [Pipeline](Pipeline/): extraccion, transformacion, generacion RDF, validacion y carga del grafo en GraphDB.
- [Interfaz](Interfaz/): aplicacion web Flask que consulta GraphDB mediante SPARQL.

## Flujo general

```text
Pipeline -> GraphDB -> Interfaz
```

`Pipeline/` prepara los datos y carga el grafo RDF en GraphDB. `Interfaz/` se conecta al repositorio de GraphDB y permite explorar esos datos desde el navegador.

## Requisitos generales

- Python 3.10 o superior.
- GraphDB si se quiere cargar y consultar el grafo desde la interfaz.
- Acceso a red para ejecutar la fase de extraccion y algunos enriquecimientos externos.

## Configuracion local

El pipeline puede usar variables locales desde `Pipeline/.env`, pero ese archivo no se versiona. Si necesitas configurar Gemini, GraphDB o parametros de enriquecimiento remoto, copia `Pipeline/.env.example` a `Pipeline/.env` y rellena solo los valores necesarios.

La interfaz lee sus variables desde el entorno del proceso. Sus valores opcionales estan documentados en [Interfaz/README.md](Interfaz/README.md).

## Documentacion

- [Pipeline/README.md](Pipeline/README.md): configuracion, ejecucion, salidas, pruebas y variables propias del pipeline.
- [Interfaz/README.md](Interfaz/README.md): configuracion, ejecucion, rutas y funcionamiento propio de la aplicacion web.

## Estructura

```text
.
|-- Interfaz/
|   |-- README.md
|   `-- requirements.txt
|-- Pipeline/
|   |-- .env.example
|   |-- README.md
|   |-- requirements.txt
|   |-- pytest.ini
|   |-- src/
|   `-- tests/
|-- .gitignore
`-- README.md
```

## Puesta en marcha resumida

1. Instala y ejecuta el pipeline siguiendo [Pipeline/README.md](Pipeline/README.md).
2. Carga `Pipeline/data/ttl/full_knowledge_graph.ttl` en GraphDB, directamente o con la fase `load`.
3. Instala y ejecuta la interfaz siguiendo [Interfaz/README.md](Interfaz/README.md).
4. Abre la URL local de Flask y selecciona competicion y temporada.

## Archivos no versionados

No se versionan claves privadas ni resultados generados. En particular,
`Pipeline/.env`, `Pipeline/data/`, `Pipeline/logs/`, `Pipeline/downloaded_files/`,
caches de Python, temporales de pruebas, entornos virtuales y artefactos de cobertura quedan excluidos por `.gitignore`. La plantilla `Pipeline/.env.example` si se versiona porque no contiene secretos.
