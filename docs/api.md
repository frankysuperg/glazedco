# Backend del dashboard

FastAPI expone los módulos existentes, sin frontend ni cambios de reglas de
negocio. Se consultó la documentación vigente de
[modelos de respuesta](https://fastapi.tiangolo.com/tutorial/response-model/) y
[errores](https://fastapi.tiangolo.com/tutorial/handling-errors/) antes de implementar.

Arranque local: `uv run uvicorn glazedco.api.app:app --host 127.0.0.1 --port 8000`.
OpenAPI en `/openapi.json`, documentación interactiva en `/docs`.
La aplicación no consulta PostgreSQL ni Gemini al importarse.

## Arranque y disponibilidad

El [lifespan de FastAPI](https://fastapi.tiangolo.com/advanced/events/) lanza
el precalentamiento en un trabajador en segundo plano y cede inmediatamente
el control al servidor. `GET /api/estado` devuelve `{"listo": false, "detalle": "..."}`
mientras trabaja y `listo: true` únicamente cuando se completaron todos los
cálculos. Las demás rutas devuelven 503 sin esperar al trabajador, con
`error.codigo: API_NO_LISTA`, un mensaje seguro, `reintentar_despues: 5` y
la cabecera `Retry-After: 5`. El fallo de preparación permanece visible en
el estado; corregir su causa y reiniciar. No se publican excepciones privadas.

Se cargan las tablas limpias y los tres reportes, y se precalculan margen,
pronóstico, recomendaciones y alertas tanto globales como para cada sucursal,
con los mismos módulos y filtros existentes. No se consulta Gemini ni TrendsAPI
al arrancar. El tiempo total (éxito o fallo) se imprime en consola. Cada proceso
tiene su propia memoria; el cierre ordenado espera al trabajador para no dejar
lecturas o escrituras pendientes. La preparación todavía necesita tiempo,
pero las peticiones no lo esperan ni disparan el cálculo.

## Rutas

Todas admiten `?sucursal=S001`; omitir el parámetro devuelve
`sucursal: null, vista: "Todas las sucursales"`. Una sucursal desconocida da 404.
Los identificadores se toman del catálogo limpio, no se interpretan como números.

| Método | Ruta | Respuesta |
|---|---|---|
| GET | `/api/estado` | Disponibilidad y detalle seguro del precalentamiento; sin filtro de sucursal |
| GET | `/api/dashboard/resumen` | Margen, pronóstico agregado y conteo de alertas del corte |
| GET | `/api/margen` | Monto/porcentaje separados, cobertura y exclusiones |
| GET | `/api/pronostico/semanal` | Suma por semana de los pronósticos publicados |
| GET | `/api/reabastecimiento` | Recomendaciones de los módulos existentes |
| GET | `/api/anomalias` | Anomalías históricas publicadas, sin declararlas tendencias |
| GET | `/api/alertas` | Quiebre/sobreinventario por SKU y sucursal |
| GET | `/api/sucursales` | Búsqueda `q` literal por nombre o identificador, sin distinguir mayúsculas |
| POST | `/api/asistente` | Cuerpo `{"pregunta":"¿Cuáles son los ingresos netos?"}` |
| POST | `/api/tendencias/estrategias` | Cuerpo `{"objetivo":"Mejorar ventas"}`; 503 mientras no exista fuente verificada |

Las listas admiten `limite` (predeterminado 100, máximo 1000) y
`desplazamiento` (predeterminado 0). Incluyen el total antes de paginar.
Los POST limitan el texto a 4000 caracteres y rechazan campos adicionales.
Todas las respuestas tienen esquema Pydantic; no se serializan objetos internos.

## Datos y cálculo

El adaptador reutiliza las mismas funciones `read_tables` y `clean_all`: caché
Parquet existente en `data/clean` o lectura SELECT y limpieza existentes. Carga los CSV
`forecast_next_weeks.csv`, `forecast_errors.csv` y `anomalies.csv` de `reports`.
No entrena modelos por solicitud ni inventa resultados si falta un archivo.
El margen se recalcula con `aggregate_margin` después del filtro: conserva
denominadores de costo conocido, cobertura, exclusiones y porcentaje nulo con
ingresos base cero. Los agregados del pronóstico son unidades, no ingresos ni
estimaciones de demanda perdida. No se suman bandas como si fueran intervalos
de confianza agregados; un pronóstico faltante deja el agregado nulo.

El reabastecimiento reutiliza `weekly_demand`, `observed_weekly_means` y
`recommend_replenishment`. Antes del cálculo se filtran todas las entradas que
tienen `store_id`, incluyendo errores, inventario, ventas, órdenes y precios.
Los maestros sin sucursal mantienen sus atributos compartidos. Los pronósticos
conservan los modelos ya publicados; no se vuelven a entrenar al filtrar.
El presupuesto y los respaldos se aplican por el módulo al conjunto filtrado,
por lo que una recomendación filtrada puede diferir de su fila en la vista global.

Los datos y resultados se guardan en memoria durante el precalentamiento;
reiniciar el proceso después de publicar nuevos
archivos. El despliegue debe usar caché y reportes del mismo corte. No existe
un manifiesto común que permita garantizar automáticamente esa consistencia.

`API_CLEAN_CACHE_MAX_AGE_SECONDS` controla la antigüedad máxima del Parquet:
86400 segundos por defecto; 0 fuerza recarga en cada arranque. Se exige que las
13 tablas estén presentes, legibles y dentro del plazo. Se acepta la caché
existente de `glazedco.cleaning --cache`. Tras una carga desde PostgreSQL se
guardan los Parquet y un manifiesto local con configuración y firmas de los
archivos; este detecta cambios o escrituras incompletas. Se preservan tipos e
índices y no se modifican tablas en PostgreSQL. La edad se comprueba al arrancar,
no se refresca la memoria mientras hay solicitudes. `data/` sigue ignorado por Git.

## Verificación del cambio de arranque

Antes de editar se guardó `data/verification/resumen_before.json`. La consulta
real falló con 503 durante la lectura de PostgreSQL en dos intentos, por lo que
no se obtuvo una referencia real con cifras. Después se guardaron
`resumen_after.json` y `estado_after.json`: el lifespan devolvió el control en
0,317 s; el resumen respondió 503 inmediatamente y el precalentamiento informó
fallo de carga a los 101,52 s. Los JSON de error difieren por el nuevo contrato
API_NO_LISTA y reintentar_despues, no por un cambio demostrado de cifras.

Se guardaron además `resumen_fixture_before.json` y `resumen_fixture_after.json`
con datos de prueba: igualdad exacta del JSON para Todas las sucursales, S1 y S2.
Estos archivos locales están bajo `data/` y no se versionan. No se arrancó un
servidor de red; todos los clientes en proceso se cerraron. Queda pendiente
comparar un resumen real exitoso cuando PostgreSQL pueda completar la lectura.

Pruebas de disponibilidad, 503 inmediato, recuperación, errores sin secretos,
caché nueva/existente/vencida/incompleta/corrupta y regresiones de endpoints:
`uv run --offline --no-sync pytest tests/test_api_startup.py tests/test_api.py tests/test_strategies.py -q --tb=short`

## Alertas y limitación existente

Las alertas tempranas traducen los riesgos existentes: `alto → alta`,
`medio → media`, `desconocido → desconocida`; `bajo` no abre alerta.
Desconocido significa falta de datos, no certeza de quiebre o exceso.
Se conservan motivo, SKU, sucursal y la fecha de corte de la configuración
existente de reabastecimiento. Un SKU puede tener dos alertas distintas.
No hay registro de cierres: `abierta` significa riesgo no bajo en ese corte;
el resumen cuenta estas alertas, no todas las anomalías históricas.

Limitación reportada sin corregir: `_unit_margin` en reabastecimiento selecciona
precio por SKU sin distinguir sucursal. En la vista global, precios distintos
pueden afectar la priorización con presupuesto. La vista filtrada solo entrega
precios de su sucursal. No se cambió esa lógica de negocio.

## Asistente y tendencias

El POST del asistente reutiliza `answer_question` y las herramientas FastMCP
en memoria, incluida su validación, transacción de solo lectura, timeout,
reintento único y explicación verificada. PostgreSQL y Gemini se abren bajo
demanda y se cierran al terminar; usa las variables de entorno ya existentes.
Las consultas del asistente siguen usando tablas crudas, con la advertencia
existente de posibles diferencias respecto del dashboard limpio.

Con filtro, un adaptador del backend ofrece solo tablas autorizadas con
`store_id`. Valida el SQL, envuelve cada tabla física en una subconsulta filtrada
mediante AST y delega al servicio original para revalidar y ejecutar. El modelo
no puede eliminar el filtro ni ampliarlo con condiciones. CTE y subconsultas
también quedan restringidas. Las consultas a maestros sin `store_id` se rechazan
en este modo; no se inventan relaciones de sucursal para esos maestros.

Tendencias usa ahora el flujo documentado en [strategies.md](strategies.md):
mapeo explícito, hechos internos por categoría y propuestas de Gemini creativo
posvalidadas. Conserva `503 TENDENCIAS_NO_DISPONIBLES` si falta la llave o falla
la fuente. El mapeo inicial vacío exige configurar relaciones aprobadas antes
de generar propuestas; no se presentan anomalías como tendencias.

## Errores y pruebas

Formato: `{"error":{"codigo":"...","mensaje":"..."}}`.
404 para sucursal inexistente; 422 para entrada inválida o consulta insegura;
503 para datos/reportes/asistente/tendencias no disponibles; 500 genérico para
fallos inesperados. No se devuelven excepciones, trazas, llaves, URLs de conexión,
cuerpo inválido ni configuración interna.

`uv run --offline --no-sync pytest tests/test_api.py -q --tb=short`

Pruebas HTTP en memoria con fixtures ficticias, motor de negocio existente y
Gemini/base simulados: vista global/sucursal, agregados, nulls, severidad, búsqueda,
paginación, errores redactados, bloqueo de SQL destructivo sin ejecución y filtro
SQL impuesto en tablas, CTE y subconsultas. No se probaron servicios reales.
