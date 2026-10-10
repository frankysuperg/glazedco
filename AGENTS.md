# Reglas del proyecto

## Negocio
- Retail con sucursales físicas.
- El dashboard inicia en **Todas las sucursales** y permite filtrar por sucursal.
- Margen bruto = ingresos por ventas − COGS; porcentaje de margen = margen bruto / ingresos × 100. Mantén monto y porcentaje separados; con ingresos de 0, el porcentaje es nulo.

## Stack
- Python con `uv`, pandas, scikit-learn (Prophet solo si aporta), FastAPI, PostgreSQL, Gemini, FastMCP y TrendsAPI.

## Desarrollo y datos
- Antes de cualquier tarea con datos, lee [docs/dataset_context.md](docs/dataset_context.md); si contradice una definición de este archivo, gana dataset_context.md.
- La base es PostgreSQL en Tiger Data, ya poblada; ningún agente modifica su esquema ni sus datos.
- Haz el cambio mínimo solicitado; usa módulos pequeños, tipado y pruebas pytest.
- Mantén secretos en variables de entorno y `.env.example` sin valores reales.
- Nunca inventes datos, métricas, resultados SQL ni fuentes.
- Valida modelos temporalmente para evitar data leakage. Distingue ventas observadas de demanda real insatisfecha. No declares tendencia a partir de una anomalía sin demostrarlo.
- El asistente SQL solo ejecuta consultas `SELECT`.
- Gemini: temperatura cercana a 0 para análisis, explicaciones numéricas y generación SQL; 0.8–1.0 solo para estrategias comerciales creativas basadas en hechos verificados.

## Ahorro de tokens y cierre
- Lee solo archivos necesarios y no imprimas datasets completos: usa `head`, `describe` o conteos. No reescribas archivos enteros para cambios pequeños.
- Cierra cada tarea en un máximo de 10 líneas e indica archivos creados o modificados, comando exacto de verificación, resultado de pruebas y riesgos pendientes.
