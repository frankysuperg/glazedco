"""Contrato del SDK con dobles: ninguna prueba accede a Gemini ni a .env."""

import json
import socket
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from google.genai import errors, types

from glazedco.gemini import GeminiClient, GeminiConfig, GeminiConfigError, GeminiError, explain_metrics, load_config
from glazedco.gemini import client as client_module
from glazedco.gemini import config as config_module


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Estas pruebas no permiten acceso a la red")
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    monkeypatch.setattr(config_module, "load_dotenv", Mock())
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_MODEL", raising=False)


@pytest.fixture
def sdk():
    fake = Mock()
    fake.models.generate_content.return_value = SimpleNamespace(text="Explicación simulada")
    return fake


@pytest.mark.parametrize("profile,temperature", [("analysis", 0.0), ("sql", 0.0), ("creative", 0.9)])
def test_perfiles_envian_parametros_oficiales_y_politica(profile, temperature, sdk):
    client = GeminiClient(GeminiConfig("llave-ficticia", "modelo-ficticio"), sdk_client=sdk)
    assert client.generate("Datos suministrados", profile=profile) == "Explicación simulada"
    kwargs = sdk.models.generate_content.call_args.kwargs
    assert kwargs["model"] == "modelo-ficticio"
    assert kwargs["contents"] == "Datos suministrados"
    assert isinstance(kwargs["config"], types.GenerateContentConfig)
    assert kwargs["config"].temperature == temperature
    prompt = kwargs["config"].system_instruction
    for text in ("solo los datos entregados", "no tengo ese dato", "Nunca inventes métricas, resultados de consultas ni fuentes"):
        assert text in prompt
    assert kwargs["config"].tools is None


@pytest.mark.parametrize("key", [None, "", "   "])
def test_falta_llave_falla_antes_de_construir_sdk(monkeypatch, key):
    if key is not None:
        monkeypatch.setenv("GEMINI_API_KEY", key)
    monkeypatch.setenv("GEMINI_MODEL", "modelo-ficticio")
    constructor = Mock()
    monkeypatch.setattr(client_module.genai, "Client", constructor)
    with pytest.raises(GeminiConfigError, match=r"^Falta GEMINI_API_KEY en el entorno o en .env\.$"):
        GeminiClient()
    constructor.assert_not_called()


def test_falta_modelo_no_inventa_uno(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "llave-ficticia")
    with pytest.raises(GeminiConfigError, match="Falta GEMINI_MODEL"):
        load_config()


def test_entorno_dotenv_y_llave_explicita(monkeypatch, sdk):
    monkeypatch.setenv("GEMINI_API_KEY", "llave-ficticia")
    monkeypatch.setenv("GEMINI_MODEL", "modelo-ficticio")
    monkeypatch.setenv("GOOGLE_API_KEY", "otra-llave-ficticia")
    constructor = Mock(return_value=sdk)
    monkeypatch.setattr(client_module.genai, "Client", constructor)
    with GeminiClient() as client:
        assert client.config.model == "modelo-ficticio"
        assert "llave-ficticia" not in repr(client.config)
    constructor.assert_called_once_with(api_key="llave-ficticia")
    assert config_module.load_dotenv.call_args.kwargs["override"] is False
    sdk.close.assert_called_once()


def test_perfil_desconocido_no_envia_peticion(sdk):
    client = GeminiClient(GeminiConfig("llave-ficticia", "modelo-ficticio"), sdk_client=sdk)
    with pytest.raises(ValueError, match="Perfil no admitido"):
        client.generate("texto", profile="../otro")
    sdk.models.generate_content.assert_not_called()


def test_explicacion_envia_solo_metricas_y_no_recalcula(sdk):
    metrics = {"ingresos_USD": 100.0, "margen_USD": 35.0, "margen_pct": 35.0, "dato_faltante": None}
    before = metrics.copy()
    sdk.models.generate_content.return_value.text = "Ingresos [ingresos_USD: 100.0]; margen [margen_USD: 35.0]."
    client = GeminiClient(GeminiConfig("llave-ficticia", "modelo-ficticio"), sdk_client=sdk)
    result = explain_metrics(metrics, client=client)
    kwargs = sdk.models.generate_content.call_args.kwargs
    assert json.loads(kwargs["contents"]) == {"metricas": metrics}
    assert kwargs["config"].temperature == 0
    assert "[nombre: valor]" in kwargs["config"].system_instruction
    assert "No recalcules" in kwargs["config"].system_instruction
    assert result == sdk.models.generate_content.return_value.text
    assert metrics == before


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_explicacion_rechaza_no_finitos_sin_enviar(value, sdk):
    client = GeminiClient(GeminiConfig("llave-ficticia", "modelo-ficticio"), sdk_client=sdk)
    with pytest.raises(ValueError):
        explain_metrics({"valor": value}, client=client)
    sdk.models.generate_content.assert_not_called()


@pytest.mark.parametrize("text", [None, "", "   "])
def test_respuesta_vacia_falla_claramente(text, sdk):
    sdk.models.generate_content.return_value.text = text
    client = GeminiClient(GeminiConfig("llave-ficticia", "modelo-ficticio"), sdk_client=sdk)
    with pytest.raises(GeminiError, match="no devolvió texto"):
        client.generate("texto")


def test_error_del_sdk_no_expone_contenido_ni_llave(sdk):
    sdk.models.generate_content.side_effect = errors.APIError(400, {"error": {"message": "llave-ficticia: contenido privado"}})
    client = GeminiClient(GeminiConfig("llave-ficticia", "modelo-ficticio"), sdk_client=sdk)
    with pytest.raises(GeminiError) as caught:
        client.generate("texto")
    assert "llave-ficticia" not in str(caught.value)
    assert "contenido privado" not in str(caught.value)
