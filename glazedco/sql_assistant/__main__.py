"""Sin argumentos: MCP stdio. --question: flujo de negocio mediante MCP en memoria."""

import argparse
import asyncio
import sys

from fastmcp import Client

from glazedco.gemini import GeminiClient
from .assistant import answer_question
from .server import create_server


async def _answer(question: str) -> str:
    with GeminiClient() as gemini:
        async with Client(create_server()) as client:
            return await answer_question(question, gemini=gemini, client=client)


def main() -> int:
    parser = argparse.ArgumentParser(description="Asistente SQL de solo lectura")
    parser.add_argument("--question", help="Pregunta de negocio; omitir para MCP stdio")
    args = parser.parse_args()
    try:
        if args.question is not None:
            print(asyncio.run(_answer(args.question)))
        else:
            create_server().run(transport="stdio", show_banner=False)
        return 0
    except Exception:
        print("No se pudo iniciar el asistente; revisa la configuración local.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
