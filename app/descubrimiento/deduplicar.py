"""Quedarse con UNA version de cada canción.

Las fuentes devuelven "Everlong", "Everlong - Remastered 2011" y "Everlong
(Live at Wembley)" como tres cosas distintas. Para una fiesta son una sola, y
la que se quiere es la original.
"""
from __future__ import annotations

from app.descubrimiento.base import CancionDescubierta
from app.descubrimiento.normalizar import huella, version_de

# Cuanto vale cada version cuando hay que elegir. ORIGINAL siempre gana; un
# remix entra solo si es lo unico que hay de esa canción --y entonces es
# porque de verdad es la version que circula--.
PREFERENCIA = {
    "ORIGINAL": 0,
    "REMASTER": 1,
    "REMIX": 2,
    "ACOUSTIC": 3,
    "LIVE": 4,
    "COVER": 5,
    "OTHER": 6,
}


def deduplicar(descubiertas: list[CancionDescubierta]) -> list[CancionDescubierta]:
    """Una por huella, prefiriendo la version original y el mejor ranking."""
    mejores: dict[str, tuple[int, int, CancionDescubierta]] = {}
    for cruda in descubiertas:
        if not (cruda.titulo or "").strip() or not (cruda.artista or "").strip():
            continue
        clave = huella(cruda.titulo, cruda.artista)
        peso = PREFERENCIA.get(version_de(cruda.titulo), 6)
        # La que viene de un CHART gana, y esto costo entender por que la
        # tendencia de Paraguay se perdia: la misma canción llegaba por el top
        # de Apple y por una busqueda de Deezer, ganaba la de Deezer --mejor
        # posicion-- y con ella se iba la unica fuente que sabia que la canción
        # esta sonando ahora.
        if cruda.extra.get("chart"):
            peso -= 10
        # Ranking chico es mejor; sin ranking, al fondo.
        posicion = cruda.ranking if cruda.ranking is not None else 9999
        anterior = mejores.get(clave)
        if anterior is None or (peso, posicion) < (anterior[0], anterior[1]):
            mejores[clave] = (peso, posicion, cruda)
    return [fila[2] for fila in mejores.values()]
