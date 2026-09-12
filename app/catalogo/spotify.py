"""Spotify por client_credentials: alcanza para BUSCAR.

Lo que este flujo NO permite es crear la playlist del final del evento --eso
necesita un token de usuario con su propio consentimiento, y todavia no esta
hecho. Cuando se haga, va en un modulo aparte: buscar es publico y anonimo,
publicar es en nombre de una persona, y mezclarlos en el mismo cliente termina
con un token de usuario viajando en cada busqueda de cada telefono.
"""
from __future__ import annotations

import base64
import logging
import time

import httpx

from app.ajustes import ajustes
from app.catalogo import Pista, clave_artista, plano

registro = logging.getLogger("tinker.spotify")

_TOKEN: str | None = None
_VENCE: float = 0.0


async def _token() -> str:
    global _TOKEN, _VENCE
    # Un minuto de margen: renovar justo en el limite deja pedidos en el aire.
    if _TOKEN and time.time() < _VENCE - 60:
        return _TOKEN
    cfg = ajustes()
    if not cfg.spotify_disponible:
        raise RuntimeError("TINKER_CATALOGO=spotify pero faltan SPOTIFY_CLIENT_ID/SECRET")
    credencial = base64.b64encode(f"{cfg.spotify_id}:{cfg.spotify_secreto}".encode()).decode()
    async with httpx.AsyncClient(timeout=10) as cliente:
        respuesta = await cliente.post(
            "https://accounts.spotify.com/api/token",
            data={"grant_type": "client_credentials"},
            headers={"Authorization": f"Basic {credencial}"},
        )
        respuesta.raise_for_status()
        datos = respuesta.json()
    _TOKEN = datos["access_token"]
    _VENCE = time.time() + int(datos.get("expires_in", 3600))
    return _TOKEN


def _anio(album: dict) -> int | None:
    """El año de `release_date`, que viene "1997", "1997-08" o "1997-08-12"."""
    crudo = (album.get("release_date") or "")[:4]
    return int(crudo) if crudo.isdigit() else None


def _pista(item: dict) -> Pista:
    album = item.get("album") or {}
    imagenes = album.get("images") or []
    # La mas chica que sirva: el telefono muestra una miniatura de 64px y
    # bajar la portada de 640 es tirar datos moviles de la gente.
    imagen = imagenes[-1]["url"] if imagenes else None
    # `album.release_date` y `explicit` vienen en cada respuesta, sin un pedido
    # extra y sin scopes: son la decada DE VERDAD --no la fecha de descarga-- y
    # la adecuacion al evento. Hasta hoy se tiraban las dos.
    #
    # `popularity` se lee si viene, y EN MODO DEVELOPMENT NO VIENE: se verifico
    # contra la API y la clave directamente no esta en el objeto. Por eso
    # `Cancion.popularidad` queda en NULL y el termino de mainstream del puntaje
    # colectivo se reparte entre los otros en vez de contar como cero. El dia
    # que la app salga de Development, esto empieza a llenarse solo.
    popularidad = item.get("popularity")
    return Pista(
        proveedor="spotify",
        proveedor_id=item["id"],
        titulo=item["name"],
        artista=", ".join(a["name"] for a in item.get("artists", [])) or "—",
        album=album.get("name"),
        imagen=imagen,
        duracion_ms=item.get("duration_ms"),
        popularidad=round(popularidad / 100, 3) if isinstance(popularidad, int) else None,
        anio=_anio(album),
        explicito=item.get("explicit"),
    )


async def buscar(consulta: str, limite: int = 20) -> list[Pista]:
    cabeceras = {"Authorization": f"Bearer {await _token()}"}
    async with httpx.AsyncClient(timeout=10) as cliente:
        respuesta = await cliente.get(
            "https://api.spotify.com/v1/search",
            params={
                "q": consulta,
                "type": "track",
                "limit": min(limite, 50),
                "market": ajustes().spotify_mercado,
            },
            headers=cabeceras,
        )
        if respuesta.status_code != 200:
            registro.warning("busqueda fallida %s: %s", respuesta.status_code, respuesta.text[:200])
            return []
        items = respuesta.json().get("tracks", {}).get("items", [])
    return [_pista(i) for i in items if i]


async def obtener(proveedor_id: str) -> Pista | None:
    cabeceras = {"Authorization": f"Bearer {await _token()}"}
    async with httpx.AsyncClient(timeout=10) as cliente:
        respuesta = await cliente.get(
            f"https://api.spotify.com/v1/tracks/{proveedor_id}",
            params={"market": ajustes().spotify_mercado},
            headers=cabeceras,
        )
        if respuesta.status_code != 200:
            return None
        return _pista(respuesta.json())


async def parecida(titulo: str, artista: str) -> Pista | None:
    """La misma canción, escrita distinto. Para `resolver`.

    Busca con los campos de Spotify (`track:` y `artist:`) en vez de pegar todo
    en una consulta libre: una busqueda libre de "Vasos vacios Cadillacs"
    devuelve tambien covers y presentaciones en vivo, y el primero no es
    siempre el que la persona quiso poner en su fiesta.

    Y despues COMPARA. Spotify siempre devuelve algo; que haya devuelto algo no
    quiere decir que sea esto.
    """
    consulta = f'track:"{titulo}"'
    if artista.strip():
        consulta += f' artist:"{artista}"'
    for pista in await buscar(consulta, limite=5):
        if plano(pista.titulo) == plano(titulo) and clave_artista(pista.artista) == clave_artista(artista):
            return pista
    return None
