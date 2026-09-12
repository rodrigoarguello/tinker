"""Todo lo que cambia entre una maquina y otra, en un solo lugar."""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


# Lo que cuenta como "si" en una variable de entorno booleana.
AFIRMATIVOS = ("si", "sí", "yes", "1", "true")


@dataclass(frozen=True)
class Ajustes:
    bd: str
    catalogo: str            # "local" | "spotify"
    spotify_id: str
    spotify_secreto: str
    spotify_mercado: str
    semilla: bool
    docs: bool

    @property
    def spotify_disponible(self) -> bool:
        return bool(self.spotify_id and self.spotify_secreto)


@lru_cache
def ajustes() -> Ajustes:
    catalogo = os.environ.get("TINKER_CATALOGO", "local").strip().lower()
    if catalogo not in ("local", "spotify"):
        raise RuntimeError(f"TINKER_CATALOGO invalido: {catalogo!r}. Vale 'local' o 'spotify'.")
    return Ajustes(
        bd=os.environ.get("TINKER_BD", "postgresql+psycopg://localhost/tinker"),
        catalogo=catalogo,
        spotify_id=os.environ.get("SPOTIFY_CLIENT_ID", "").strip(),
        spotify_secreto=os.environ.get("SPOTIFY_CLIENT_SECRET", "").strip(),
        spotify_mercado=os.environ.get("SPOTIFY_MERCADO", "PY").strip() or "PY",
        semilla=os.environ.get("TINKER_SEMILLA", "no").strip().lower() in AFIRMATIVOS,
        # Swagger UI en /docs. Apagado por defecto: la app es publica y la
        # superficie de la API no tiene por que estarlo. compose.dev.yaml lo
        # enciende para que un clon limpio se pueda explorar desde el navegador.
        docs=os.environ.get("TINKER_DOCS", "no").strip().lower() in AFIRMATIVOS,
    )
