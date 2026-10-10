"""Tracebacks de arranque: todos los frames y causas, sin locales ni secretos."""

import os
import re
from urllib.parse import quote, quote_plus


_SECRET_NAME = re.compile(r"key|token|secret|password|passwd|pwd|dsn|url|conn|credential", re.I)
_URL = re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s\"'<>]+", re.I)
_ASSIGNMENT = re.compile(
    r"(?P<key>[\w.-]*(?:key|token|secret|password|passwd|pwd|dsn|url|conninfo|credential)[\w.-]*"
    r"|host|hostaddr|port|dbname|user|sslmode|options)"
    r"[\"']?\s*[:=]\s*(?:\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|[^\s,;)}]+)", re.I)
_BEARER = re.compile(r"\b(Bearer|Basic)\s+[^\s\"',;]+", re.I)


def redact(text: str) -> str:
    for name, value in sorted(os.environ.items(), key=lambda item: len(item[1]), reverse=True):
        if value and _SECRET_NAME.search(name):
            for variant in {value, quote(value, safe=""), quote_plus(value)}:
                text = text.replace(variant, "[REDACTED]")
    text = _URL.sub("[CONNECTION REDACTED]", text)
    text = _ASSIGNMENT.sub(lambda match: match["key"] + "=[REDACTED]", text)
    return _BEARER.sub(lambda match: match[1] + " [REDACTED]", text)


def exception_chain(exc: BaseException) -> list[BaseException]:
    """Incluye contextos suprimidos por los wrappers públicos con 'from None'."""
    chain: list[BaseException] = []
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        chain.append(exc)
        seen.add(id(exc))
        exc = exc.__cause__ if exc.__cause__ is not None else exc.__context__
    return chain


def root_error_type(exc: BaseException) -> str:
    return type(exception_chain(exc)[-1]).__name__


def sanitized_traceback(exc: BaseException) -> str:
    lines: list[str] = []
    seen: set[int] = set()

    def render(error: BaseException) -> None:
        for item in reversed(exception_chain(error)):
            if id(item) in seen:
                continue
            seen.add(id(item))
            if lines:
                lines.append("\nCadena de excepciones (incluye contexto suprimido):\n")
            lines.append("Traceback (most recent call last):\n")
            tb = item.__traceback__
            while tb is not None:
                code = tb.tb_frame.f_code
                # No capturar variables locales ni líneas de fuente con posibles literales secretos.
                lines.append(f'  File "{code.co_filename}", line {tb.tb_lineno}, in {code.co_name}\n')
                tb = tb.tb_next
            lines.append(f"{type(item).__name__}: {item}\n")
            for note in getattr(item, "__notes__", ()):
                lines.append(str(note) + "\n")
            if isinstance(item, BaseExceptionGroup):
                for child in item.exceptions:
                    render(child)

    render(exc)
    return redact("".join(lines))
