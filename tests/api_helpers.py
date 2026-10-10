"""Esperar el estado explícito en pruebas de endpoints posteriores al arranque."""

from time import monotonic, sleep

from fastapi.testclient import TestClient


class ReadyClient(TestClient):
    def __enter__(self):
        super().__enter__()
        try:
            deadline = monotonic() + 20
            while monotonic() < deadline:
                state = self.get("/api/estado").json()
                if state["listo"] or "fallido" in state["detalle"]:
                    return self
                sleep(0.01)
            raise AssertionError("El precalentamiento de prueba no terminó")
        except BaseException:
            self.__exit__(None, None, None)
            raise
