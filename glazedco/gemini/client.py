"""Adaptador del SDK oficial google-genai con perfiles y puente MCP opcional."""

import json
import logging
from pathlib import Path
from typing import Literal, Self

from google import genai
from google.genai import errors, types
from mcp import ClientSession

from .config import GeminiConfig, load_config

Profile = Literal["analysis", "sql", "creative"]
TEMPERATURES: dict[Profile, float] = {"analysis": 0.0, "sql": 0.0, "creative": 0.9}
PROMPTS = Path(__file__).with_name("prompts")
MetricValue = int | float | str | None


class GeminiError(RuntimeError):
    """Fallo de generación sin exponer credenciales ni contenido del proveedor."""


class GeminiClient:
    """Un método y tres perfiles. La construcción no genera contenido.

    sdk_client permite inyectar un doble en pruebas. close() también cierra el
    cliente inyectado; usar el wrapper como context manager libera recursos.
    """

    def __init__(self, config: GeminiConfig | None = None, *, sdk_client: genai.Client | None = None) -> None:
        self.config = config if config is not None else load_config()
        self._sdk = sdk_client if sdk_client is not None else genai.Client(api_key=self.config.api_key)

    def generate(self, contents: str, *, profile: Profile = "analysis",
                 response_schema: dict[str, object] | None = None) -> str:
        if profile not in TEMPERATURES:
            raise ValueError("Perfil no admitido: usa analysis, sql o creative.")
        instruction = (PROMPTS / f"{profile}.txt").read_text(encoding="utf-8")
        config = types.GenerateContentConfig(
            temperature=TEMPERATURES[profile], system_instruction=instruction,
        )
        if response_schema is not None:
            config.response_mime_type = "application/json"
            config.response_json_schema = response_schema
        try:
            response = self._sdk.models.generate_content(
                model=self.config.model, contents=contents,
                config=config,
            )
        except errors.APIError as exc:
            # Nunca registrar message, respuesta, traceback ni credenciales del SDK.
            code = exc.code if isinstance(exc.code, int) else None
            statuses = {"INVALID_ARGUMENT", "UNAUTHENTICATED", "PERMISSION_DENIED", "NOT_FOUND",
                        "RESOURCE_EXHAUSTED", "INTERNAL", "UNAVAILABLE", "DEADLINE_EXCEEDED",
                        "FAILED_PRECONDITION", "UNKNOWN", "CANCELLED", "OUT_OF_RANGE",
                        "UNIMPLEMENTED", "DATA_LOSS", "ALREADY_EXISTS", "ABORTED"}
            status = exc.status if isinstance(exc.status, str) and exc.status in statuses else "UNKNOWN"
            logging.getLogger("uvicorn.error").warning(
                "Gemini APIError tipo=%s code=%s status=%s", type(exc).__name__, code, status)
            raise GeminiError("Gemini rechazó la solicitud; revisa llave, modelo, permisos o cuota.") from None
        if not response.text or not response.text.strip():
            raise GeminiError("Gemini no devolvió texto; la respuesta puede haber sido bloqueada o estar vacía.")
        return response.text.strip()

    def close(self) -> None:
        self._sdk.close()

    async def generate_with_mcp(self, contents: str, *, session: ClientSession,
                                profile: Profile = "analysis") -> str:
        """Function calling automático mediante una sesión MCP ya conectada.

        Expone las herramientas registradas en esa sesión. El llamador conserva
        el contexto de conexión; este método no reemplaza el flujo SQL verificado.
        """
        if profile not in TEMPERATURES:
            raise ValueError("Perfil no admitido.")
        instruction = (PROMPTS / f"{profile}.txt").read_text(encoding="utf-8")
        try:
            response = await self._sdk.aio.models.generate_content(
                model=self.config.model, contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=instruction + "\nUsa las herramientas para obtener datos. "
                    "Comunica sus errores y limitaciones; no inventes métricas ausentes.",
                    temperature=TEMPERATURES[profile], tools=[session],
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(maximum_remote_calls=3),
                ),
            )
        except Exception:
            raise GeminiError("No se pudo completar la consulta con herramientas MCP.") from None
        if not response.text or not response.text.strip():
            raise GeminiError("Gemini no devolvió una respuesta con herramientas MCP.")
        return response.text.strip()

    def __enter__(self) -> Self:
        return self

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        try:
            await self._sdk.aio.aclose()
        finally:
            self.close()

    def __exit__(self, *args: object) -> None:
        self.close()


def explain_metrics(metrics: dict[str, MetricValue], *, client: GeminiClient) -> str:
    """Explica métricas ya calculadas con analysis; no calcula ni consulta datos.

    Las unidades deben estar indicadas en las claves o valores suministrados.
    None significa dato desconocido, no cero. Rechaza NaN/infinito y tipos no
    JSON; no modifica el diccionario ni añade valores derivados.
    """
    if any(not isinstance(key, str) for key in metrics):
        raise ValueError("Los nombres de métricas deben ser texto.")
    payload = json.dumps({"metricas": metrics}, ensure_ascii=False, allow_nan=False)
    return client.generate(payload, profile="analysis")
