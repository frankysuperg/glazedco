"""Registro local FastMCP; configuración y credenciales solo al invocar."""

from fastmcp import FastMCP

from .client import TrendsClient
from .config import load_config
from .models import HashtagResult, SocialError


def register_social_tools(server: FastMCP, client: TrendsClient | None = None) -> None:
    @server.tool(annotations={"readOnlyHint": True, "destructiveHint": False, "openWorldHint": True})
    def get_trending_hashtags(categoria: str | None = None, palabra_clave: str | None = None,
                             region: str | None = None, limite: int = 10) -> HashtagResult:
        """Hashtags de TikTok. Categoría del proveedor; palabra clave filtra el ranking localmente.

        Región no documentada: omitirla o se devuelve error. Métricas ausentes son null.
        Nunca interpretar un error como ausencia de tendencias. Cada consulta puede gastar cuota.
        """
        try:
            social = client if client is not None else TrendsClient(load_config())
            return social.get_trending_hashtags(categoria, palabra_clave, region, limite)
        except Exception:
            return HashtagResult(error=SocialError(codigo="CONFIGURACION_SOCIAL", mensaje="No se pudo cargar la configuración social."))
