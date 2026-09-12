"""Lo que esta sonando y lo que sigue. La playlist, sin playlist.

Spotify nos cerro TODOS los endpoints de playlist --403 incluso para crear una
nueva, y tambien con el token anonimo-- y nos dejo abiertos los del
reproductor. Comprobado el 2026-09-12 contra la API real:

    GET  /me/player/devices  200      POST /me/player/queue        200
    GET  /me/player          200      GET  /playlists/{id}/tracks  403
    GET  /me/player/queue    200      POST /playlists/{id}/tracks  403
                                      POST /users/{id}/playlists   403

Asi que la playlist no hace falta: LA LISTA ES NUESTRA, vive en `solicitudes`,
y Spotify solo pone el parlante. `PUT /me/player/play` acepta una lista de URIs
explicita --hasta cien-- y eso es exactamente el ranking de la noche.

UNA sola llamada cada quince segundos y no dos: la app esta en modo desarrollo
y hoy ya contesto 429 QUOTA_EXCEEDED. `/me/player/queue` trae lo que suena Y lo
que sigue en la misma respuesta, asi que no se pide `/me/player` aparte. Por
eso tampoco hay barra de progreso en la pantalla --exigiria esa segunda
llamada-- y en su lugar late el ecualizador, que no cuesta nada.
"""
from __future__ import annotations

import logging
import time

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.modelos import Cancion, Evento, Solicitud, SpotifyCuenta
from app.spotify import cliente

registro = logging.getLogger("tinker.spotify.reproduccion")

# Cuantas de las que siguen se muestran. Tres: es una columna de televisor, no
# una playlist para leer.
SIGUEN = 3

# Cuantas manda `sonar_por_votos`. Spotify acepta cien URIs por llamada; con
# cincuenta hay mas de tres horas de musica y sobra margen para que la cola se
# siga alimentando sola con lo que la gente elija despues.
TOPE_REPRODUCIR = 50

# El estado vive cacheado: si hay tres televisores abiertos, los tres tienen
# que leer la MISMA llamada. Sin esto, cada pantalla que se conecta es un
# pedido mas a una API que ya nos esta limitando.
VIDA_CACHE = 12.0
_CACHE: tuple[dict, float] | None = None


def _pista_json(pista: dict | None, votos: dict[str, int]) -> dict | None:
    """Una pista de Spotify, en el idioma de la pantalla."""
    if not pista or not pista.get("id"):
        return None
    imagenes = (pista.get("album") or {}).get("images") or []
    return {
        "id": pista["id"],
        "titulo": pista.get("name") or "",
        "artista": ", ".join(a["name"] for a in pista.get("artists", []) if a.get("name")),
        # La mas chica que sirva: es un disco de 56px en el televisor, no una
        # portada. Spotify las devuelve de mayor a menor.
        "imagen": imagenes[-1]["url"] if imagenes else None,
        "votos": votos.get(pista["id"], 0),
    }


def votos_del_evento(sesion: Session, evento_id: int) -> dict[str, int]:
    """`spotify_id -> votos`, para poder anotar lo que suena.

    Que la pantalla diga «la eligieron 5 personas» al lado del tema que esta
    sonando es la unica forma de cerrar el circulo a la vista: sin eso, el
    televisor muestra una lista y un reproductor que no se saludan.
    """
    filas = sesion.execute(
        select(Cancion.spotify_id, func.count(Solicitud.id))
        .join(Solicitud, Solicitud.cancion_id == Cancion.id)
        .where(Solicitud.evento_id == evento_id, Cancion.spotify_id.is_not(None))
        .group_by(Cancion.spotify_id)
    ).all()
    return {spotify_id: cuenta for spotify_id, cuenta in filas}


async def leer(sesion: Session, evento: Evento, forzar: bool = False) -> dict:
    """Lo que suena y lo que sigue. Nunca levanta: la pantalla no se rompe
    porque Spotify no conteste."""
    global _CACHE

    if not forzar and _CACHE and time.monotonic() - _CACHE[1] < VIDA_CACHE:
        crudo = _CACHE[0]
    else:
        cuenta = sesion.scalar(select(SpotifyCuenta).limit(1))
        if cuenta is None or not cliente.configurado():
            return {"conectado": False, "sonando": None, "siguen": []}
        try:
            acceso = await cliente.token_usuario(cuenta.refresh_token)
            crudo = await cliente.cola_de_reproduccion(acceso)
        except Exception as error:  # noqa: BLE001
            registro.info("no pude leer el reproductor: %s", str(error)[:160])
            return {"conectado": True, "sonando": None, "siguen": []}
        _CACHE = (crudo, time.monotonic())

    marcador = votos_del_evento(sesion, evento.id)
    sonando = _pista_json(crudo.get("currently_playing"), marcador)
    siguen = [
        pista for pista in
        (_pista_json(p, marcador) for p in (crudo.get("queue") or [])[:SIGUEN])
        if pista
    ]
    return {"conectado": True, "sonando": sonando, "siguen": siguen}


def _ranking_uris(sesion: Session, evento_id: int, tope: int) -> list[str]:
    """El ranking de la noche como URIs de Spotify, de mas votada a menos.

    El desempate por id, igual que en `panorama.ranking`: sin el, dos canciones
    con los mismos votos se turnan el lugar en cada llamada y la lista que
    suena no es la que muestra la pantalla.
    """
    filas = sesion.execute(
        select(Cancion.spotify_id, func.count(Solicitud.id).label("cuenta"))
        .join(Solicitud, Solicitud.cancion_id == Cancion.id)
        .where(Solicitud.evento_id == evento_id, Cancion.spotify_id.is_not(None))
        .group_by(Cancion.spotify_id, Cancion.id)
        .order_by(desc("cuenta"), Cancion.id)
        .limit(tope)
    ).all()
    return [f"spotify:track:{spotify_id}" for spotify_id, _ in filas]


async def sonar_por_votos(sesion: Session, evento: Evento, tope: int = TOPE_REPRODUCIR) -> dict:
    """Pone a sonar el ranking, de la mas elegida a la menos.

    ES DISRUPTIVO A PROPOSITO y por eso NO lo dispara ningun job: reemplaza lo
    que este sonando en el salon. Sale de un boton de `/admin`, que es donde
    hay una persona mirando.
    """
    cuenta = sesion.scalar(select(SpotifyCuenta).limit(1))
    if cuenta is None or not cliente.configurado():
        return {"ok": False, "motivo": "spotify no conectado"}

    uris = _ranking_uris(sesion, evento.id, tope)
    if not uris:
        return {"ok": False, "motivo": "todavia no hay elecciones con id de Spotify"}

    acceso = await cliente.token_usuario(cuenta.refresh_token)
    dispositivos = await cliente.dispositivos(acceso)
    if not dispositivos:
        return {"ok": False, "motivo": "no hay ningun dispositivo de Spotify abierto"}
    activo = next((d for d in dispositivos if d.get("is_active")), dispositivos[0])

    await cliente.reproducir(acceso, uris, activo.get("id"))
    global _CACHE
    _CACHE = None      # lo que habia cacheado ya no es cierto
    return {"ok": True, "temas": len(uris), "dispositivo": activo.get("name")}
