"""Errores públicos constantes; nunca interpolar excepciones internas."""


class APIError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        self.status, self.code, self.message = status, code, message
        super().__init__(message)
