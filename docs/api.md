# Backend del dashboard

FastAPI expone los módulos existentes, sin frontend ni cambios de reglas de
negocio. Se consultó la documentación vigente de
[modelos de respuesta](https://fastapi.tiangolo.com/tutorial/response-model/) y
[errores](https://fastapi.tiangolo.com/tutorial/handling-errors/) antes de implementar.

Arranque local: `uv run uvicorn glazedco.api.app:app --host 127.0.0.1 --port 8000`.
OpenAPI en `/openapi.json`, documentación interactiva en `/docs`.
La aplicación no consulta PostgreSQL ni Gemini al importarse.

## Rutas

Todas admiten `?sucursal=S001`; omitir el parámetro devuelve
`sucursal: null, vista: "Todas las sucursales"`. Una sucursal desconocida da 404.
Los identificadores se toman del catálogo limpio, no se interpretan como números.

| Método | Ruta | Respuesta |
|---|---|---|
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

El adaptador reutiliza `forecast.load_clean`: caché Parquet existente en
`data/clean` o lectura SELECT y limpieza existentes. Carga los CSV
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

Los datos y resultados se guardan en memoria al primer uso, con bloqueo para
evitar cargas duplicadas; reiniciar el proceso después de publicar nuevos
archivos. El despliegue debe usar caché y reportes del mismo corte. No existe
un manifiesto común que permita garantizar automáticamente esa consistencia.

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
