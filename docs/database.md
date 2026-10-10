# PostgreSQL en Tiger Data

La base ya está poblada. Este módulo únicamente consulta; no hay migraciones ni cargas.
Instalar dependencias con `uv sync --inexact`. `python-dotenv` carga automáticamente el `.env`
de la raíz al inicializar `Database`; las variables del proceso tienen prioridad.
No registrar ni compartir `.env`, las URL ni los errores originales del driver.

```powershell
uv run --inexact python -m glazedco.db_check
uv run --inexact python -m glazedco.db_check --schema public
uv run --inexact python -m glazedco.db_check --assistant
uv run --inexact pytest -m "not db" -q
uv run --inexact pytest -m db -q
```

El esquema por defecto es `current_schema()`; seleccionar otro con `--schema`.
`--inexact` conserva paquetes ya instalados si uv utiliza un entorno compartido.
El chequeo ejecuta `SELECT 1`, consulta versión, verifica SSL/solo lectura y lista
tablas con estimaciones de `pg_class.reltuples`, incluyendo descendientes.
No cuenta filas de negocio. `null` significa estadísticas desconocidas; las
estimaciones pueden estar desactualizadas o ser incompletas con compresión Timescale.
Si existe TimescaleDB, consulta su vista de hypertables y marca las del esquema.

`Database(assistant=True)` usa `DATABASE_URL_READONLY` si existe; en caso contrario,
usa `DATABASE_URL`. Ambas rutas establecen transacciones de solo lectura y hacen
rollback al salir. El chequeo muestra los nombres de las variables seleccionadas,
nunca sus valores. Las futuras consultas del asistente deben limitarse a SELECT;
este cambio no implementa generación ni ejecución de SQL de lenguaje natural.

Pool: 0–2 conexiones, espera de pool 15 s, conexión 10 s y sentencia 15 s;
SSL `require`, sin rebajar el cifrado al fallar. El timeout de conexión se aplica
por host si la URL especifica varios. Ante fallos se muestran categorías seguras
(SSL, credenciales, red, permisos, timeout); no se cambia la configuración remota.

`db_readonly_role.sql` es un script psql administrativo sin ejecutar: conectarse a
la base correcta y revisar la lista explícita de tablas observadas en `public`.
La lista excluye staging, rechazos y `sales_daily_original`. No concede acceso general
a todas las tablas ni a tablas futuras. Revisar permisos heredados de PUBLIC;
`default_transaction_read_only` es una defensa adicional, no sustituye privilegios.

Chequeo realizado: PostgreSQL 18.6, esquema `public`, SSL y transacción de solo
lectura confirmados. Hypertables detectadas: `inventory_daily`, `sales_daily` y
`store_daily_kpis`. El asistente utiliza `DATABASE_URL` porque no está definida
`DATABASE_URL_READONLY`; el rol propuesto sigue pendiente de revisión y ejecución manual.

Documentación consultada:
- [Psycopg: pool y kwargs](https://www.psycopg.org/psycopg3/docs/api/pool.html)
- [Psycopg: transacciones y read_only](https://www.psycopg.org/psycopg3/docs/api/connections.html)
- [PostgreSQL: SSL y parámetros de conexión](https://www.postgresql.org/docs/current/libpq-connect.html)
- [PostgreSQL: estadísticas](https://www.postgresql.org/docs/current/planner-stats.html)
- [Tiger Data: hypertables](https://www.tigerdata.com/docs/reference/timescaledb/informational-views/hypertables)
