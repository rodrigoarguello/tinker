"""De donde salen las canciones.

Dos formas de pedir, y la diferencia importa:

  buscar / obtener   UN INTERRUPTOR. Van al proveedor configurado y a ninguno
                     mas. Es la vieja interfaz del buscador.
  resolver           UNA CASCADA. Convierte texto pegado a mano --"Californication
                     — Red Hot Chili Peppers"-- en una canción con identidad
                     estable, probando Spotify, despues el catalogo local, y
                     acuñando una identidad propia si ninguno la reconoce.

`resolver` NUNCA devuelve None, y ese es el punto: permite que un evento tenga
su repertorio HOY, sin una sola credencial. El dia que haya Spotify, las
mismas filas se ENRIQUECEN con su `spotify_id` en vez de reemplazarse -- una
canción con solicitudes apuntandole no puede cambiar de identidad.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass

from app.ajustes import ajustes


@dataclass(frozen=True)
class Pista:
    proveedor: str
    proveedor_id: str
    titulo: str
    artista: str
    album: str | None = None
    imagen: str | None = None
    duracion_ms: int | None = None
    # Las tres que Spotify ya manda en cada respuesta y que se descartaban.
    # `popularidad` viene 0..100 y se guarda 0..1 para que sea comparable con
    # la de Apple y la de Deezer; `anio` es la decada de verdad, no la fecha de
    # descarga; `explicito` decide si una canción va a una boda o no.
    popularidad: float | None = None
    anio: int | None = None
    explicito: bool | None = None

    def como_json(self) -> dict:
        return {
            "id": self.proveedor_id,
            "proveedor": self.proveedor,
            "titulo": self.titulo,
            "artista": self.artista,
            "album": self.album,
            "imagen": self.imagen,
            "duracion_ms": self.duracion_ms,
        }


# ── normalizacion, en un solo lugar ──────────────────────────────────────

def plano(texto: str) -> str:
    """Sin acentos, en minusculas y con los espacios colapsados.

    Quien escribe "musica ligera" tiene que encontrar "De música ligera", y
    "LOS PALMERAS " pegado de una planilla tiene que ser el mismo artista que
    "Los Palmeras".
    """
    descompuesto = unicodedata.normalize("NFD", (texto or "").strip().lower())
    sin_tildes = "".join(c for c in descompuesto if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", sin_tildes)


# Lo que separa al artista principal de los invitados. "Shakira, Bizarrap",
# "Bizarrap feat. Shakira" y "Shakira & Alejandro Sanz" son, para la regla de
# «no dos del mismo artista en una tanda», el mismo artista.
_ACOMPAÑANTES = re.compile(r"\s*(?:,|&|\bfeat\.?\b|\bft\.?\b|\bcon\b|\bx\b|\by\b)\s+", re.I)


def clave_artista(artista: str) -> str:
    """El artista principal, normalizado. Es la clave de la regla de diversidad."""
    return _ACOMPAÑANTES.split(plano(artista), maxsplit=1)[0].strip()[:120] or "?"


def id_curado(titulo: str, artista: str) -> str:
    """La identidad que se acuña para una canción pegada a mano.

    Determinista a proposito: el mismo tema cargado en la fiesta del sabado y
    en la del domingo es la MISMA fila de `canciones`, y por lo tanto la misma
    en cualquier ranking que las cruce.
    """
    huella = hashlib.sha1(f"{plano(titulo)}|{clave_artista(artista)}".encode()).hexdigest()
    return f"cur-{huella[:16]}"


# ── los proveedores ──────────────────────────────────────────────────────

def _proveedor():
    if ajustes().catalogo == "spotify":
        from app.catalogo import spotify

        return spotify
    from app.catalogo import local

    return local


async def buscar(consulta: str, limite: int = 20) -> list[Pista]:
    consulta = consulta.strip()
    if len(consulta) < 2:
        return []
    return await _proveedor().buscar(consulta, limite)


async def obtener(proveedor_id: str) -> Pista | None:
    return await _proveedor().obtener(proveedor_id)


async def resolver(titulo: str, artista: str) -> Pista:
    """Texto pegado a mano -> una Pista con identidad estable. Nunca None."""
    titulo, artista = titulo.strip()[:300], artista.strip()[:300]

    if ajustes().spotify_disponible:
        from app.catalogo import spotify

        encontrada = await spotify.parecida(titulo, artista)
        if encontrada is not None:
            return encontrada

    from app.catalogo import local

    encontrada = await local.parecida(titulo, artista)
    if encontrada is not None:
        return encontrada

    return Pista(
        proveedor="curado",
        proveedor_id=id_curado(titulo, artista),
        titulo=titulo,
        artista=artista or "—",
    )
