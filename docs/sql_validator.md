# Validador SQL local (Parte A)

Se revisaron las [herramientas de FastMCP](https://gofastmcp.com/servers/tools)
el 2026-10-10, antes de implementar. FastMCP permite herramientas mediante
funciones tipadas y `@mcp.tool`; sus anotaciones y timeout no sustituyen la
validación del SQL ni el timeout de PostgreSQL. Esta parte no crea un servidor,
endpoints, herramientas MCP, conexiones ni flujo con Gemini.

`glazedco.sql_validator.validate_sql(sql, allowed_schema, policy=SQLPolicy(...))`
recibe una lista permitida de `{tabla: {columna: tipo}}` de configuración
confiable. Los nombres deben corresponder exactamente al catálogo autorizado;
no se supone que los CSV reflejen la base. No descubre el esquema por red.

Usa el AST PostgreSQL de sqlglot y una lista cerrada de construcciones y
funciones. Admite SELECT, CTE no recursivas, subconsultas, joins, agregaciones,
CASE y aritmética. Rechaza escrituras incluso dentro de CTE, SELECT INTO,
bloqueos, sentencias múltiples, funciones desconocidas, casts, UNION y otras
construcciones no revisadas. Una restricción conservadora puede rechazar SQL
de lectura válido; ampliar la lista exige revisión y pruebas.

Resuelve columnas, alias y ámbitos contra el esquema permitido, rechaza
referencias ambiguas y filas completas, expande comodines y califica tablas
con el esquema configurado (por defecto `public`). Conserva nombres entre
comillas y normaliza los demás según PostgreSQL. No consulta tipos en la BD.

`SQLPolicy(max_rows=1000, statement_timeout_ms=5000, schema_name="public")`
acepta límites y timeout enteros positivos de 32 bits. El LIMIT externo se
inserta o reduce; preserva un límite menor, incluido 0, y reemplaza ALL/NULL.
No limita el trabajo de joins o agregaciones ni garantiza un tiempo de cálculo.

La salida inmutable `ValidatedSQL` contiene `sql` y `statement_timeout_ms`.
El timeout es configuración para el futuro ejecutor: **aquí no se aplica**,
porque no existe ejecución. La siguiente parte deberá configurar el timeout
de sentencia y una transacción de solo lectura usando conexión/rol sin permiso
de escritura; también deberá controlar search_path y objetos autorizados.
El validador por sí solo no constituye una barrera de permisos de PostgreSQL.

Verificación sin red ni base de datos:
`uv run --offline --no-sync pytest tests/test_sql_validator.py -q`.
Las pruebas bloquean sockets y usan únicamente esquemas ficticios en memoria.
No verifican explicaciones de Gemini ni resultados SQL; quedan fuera de Parte A.
