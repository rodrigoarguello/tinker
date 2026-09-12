"""Completa las canciones que ya estan con lo que Spotify manda gratis.

`popularity`, `album.release_date` y `explicit` vienen en CADA respuesta de
Spotify y el codigo los descartaba. Desde hoy se guardan al crear la canción,
pero las que ya estaban en la base se crearon antes y quedaron sin eso.

Esto es la pasada de relleno, y va DE A UNA canción por pedido. El endpoint por
lotes --`GET /v1/tracks?ids=`, cincuenta de un saque-- seria ocho llamadas en
vez de cuatrocientas, y esta BLOQUEADO: contesta 403 en modo Development. El
individual, `GET /v1/tracks/{id}`, contesta 200. Se probaron los dos antes de
elegir; el mapa completo de que endpoint vive y cual no esta en `cliente.py`.

QUE SE PUEDE TRAER Y QUE NO, medido contra la API y no supuesto:

  · `album.release_date` -> SI. Es la decada de verdad, y es lo que hace que el
    perfil del salon tenga decadas en vez de un diccionario vacio.
  · `explicit` -> SI.
  · `popularity` -> NO. En modo Development la clave ni siquiera aparece en el
    objeto. Se lee igual por si algun dia viene; mientras tanto queda NULL y el
    termino de mainstream se reparte entre los otros del puntaje colectivo.
  · audio-features (energy, danceability, valence) -> NO, y no es cuestion de
    quota: Spotify los retiro en noviembre de 2024 para toda app nueva. La
    energia de tinker sale de `intensidad`, que la ESTIMA el etiquetador. Es una
    estimacion declarada, no una medicion, y asi se dice en el README.

Se puede correr cuantas veces se quiera: solo mira las que les falta algo.
"""
from __future__ import annotations

import asyncio
import logging

import httpx
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.ajustes import ajustes
from app.catalogo.spotify import _anio, _token
from app.modelos import Cancion

registro = logging.getLogger("tinker.enriquecer")

LOTE = 25          # de cuantas se informa el progreso
TOPE_LOTES = 40
# De a tres a la vez. No es para ir rapido: es para no ganarse un 429, que ya
# pasó con `/v1/search` mientras se armaba esto.
SIMULTANEAS = 3


def pendientes(sesion: Session, limite: int = LOTE * TOPE_LOTES) -> list[Cancion]:
    """Las que tienen `spotify_id` y les falta alguno de los tres datos."""
    return list(
        sesion.execute(
            select(Cancion)
            .where(
                Cancion.spotify_id.is_not(None),
                or_(
                    Cancion.popularidad.is_(None),
                    Cancion.anio.is_(None),
                    Cancion.explicito.is_(None),
                ),
            )
            .limit(limite)
        ).scalars().all()
    )


async def _traer(ids: list[str]) -> dict[str, dict]:
    """Una canción por pedido, de a tres a la vez. Devuelve lo que se pudo."""
    cabeceras = {"Authorization": f"Bearer {await _token()}"}
    mercado = ajustes().spotify_mercado
    puerta = asyncio.Semaphore(SIMULTANEAS)
    salida: dict[str, dict] = {}

    async with httpx.AsyncClient(timeout=15) as cliente:
        async def una(spotify_id: str) -> None:
            async with puerta:
                try:
                    respuesta = await cliente.get(
                        f"https://api.spotify.com/v1/tracks/{spotify_id}",
                        params={"market": mercado},
                        headers=cabeceras,
                    )
                except Exception as error:  # noqa: BLE001
                    registro.info("%s: %s", spotify_id, type(error).__name__)
                    return
                if respuesta.status_code == 429:
                    # El 429 no se insiste: se anota y se corta el lote. Volver
                    # a correr esto mañana no cuesta nada; que Spotify nos
                    # ponga en penitencia en medio de una fiesta, si.
                    registro.warning("429 de Spotify: se deja el resto para despues")
                    return
                if respuesta.status_code != 200:
                    return
                salida[spotify_id] = respuesta.json()

        await asyncio.gather(*(una(i) for i in ids))
    return salida


async def enriquecer(sesion: Session) -> dict:
    """Rellena lo que falte. Nunca levanta: un lote que falla no frena al resto."""
    if not ajustes().spotify_disponible:
        return {"ok": False, "error": "sin_spotify", "completadas": 0}

    faltantes = pendientes(sesion)
    if not faltantes:
        return {"ok": True, "pendientes": 0, "completadas": 0, "lotes": 0}

    completadas, fallados = 0, 0
    lotes = [faltantes[i : i + LOTE] for i in range(0, len(faltantes), LOTE)]
    for numero, lote in enumerate(lotes, start=1):
        try:
            datos = await _traer([c.spotify_id for c in lote])
        except Exception as error:  # noqa: BLE001
            registro.warning("lote %d de %d fallo: %s", numero, len(lotes), error)
            fallados += 1
            continue

        for cancion in lote:
            pista = datos.get(cancion.spotify_id)
            if not pista:
                continue
            cambio = False
            popularidad = pista.get("popularity")
            # Solo se RELLENA: lo que ya tiene valor no se pisa, igual que en el
            # etiquetado. Un dato puesto a mano vale mas que uno traido.
            if cancion.popularidad is None and isinstance(popularidad, int):
                cancion.popularidad = round(popularidad / 100, 3)
                cambio = True
            if cancion.anio is None:
                anio = _anio(pista.get("album") or {})
                if anio:
                    cancion.anio = anio
                    cambio = True
            if cancion.explicito is None and pista.get("explicit") is not None:
                cancion.explicito = bool(pista["explicit"])
                cambio = True
            completadas += 1 if cambio else 0
        sesion.commit()
        registro.info("lote %d/%d: %d canciones", numero, len(lotes), len(lote))

    return {
        "ok": True,
        "pendientes": len(faltantes),
        "lotes": len(lotes),
        "fallados": fallados,
        "completadas": completadas,
    }
