# Hashtags TikTok mediante TrendsAPI

Documentación oficial revisada el 2026-10-10:
[REST](https://trendsapi.ai/docs), [TikTok](https://trendsapi.ai/trends/tiktok-trends),
[FastMCP/Gemini](https://gofastmcp.com/integrations/gemini) y
[Google Gen AI SDK](https://googleapis.github.io/python-genai/).
El enlace oficial a OpenAPI no fue accesible con el lector web; se usó la referencia REST.

## Contrato verificado y límites

TrendsAPI documenta `POST https://api.trendsapi.ai/api`, autenticación Bearer,
`mode=get_top_trends`, feeds `TikTok Trending Hashtags` y
`TikTok Trending Hashtags by Category`, `category` y `limit` (máximo 200).
La respuesta de ranking documentada contiene `type` y `data: [[rank,label],...]`.
Los errores pueden venir en HTTP 200 con `statusCode` y `body` como JSON serializado.

No está documentado el parámetro región: se rechaza sin llamada externa.
Tampoco se documentan volumen/crecimiento en ese ranking: ambos son null.
No se convierten posiciones ni puntuaciones normalizadas en volumen.
Los nombres de categorías aceptadas quedan por confirmar con el proveedor;
se envía el texto solicitado sin traducirlo ni inventar códigos.
La palabra clave se filtra localmente sobre hasta 200 elementos solicitados,
con cobertura real dependiente del plan; no es búsqueda exhaustiva.
La referencia indica que el plan gratuito puede devolver solo diez elementos.

## Configuración e interfaz

En `.env`, definir `TRENDSAPI_KEY`; `.env.example` no contiene valores reales.
`social/config.py` contiene el comentario TODO solicitado junto a la lectura
de la llave. Nunca se imprime ni se guarda en caché la credencial.

`create_server()` registra `get_trending_hashtags(categoria=None,
palabra_clave=None, region=None, limite=10)` junto a las herramientas SQL.
No cambia el flujo SQL ni el endpoint HTTP de estrategias existente.
La salida Pydantic incluye `items`, `error`, `cache` y `aviso`; cada elemento
contiene `hashtag`, `volumen`, `crecimiento`, `fecha_consulta` UTC y `fuente`.
Una lista vacía con `error=null` es un resultado vacío; un error nunca significa
que no existan tendencias. La fecha de caché conserva la consulta original.

## Control de llamadas

El cliente usa HTTPX: timeout de 20 segundos por operación de transporte,
sin redirecciones, hasta dos reintentos por fallos de red/429/5xx seleccionados.
Espera exponencial y respeto a `Retry-After` numérico o fecha HTTP. Si la espera
excede treinta segundos, devuelve error y persiste la pausa cuando hay 429.
No reintenta errores de autenticación, parámetros ni formato desconocido.

SQLite local en `data/social/trends.sqlite3` (ignorado por Git): TTL de quince
minutos, pausa mínima entre llamadas y presupuesto de cien intentos por mes UTC.
Son controles locales configurables en `SocialConfig`, no límites de velocidad
atribuidos al proveedor. Por prudencia, los intentos fallidos también consumen
presupuesto local. Una reserva transaccional protege llamadas concurrentes;
las consultas en caché no consumen presupuesto. La clave de cuenta es un hash.
Estos controles solo comparten estado entre procesos que usen el mismo archivo;
otras aplicaciones/dispositivos pueden consumir cuota adicional del proveedor.
La caché solo guarda resultados normalizados satisfactorios y elimina vencidos.

## Function calling en Gemini

FastMCP expone las herramientas mediante `list_tools`. Google Gen AI admite
pasar la sesión MCP conectada en `GenerateContentConfig(tools=[session])` y
ejecuta automáticamente las funciones elegidas por el modelo. La integración
MCP del SDK se documenta como experimental. `generate_with_mcp` utiliza esta
vía con perfil `analysis` a temperatura cero y máximo de tres llamadas remotas
del SDK. `creative` conserva 0.9; no se cambia ningún perfil existente.

Ejemplo de uso real (sí consume servicios al ejecutarse):

```python
from fastmcp import Client
from glazedco.gemini import GeminiClient
from glazedco.sql_assistant.server import create_server

async def consultar():
    async with GeminiClient() as gemini, Client(create_server()) as mcp:
        return await gemini.generate_with_mcp(
            "Consulta los hashtags de TikTok; no inventes métricas ausentes.",
            session=mcp.session,
        )
```

La sesión expone también las herramientas SQL existentes y conserva sus
validaciones. El servidor existente carga PostgreSQL en su lifespan; requiere
su configuración habitual. `register_social_tools` puede registrar la misma
herramienta en un servidor de pruebas sin base de datos. La nueva ruta de
function calling no sustituye la explicación numérica verificada del asistente
SQL; la prosa del modelo sigue requiriendo revisión.

## Verificación

`uv run --offline --no-sync pytest tests/test_social.py tests/test_sql_assistant.py tests/test_gemini.py -q --tb=short`

MockTransport simula respuestas basadas en el contrato documentado, no capturas
de TrendsAPI. Se prueban normalización, nulos, filtros, TTL persistente, llave
ausente, red, errores encapsulados, Retry-After, presupuesto, registro MCP y
SDK Gemini simulado invocando la sesión real en memoria. Ninguna prueba consulta
TrendsAPI, Gemini ni PostgreSQL reales. Falta verificar la integración autenticada
y confirmar región, categorías y métricas adicionales con documentación del proveedor.
