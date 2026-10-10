# Backend del dashboard

FastAPI expone los módulos existentes y un dashboard HTML autocontenido, sin
cambios de reglas de negocio. Se consultó la documentación vigente de
[modelos de respuesta](https://fastapi.tiangolo.com/tutorial/response-model/) y
[errores](https://fastapi.tiangolo.com/tutorial/handling-errors/) antes de implementar.

Arranque local: `uv run uvicorn glazedco.api.app:app --host 127.0.0.1 --port 8000`.
OpenAPI en `/openapi.json`, documentación interactiva en `/docs`.
La aplicación no consulta PostgreSQL ni Gemini al importarse.

## Dashboard en la raíz

`GET /` sirve `glazedco/api/static/index.html` mediante FileResponse, incluso
durante el precalentamiento. CSS, JavaScript y SVG propios viven en ese archivo;
no usa CORS, CDN, dependencias de frontend, fuentes ni imágenes remotas. Las
rutas `/api/*` conservan sus contratos. El HTML consulta `/api/estado` y aplica
un plazo de cinco minutos por tarjeta, con reintento independiente. Al cambiar
de sucursal cancela las peticiones anteriores y recarga todos los indicadores.

Se verificaron con TestClient los contratos Pydantic de estado, resumen,
margen, pronóstico semanal, alertas, reabastecimiento, anomalías y sucursales,
con datos de prueba globales y filtrados. Las listas se paginan hasta completar
`total`, sin asumir una cantidad fija de sucursales ni limitar silenciosamente
alertas o recomendaciones. Las capturas de esos contratos para verificación
local viven en `data/verification/`; no se incluyen como datos del HTML.

El margen muestra `margen_bruto_porcentaje`, `margen_bruto_monto` y
`cobertura_ingresos_pct` (cobertura de ingresos con costo, no de productos).
El riesgo cuenta alertas `quiebre_stock` con severidad `alta`; los pedidos de
hoy suman cantidades positivas cuya `fecha_pedido` coincide con la fecha local
del navegador. Se advierte que el plan pertenece al corte histórico y no
confirma una necesidad actual. Las recomendaciones del detalle se relacionan
por SKU y sucursal. Ningún importe USD se estima en el navegador.

El pronóstico muestra hasta las primeras cuatro semanas publicadas, ordenadas
por fecha, y suma sus unidades únicamente si hay cuatro valores conocidos.
Si faltan semanas o hay nulos, el total de cuatro semanas dice «No disponible».
Cada barra SVG procede de una semana devuelta por el endpoint. El HTML consume
`comparacion` para las cuatro semanas anteriores y la variación; los nulos se
muestran como «No disponible». La gráfica grande usa las semanas observadas
completas y la proyección publicada, con tabla de valores exactos. Los huecos
no se unen ni se interpolan como registros. No hay banda de incertidumbre en
este contrato y no se construye una global.

Las prioridades se ordenan por impacto conocido descendente y luego por
severidad. Cada impacto muestra importe, método y «Estimación». El KPI de
quiebre suma exclusivamente impactos de quiebre conocidos y marca el total
como parcial si faltan importes; no mezcla capital inmovilizado con margen.
Las anomalías muestran severidad y duración publicadas; no las recalcula el HTML.
Se presentan tres filas iniciales y un botón «Ver más», sin truncar textos.
El panel lateral usa diálogo modal, foco atrapado, cierre por botón/Esc/velo
y devolución del foco; las tarjetas responden a clic, Enter y Espacio.

Siguen ausentes del contrato las alertas de caducidad, el punto de reorden y
las bandas de incertidumbre; se muestran como «No disponible».
Tampoco se inventan acciones específicas para margen o
pronóstico ni pedidos para una alerta sin recomendación vinculada. El pronóstico
se muestra en unidades; la API no entrega su equivalente en USD.

Reabastecimiento muestra hasta cinco recomendaciones positivas, por fecha de
pedido ascendente y luego riesgo de quiebre; distingue fechas alcanzadas y
futuras sin afirmar que el corte histórico represente necesidades actuales.
El detalle incluye stock de seguridad, fecha, cantidad y motivo publicados.
Desempeño consulta `/api/margen` por sucursal (hasta cuatro solicitudes
simultáneas), ordena por monto descendente y permite seleccionar la sucursal.
Con un filtro activo muestra solo esa sucursal. Las barras negativas se
identifican con signo y texto; su longitud representa la magnitud absoluta.

Tendencias se consulta exclusivamente al pulsar «Ver tendencias», mediante
POST con `objetivo` fijo; el servicio actual no utiliza ese campo. Se muestran
hashtag, categoría del hecho referenciado, propuesta, efecto esperado y
`evidencia_verificada`, siempre como hipótesis. Los hashtags no mapeados se
listan aparte. El asistente envía `pregunta` solo al enviar el formulario y
muestra `respuesta` o el error explícito. Ambos POST llevan el filtro activo,
no se reintentan automáticamente ni se ejecutan al cargar. Al cambiar de
sucursal se cancelan solicitudes pendientes y se descartan resultados previos.
El chat aparece sobre el detalle y devuelve el foco al cerrarlo con Esc.
El banner de arranque consulta estado cada cinco segundos hasta disponibilidad
o fallo; los bloques conservan sus tiempos de espera y reintentos independientes.

Verificación: `uv run --offline --no-sync pytest tests/test_dashboard_html.py tests/test_api.py tests/test_api_startup.py -q --tb=short`.
Además se comprobó en Edge sin interfaz a 1366×768, 1280×720, 1920×1080 y
390×844, interceptando solicitudes con respuestas de TestClient: alcance,
reintento independiente, carga inicial, teclado, retorno de foco, velo, panel
móvil, POST bajo demanda, errores 422/503, gráfico sin datos observados y
campos nulos. No hubo servicios reales ni servidor de red. El script inline
extraído pasó `node --check data/verification/dashboard-script.js`.

## Arranque y disponibilidad

El [lifespan de FastAPI](https://fastapi.tiangolo.com/advanced/events/) lanza
el precalentamiento en un trabajador en segundo plano y cede inmediatamente
el control al servidor. `GET /api/estado` devuelve `{"listo": false, "detalle": "..."}`
mientras trabaja y `listo: true` únicamente cuando se completaron todos los
cálculos. Salvo `/` y `/api/estado`, las rutas devuelven 503 sin esperar al trabajador, con
`error.codigo: API_NO_LISTA`, un mensaje seguro, `reintentar_despues: 5` y
la cabecera `Retry-After: 5`. El fallo de preparación permanece visible en
el estado; corregir su causa y reiniciar. No se publican excepciones privadas.

El estado incluye también `tipo_error`: `null` mientras prepara o cuando está
listo, y el nombre de la excepción original si falla (por ejemplo,
`QueryCanceled`, no solamente el wrapper `APIError`). El traceback completo
de frames y causas se imprime en stderr del servidor, incluso los contextos
ocultos por `from None`. Se redactan URLs, DSN, credenciales en asignaciones,
tokens de autorización y valores secretos del entorno; se omiten variables
locales y líneas de código fuente para no mostrar literales secretos. Los
mensajes privados del traceback no forman parte de la respuesta HTTP.

La carga masiva heredaba `statement_timeout=15000` del pool. Ahora `read_tables`
aplica `SET LOCAL statement_timeout` antes de los cursores, dentro de la misma
transacción READ ONLY con rollback. `SQL_LOAD_TIMEOUT_MS` vale 300000 por
defecto y admite enteros de 1 a 2147483647 ms. Solo se usa para cargas masivas
(API y comandos que reutilizan `read_tables`); no cambia las consultas, la
limpieza, los cálculos, los tiempos de conexión ni el pool general. El asistente
mantiene su política de 5000 ms y su propio SET LOCAL. Según
[PostgreSQL](https://www.postgresql.org/docs/current/runtime-config-client.html#GUC-STATEMENT-TIMEOUT),
el límite se aplica por sentencia ejecutada en el servidor, no a toda la
duración del precalentamiento. Un fallo a los 43 s no demuestra por sí solo
que la causa sea ese timeout: el diagnóstico permite comprobar la excepción.

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

## Comparación, impacto y severidad adicionales

`GET /api/pronostico/semanal` añade `comparacion`; el mismo objeto aparece en
`pronostico.comparacion` del resumen. Los campos anteriores conservan sus cifras.
`fecha_corte` procede de la política de reabastecimiento (2026-09-30).
`semanas_anteriores` contiene cuatro semanas completas de lunes a domingo:
2026-08-31 a 2026-09-27; excluye la semana parcial del corte. Cada elemento
entrega `semana`, `fin`, `unidades`, `registros` y `dias_con_registros`.
Son unidades vendidas observadas, no demanda insatisfecha; la cobertura informa
cuántos días tienen registros, sin rellenar días ausentes con ventas inventadas.

`unidades_reales_4_semanas` suma esas semanas;
`unidades_pronosticadas_4_semanas` suma las cuatro semanas desde el inicio
configurado del pronóstico (2026-10-05). `variacion_porcentual` es
`(pronosticadas − reales) / reales × 100`. Si el denominador es cero, falta una
semana observada, hay unidades inválidas o faltan semanas pronosticadas, la
variación es nula y `motivo` explica la causa. Una semana observada sin registros
es desconocida, no cero. Todos estos valores respetan el filtro de sucursal.

Cada elemento de `GET /api/alertas` añade el objeto `impacto` con
`impacto_usd_estimado`, `metodo`, `estimacion: true` y `motivo` (nulo cuando se
puede calcular). El `motivo` original de la alerta se conserva por separado.
Campos auxiliares: `dias_en_riesgo`, `unidades_en_riesgo`, `unidades_exceso` y
`margen_unitario`, nulos cuando no corresponden o faltan datos.

- Quiebre: demanda pronosticada sin cobertura hasta la reposición × margen
  bruto unitario. La demanda diaria reutiliza el reparto semanal existente;
  se consume la posición de inventario hasta agotarla y se incluyen fracciones
  de día. Margen unitario = precio medio ponderado (`Σ net_sales / Σ units_sold`)
  del SKU en esa sucursal durante los últimos 28 días inclusivos al corte,
  menos `products.unit_cost`. El plazo usa órdenes recibidas de esa sucursal
  hasta el corte o el plazo estándar del proveedor; no acepta el respaldo
  genérico configurado. Un margen negativo produce un importe negativo.
- Exceso: `max(posición de inventario − demanda durante cobertura objetivo, 0)
  × unit_cost`. Es **capital inmovilizado**, no pérdida de margen. Usa la
  cobertura objetivo existente, limitada por vida útil para perecederos;
  no incluye stock de seguridad en este umbral monetario.

La posición es existencias más unidades en tránsito del corte, como en la
política existente; no garantiza sus fechas de llegada. No se extrapola un
horizonte pronosticado insuficiente. Costo, precio, inventario, plazo o
pronóstico ausentes/ambiguos dejan el impacto nulo con motivo explícito;
no se sustituye por cero. Los impactos siempre se calculan por SKU y sucursal,
también en la vista global. Son estimaciones y no deben sumarse como si capital
inmovilizado y margen en riesgo representaran el mismo concepto.

Las alertas con impacto conocido se ordenan primero por importe descendente;
las que no lo tienen van después por severidad (alta, media, desconocida).
Los desempates son deterministas por sucursal, SKU y tipo. Se ordena antes de
paginar; no se cambia qué riesgos abren alertas ni sus conteos.

`GET /api/anomalias` añade `severidad` alta/media/baja según el valor absoluto
del puntaje publicado: alta desde 5, media desde 3, baja por debajo de 3.
Los umbrales inclusivos `anomaly_high` y `anomaly_medium` y la ventana de precio
`price_days` se configuran en `glazedco/api/enrichment.toml`; requieren reinicio.
Un puntaje no finito o ausente deja severidad nula con `motivo_severidad`.
`duracion_dias` es la longitud de la racha de fechas consecutivas por sucursal,
SKU, entidad, métrica y tipo; un día aislado vale 1, duplicados no suman y
fechas inválidas devuelven nulo. Se calcula antes de paginar. Describe el
historial publicado, sin inferir persistencia futura ni una tendencia.

Verificación sin servicios reales:
`uv run --offline --no-sync pytest tests/test_api_enrichment.py tests/test_api.py tests/test_api_startup.py tests/test_dashboard_html.py tests/test_strategies.py -q --tb=short`.

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

La respuesta 200 de `/api/tendencias/estrategias` incluye `descartes`, una lista
de `{ "indice": 0, "motivo": "cifra_sin_origen" }`, vacía si no hubo rechazos.
`indice` es la posición desde cero en la lista de cada intento, o `null` si el
JSON/envoltura es inválido. Se acumulan los descartes de ambos intentos en orden
(los índices pueden repetirse); `estrategias_descartadas` es su longitud.
Solo se expone el primer código de rechazo por propuesta: `esquema_invalido`,
`referencia_inexistente`, `referencia_nula`, `hashtag_no_citado`, `margen_no_citado`,
`hashtag_ajeno`, `cifra_en_efecto` o `cifra_sin_origen`. Los mismos códigos se
registran en consola sin contenido del modelo ni datos sensibles.
Si falta cobertura de hashtags relacionados (máximo cinco) o todas las
propuestas fallan, el servidor hace un único reintento creative con los mismos
hechos y códigos como retroalimentación; no vuelve a consultar TrendsAPI.
`estrategias` contiene solo propuestas que pasan todas las reglas en el último
intento; puede seguir vacía. Los errores de Gemini conservan el 503
`ESTRATEGIAS_NO_DISPONIBLES`. Detalles en [strategies.md](strategies.md).

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
