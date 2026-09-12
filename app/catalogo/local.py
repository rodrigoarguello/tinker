"""Catalogo semilla: 149 canciones en un JSON, sin ninguna llamada de red.

Ya no es el catalogo de los eventos --eso ahora es el repertorio curado de cada
uno-- sino dos cosas: el repertorio del evento de demostracion, y el segundo
escalon de `resolver`, que reconoce un tema pegado a mano y le pone su genero
sin preguntarle a nadie.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from app.catalogo import Pista, clave_artista, plano

ARCHIVO = Path(__file__).with_name("semilla.json")


@lru_cache
def _catalogo() -> list[tuple[Pista, str, str, str]]:
    crudo = json.loads(ARCHIVO.read_text(encoding="utf-8"))
    return [
        (
            Pista(
                proveedor="local",
                proveedor_id=fila["id"],
                titulo=fila["titulo"],
                artista=fila["artista"],
            ),
            plano(fila["titulo"]),
            plano(fila["artista"]),
            clave_artista(fila["artista"]),
        )
        for fila in crudo
    ]


async def buscar(consulta: str, limite: int = 20) -> list[Pista]:
    aguja = plano(consulta)
    empiezan, contienen = [], []
    for pista, titulo, artista, _ in _catalogo():
        if titulo.startswith(aguja) or artista.startswith(aguja):
            empiezan.append(pista)
        elif aguja in titulo or aguja in artista:
            contienen.append(pista)
    return (empiezan + contienen)[:limite]


async def obtener(proveedor_id: str) -> Pista | None:
    for pista, _, _, _ in _catalogo():
        if pista.proveedor_id == proveedor_id:
            return pista
    return None


async def parecida(titulo: str, artista: str) -> Pista | None:
    """La misma canción, escrita distinto. Para `resolver`.

    Exige que coincidan las DOS cosas: el titulo y el artista principal. Con el
    titulo solo, "Vasos vacios" de Cadillacs se comeria cualquier otra version,
    y el repertorio de la fiesta terminaria con la canción equivocada.
    """
    objetivo_titulo, objetivo_artista = plano(titulo), clave_artista(artista)
    for pista, t, _, a in _catalogo():
        if t == objetivo_titulo and a == objetivo_artista:
            return pista
    return None


def generos() -> dict[str, str]:
    """id de canción -> genero de la semilla. Lo usa el importador para que el
    evento de demostracion nazca etiquetado."""
    crudo = json.loads(ARCHIVO.read_text(encoding="utf-8"))
    return {fila["id"]: fila["genero"] for fila in crudo}
