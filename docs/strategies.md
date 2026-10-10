# Estrategias con evidencia

`POST /api/tendencias/estrategias` conserva el cuerpo `{"objetivo": "..."}` y
el filtro opcional `sucursal`; omitirlo usa Todas las sucursales. El texto libre
de objetivo se conserva por compatibilidad, pero no se pasa a Gemini: en esta
versión las propuestas se basan exclusivamente en el JSON de hechos verificados.

## Configuración obligatoria de relaciones

Editar `glazedco/strategies/mapping.toml`: cada sección `[hashtags."#etiqueta"]`
declara `categories` con nombres exactos del catálogo y/o `skus` con códigos
exactos. Un SKU resuelve su categoría completa, no únicamente sus ventas.
La relación es una decisión explícita de negocio; el modelo nunca la deduce.
El archivo se entrega vacío porque no hay relaciones aprobadas suministradas.
Con la fuente disponible y sin coincidencias se devuelve 200, lista de estrategias
vacía y los hashtags en `sin_relacion_con_catalogo`, sin llamar a Gemini.
Una categoría o SKU configurado que no existe produce 503 de evidencia.

## Dos pasos

El cliente compartido con la herramienta FastMCP aporta el ranking externo,
su fecha y fuente, con su caché y límites existentes. Se conserva el orden
original incluso al omitir hashtags. No se usan volumen ni crecimiento.
Se requiere TRENDSAPI_KEY; llave ausente o fuente fallida produce
503 TENDENCIAS_NO_DISPONIBLES con mensaje redactado y sin ejemplos.

El paso determinista filtra sucursal y categorías antes de agregar:
- Ventana inclusiva de `weeks * 7` días hasta `ReplenishmentConfig.as_of_date`
  (cuatro semanas por defecto). Margen total y semanal reutilizan aggregate_margin,
  con costos estimados, exclusiones, cobertura y porcentajes nulos existentes.
- Inventario observado exactamente al corte: existencias + en pedido, sin
  interpretar ausencias como cero. Se informa el número de registros; el total
  no afirma cobertura completa ni inventario actual.
- Riesgos y recomendaciones vigentes al corte reutilizan Dashboard.recommendations;
  el pronóstico publicado posterior al corte se suma por semana para las series
  disponibles. No se entrena ni se modifica el pronóstico.

Gemini recibe solo ese JSON con el perfil creative (temperatura 0.9). Las
propuestas citan rutas JSON Pointer del hecho usado y explican cualitativamente
su posible efecto sobre el margen. La señal externa siempre es una hipótesis:
no hay historial para correlaciones ni evidencia para introducirla al pronóstico.
Los datos internos son sintéticos para hackathon, no ventas reales actuales.

La posvalidación descarta individualmente propuestas con esquema incorrecto,
índices inexistentes, referencias inválidas/nulas, hashtags ajenos o cifras
no presentes en los campos numéricos citados. Exige referencias al hashtag y
al margen; no permite cifras en el efecto futuro. Fechas completas se admiten
cuando existen en el hecho, pero sus componentes no autorizan cifras monetarias.
La respuesta incluye hechos, evidencia resuelta y conteo de descartes. JSON
inválido se descarta; errores de Gemini producen 503 ESTRATEGIAS_NO_DISPONIBLES.
Esta comprobación de pertenencia no demuestra causalidad ni garantiza resultados
comerciales; las propuestas requieren evaluación antes de ejecutarse.

### Diagnóstico y reintento (prompt creativo v2)

`descartes` es una lista de `{ "indice": 0, "motivo": "referencia_inexistente" }`.
El índice parte de cero dentro de la lista del intento; es `null` si el JSON o
su envoltura es inválido. Se informa el primer fallo por propuesta, en orden:
`esquema_invalido`, `referencia_inexistente` (incluye índice de hecho inexistente),
`referencia_nula`, `hashtag_no_citado`, `margen_no_citado`, `hashtag_ajeno`,
`cifra_en_efecto`, `cifra_sin_origen`. Los códigos son estables y se imprimen
también en el log del servidor, sin texto del modelo, valores ni excepciones.
Los descartes se acumulan en orden de intento; un índice puede repetirse entre
intentos. `estrategias_descartadas` siempre es la longitud de `descartes`.

Si no hay propuestas válidas o no cubren los hashtags relacionados (hasta cinco),
se regenera la lista completa una sola vez con el perfil creative. Se reutilizan
los mismos hechos y se añaden únicamente códigos de rechazo y una instrucción
fija como retroalimentación, sin cifras ni texto de propuestas rechazadas.
Una lista vacía o cobertura incompleta sin rechazos produce códigos vacíos.
Se publica únicamente la lista posvalidada del último intento: si todas sus
propuestas fallan, queda vacía con los descartes acumulados. Máximo dos llamadas
a Gemini y una consulta al cliente TrendsAPI; sin mapeo no se llama a Gemini.
Un error del proveedor en cualquiera de los intentos conserva el 503 existente.

El prompt versionado `glazedco/gemini/prompts/creative.txt` pide una propuesta
por hashtag, máximo cinco, citando a la vez hashtag y margen mediante rutas
como `/hechos_tendencias/0/hashtag` y
`/hechos_tendencias/0/margen/margen_bruto_porcentaje`. El validador resuelve esas
rutas exclusivamente dentro del hecho indicado; conserva las rutas relativas
anteriores por compatibilidad. No cambia los criterios de evidencia ni cifras.
El efecto esperado no admite dígitos. El ejemplo ficticio del prompt solo
enseña formato: no se agrega a los hechos ni se publica como respaldo.
El antiguo contador ocultaba la causa de rechazo; sin la respuesta original
no puede atribuirse el caso observado a una regla concreta.

Verificación local sin servicios:
`uv run --offline --no-sync pytest tests/test_strategies.py tests/test_api.py tests/test_gemini.py tests/test_social.py -q --tb=short`
