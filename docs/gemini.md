# Capa Gemini

SDK oficial: `google-genai`. Se revisó la [documentación oficial de Python](https://googleapis.github.io/python-genai/): `genai.Client(api_key=...)`, `client.models.generate_content(model=..., contents=..., config=types.GenerateContentConfig(system_instruction=..., temperature=...))` y `client.close()`.

`GEMINI_API_KEY` y `GEMINI_MODEL` son obligatorias; no hay modelo por defecto. El cargador usa python-dotenv sin sobrescribir el entorno. No registrar configuración secreta ni contenido de `.env`.

`GeminiClient.generate(texto, profile="analysis")` usa un único envío; perfiles `analysis=0`, `sql=0`, `creative=0.9`. Los prompts versionados están en `glazedco/gemini/prompts/*.txt`. No se conectan herramientas, bases de datos ni ejecución SQL.

`explain_metrics(metricas, client=cliente)` serializa valores ya calculados y pide explicaciones con referencias `[nombre: valor]`. No calcula indicadores. Las unidades deben venir en los datos; null es desconocido. El prompt exige fidelidad, pero no constituye una garantía determinista contra alucinaciones: la salida generada requiere revisión antes de usarla como hecho.

El cliente admite `with GeminiClient() as cliente:` para liberar recursos. Las pruebas inyectan un SDK simulado y bloquean conexiones de sockets; no requieren llave real, modelo real ni acceso a Gemini.

Verificar: `uv run --no-sync pytest tests/test_gemini.py -q`.
