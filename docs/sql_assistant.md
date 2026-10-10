# Asistente SQL de solo lectura (Parte B)

Revisión previa de documentación de FastMCP el 2026-10-10:
[herramientas](https://gofastmcp.com/servers/tools),
[lifespan](https://gofastmcp.com/servers/lifespan),
[cliente en memoria](https://gofastmcp.com/clients/client) y
[ejecución del servidor](https://gofastmcp.com/deployment/running-server).
Implementado y probado con FastMCP 3.4.8, sin endpoints HTTP.

## Uso

Instalar dependencias con `uv sync`. Configurar `DATABASE_URL_READONLY`
(preferida) o `DATABASE_URL`, y para preguntas `GEMINI_API_KEY` y `GEMINI_MODEL`.
Las herramientas MCP no necesitan Gemini. No se registran llaves ni URLs.

- Servidor MCP por stdio: `uv run python -m glazedco.sql_assistant`
- Pregunta mediante Gemini y MCP en memoria:
  `uv run python -m glazedco.sql_assistant --question "¿Cuáles son los ingresos netos por sucursal?"`

Estos comandos sí conectan a los servicios configurados; no se ejecutaron
contra servicios reales durante esta tarea. El pool se abre en el lifespan
del servidor y se cierra al terminar o fallar la carga del catálogo.

## Esquema y seguridad

`config.py` contiene la lista explícita de las trece tablas principales.
No se descubren tablas: al iniciar se leen únicamente las columnas y tipos de
esa lista desde `information_schema`, restringida a tablas base del esquema
configurado. Se filtra otra vez el resultado en Python. Si falta una tabla
autorizada o sus columnas, el servicio no inicia. `get_schema` devuelve copias
del esquema cargado; cambios posteriores del catálogo requieren reinicio.
Se excluyen staging, rechazos, `sales_daily_original` y `weather_daily`.

`SQL_MAX_ROWS` (1000), `SQL_STATEMENT_TIMEOUT_MS` (5000) y `SQL_SCHEMA`
(`public`) configuran la política; el modelo no puede cambiarlos.
`run_readonly_sql` devuelve `status`, `columns`, `rows` y `warning`.
Un rechazo del validador, incluso por excepción inesperada, devuelve `rejected`
sin adquirir conexión. Errores de ejecución devuelven `error` sin detalles.

Solo se ejecuta `ValidatedSQL.sql`, sin parámetros. La lectura del catálogo
es una consulta fija y parametrizada independiente del SQL del modelo.
Se reutiliza `Database(assistant=True)`: transacción READ ONLY, rollback forzado,
`SET TRANSACTION READ ONLY`, `SET LOCAL statement_timeout` con el valor validado
y `SET LOCAL search_path = pg_catalog`. Las tablas del SQL validado ya están
calificadas por esquema. El LIMIT lo fuerza Parte A y la lectura usa
`fetchmany(max_rows)`. Las sentencias SET solo configuran la transacción;
no se modifica ningún dato ni esquema.

El fallback a `DATABASE_URL` no convierte sus permisos en un rol restringido;
la transacción sigue siendo de solo lectura. Es preferible provisionar
externamente un rol dedicado con permisos mínimos. Esta tarea no crea roles,
otorga permisos ni verifica privilegios en la base real.

## Flujo y explicación verificable

`answer_question` consulta `get_schema` mediante MCP y envía a Gemini el esquema,
la pregunta y las definiciones de `dataset_context.md` usando `profile="sql"`
(temperatura 0). Si responde `no tengo ese dato`, no ejecuta SQL. Si la herramienta
rechaza la propuesta, reintenta exactamente una vez con un motivo genérico,
sin transmitir excepciones internas. El segundo rechazo termina con una
respuesta segura. Errores de ejecución o del proveedor no provocan reintentos.
Si no hay filas, lo informa sin pedir una explicación a Gemini.

Con filas, Gemini usa `profile="analysis"` (temperatura 0) y recibe solo columnas
y filas devueltas. Su salida se restringe a un JSON de referencias a celdas:
`{"hechos":[{"fila":0,"columna":0}]}`. Selecciona hasta doce hechos; el renderer
comprueba los índices y produce frases en español con los valores originales.
No publica prosa libre, cifras nuevas ni cálculos del modelo. Una salida inválida
produce un mensaje de explicación no verificable. Decimal se preserva como texto
y null se verbaliza como desconocido. Esto sacrifica variedad narrativa por
fidelidad; no pretende validar que la consulta responda semánticamente la pregunta.

La explicación informa que solo describe las filas devueltas (posiblemente
limitadas) y siempre advierte sobre tablas sin limpiar y diferencias con el
dashboard, remitiendo a `docs/cleaning_report.md`, sin copiar cifras del reporte.
La Parte A y los perfiles/prompts existentes de Gemini no se modificaron.

## Pruebas

`uv run --offline --no-sync pytest tests/test_sql_assistant.py tests/test_sql_validator.py tests/test_db.py tests/test_gemini.py -q`

Base y Gemini simulados; el contrato MCP se prueba con `Client(server)` en
memoria. Se bloquean conexiones de red después de crear el socketpair interno
que asyncio necesita en Windows. Se cubren rechazos sin ejecución, fallos del
validador, texto SQL regenerado, timeout/solo lectura/rollback, límites, selección
de URL, filtrado del esquema, lifespan, reintento único, resultados vacíos,
errores redactados y explicaciones sin cifras fabricadas.
