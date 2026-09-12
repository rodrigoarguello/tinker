"""La cola hacia la playlist de Spotify.

TRES CONCEPTOS SEPARADOS, y la separacion es el punto:

    VOTO                 lo que la persona eligio. Se guarda siempre, pase lo
                         que pase con Spotify.
    PLAYLIST             lo que termina en la playlist. Depende de la politica
                         del evento y de que Spotify conteste.
    COLA DE REPRODUCCION lo que suena ahora. Tinker todavia no la tiene, y esta
                         arquitectura no se lo impide el dia que la tenga.

Que una canción entre a la playlist NO significa que suene, y que Spotify falle
NO puede afectar al telefono de nadie. Por eso esto es una cola con reintentos
y no una llamada dentro de `/elegir`.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import config
from app.modelos import Cancion, Evento, Solicitud, SpotifyCola, SpotifyCuenta
from app.spotify import cliente

registro = logging.getLogger("tinker.spotify.cola")


def modo_de(evento: Evento) -> tuple[str, int]:
    """La politica del evento, o la global si no la pisa."""
    modo = (evento.spotify_modo or config.SPOTIFY_ADD_MODE or "VOTE_THRESHOLD").upper()
    votos = evento.spotify_votos or config.SPOTIFY_VOTOS_MINIMOS
    return modo, votos


def _votos(sesion: Session, evento_id: int, cancion_id: int) -> int:
    return sesion.scalar(
        select(func.count(Solicitud.id)).where(
            Solicitud.evento_id == evento_id, Solicitud.cancion_id == cancion_id
        )
    ) or 0


def corresponde(sesion: Session, evento: Evento, cancion_id: int, final: bool = False) -> bool:
    """Si esta canción tiene que entrar a la playlist AHORA.

    EVERY_SELECTION   cada eleccion entra.
    FINAL_SELECTION   solo cuando la persona termino sus cinco rondas.
    VOTE_THRESHOLD    cuando junta los votos configurados. Es el que evita que
                      la playlist de una fiesta de doscientas personas tenga
                      ciento ochenta canciones que eligio una sola.
    """
    modo, minimo = modo_de(evento)
    if modo == "EVERY_SELECTION":
        return True
    if modo == "FINAL_SELECTION":
        return final
    return _votos(sesion, evento.id, cancion_id) >= minimo


def encolar(sesion: Session, evento: Evento, cancion_id: int) -> bool:
    """Pone una canción en la cola. Idempotente por la restriccion de la base.

    Nunca levanta: esto se llama desde el camino caliente y un problema de
    Spotify no puede tumbar la eleccion de nadie.
    """
    playlist = cliente.id_de_playlist(evento.playlist_url)
    if not playlist:
        return False
    cancion = sesion.get(Cancion, cancion_id)
    if cancion is None:
        return False
    fila = SpotifyCola(
        evento_id=evento.id,
        cancion_id=cancion_id,
        playlist_id=playlist,
        spotify_id=cancion.spotify_id,
    )
    sesion.add(fila)
    try:
        sesion.commit()
        return True
    except IntegrityError:
        # Ya estaba en la cola para esa playlist: es exactamente lo que se
        # queria, «una canción por playlist».
        sesion.rollback()
        return False


def encolar_lo_elegido(sesion: Session, evento: Evento) -> int:
    """Encola todo lo elegido que cumpla la politica y no este en la cola.

    Es el rescate para cuando Spotify se conecta a mitad de fiesta: lo que se
    eligio antes no se pierde.
    """
    playlist = cliente.id_de_playlist(evento.playlist_url)
    if not playlist:
        return 0
    elegidas = sesion.execute(
        select(Solicitud.cancion_id, func.count(Solicitud.id))
        .where(Solicitud.evento_id == evento.id)
        .group_by(Solicitud.cancion_id)
    ).all()
    modo, minimo = modo_de(evento)
    encoladas = 0
    for cancion_id, votos in elegidas:
        if modo == "VOTE_THRESHOLD" and votos < minimo:
            continue
        if encolar(sesion, evento, cancion_id):
            encoladas += 1
    return encoladas


# Cuantas por ciclo. Quince y no treinta: con treinta seguidas Spotify empieza
# a contestar 429 QUOTA_EXCEEDED. El worker corre cada minuto, asi que quince
# por vuelta son novecientas por hora -- de sobra para cualquier fiesta.
POR_CICLO = 15
PAUSA_ENTRE = 0.35


async def procesar(sesion: Session, limite: int = POR_CICLO) -> dict:
    """Manda a Spotify lo que este listo. Corre FUERA de los pedidos del juego.

    Devuelve un resumen y no levanta nunca: el peor caso es que todo quede
    PENDING para el proximo intento.
    """
    cuenta = sesion.scalar(select(SpotifyCuenta).limit(1))
    if cuenta is None or not cliente.configurado():
        return {"agregadas": 0, "motivo": "spotify no conectado"}

    ahora = datetime.now(timezone.utc)
    pendientes = sesion.execute(
        select(SpotifyCola)
        .where(
            SpotifyCola.estado.in_(("PENDING", "ERROR")),
            SpotifyCola.proximo_intento <= ahora,
        )
        .order_by(SpotifyCola.id)
        .limit(limite)
    ).scalars().all()
    if not pendientes:
        return {"agregadas": 0, "pendientes": 0}

    try:
        acceso = await cliente.token_usuario(cuenta.refresh_token)
    except Exception as error:  # noqa: BLE001
        registro.warning("no pude renovar el token de Spotify: %s", error)
        return {"agregadas": 0, "motivo": "token"}

    # Lo que YA tiene la playlist. Se consulta una vez por vuelta y no una por
    # canción: la playlist es de una persona y puede haberla tocado a mano.
    ya_estan: dict[str, set[str]] = {}
    agregadas, duplicadas, fallidas = 0, 0, 0

    for fila in pendientes:
        fila.intentos += 1
        fila.ultimo_intento = ahora
        cancion = sesion.get(Cancion, fila.cancion_id)

        # Sin `spotify_id` no hay nada que mandar: se intenta resolver ahora.
        spotify_id = fila.spotify_id or (cancion.spotify_id if cancion else None)
        if not spotify_id and cancion is not None:
            try:
                pista = await cliente.buscar_pista(cancion.titulo, cancion.artista)
                if pista:
                    spotify_id = pista["id"]
                    cancion.spotify_id = spotify_id
                    fila.spotify_id = spotify_id
            except Exception as error:  # noqa: BLE001
                registro.info("no pude resolver %s: %s", fila.cancion_id, error)

        if not spotify_id:
            _reprogramar(fila, "no encontrada en Spotify")
            fallidas += 1
            continue

        if fila.playlist_id not in ya_estan:
            try:
                ya_estan[fila.playlist_id] = await cliente.playlist_contiene(acceso, fila.playlist_id)
            except Exception as error:  # noqa: BLE001
                registro.warning("no pude leer la playlist: %s", error)
                ya_estan[fila.playlist_id] = set()

        if spotify_id in ya_estan[fila.playlist_id]:
            fila.estado = "DUPLICATE"
            fila.enviado_en = ahora
            duplicadas += 1
            continue

        try:
            await cliente.agregar(acceso, fila.playlist_id, spotify_id)
            fila.estado = "ADDED"
            fila.enviado_en = ahora
            fila.ultimo_error = None
            ya_estan[fila.playlist_id].add(spotify_id)
            agregadas += 1
        except Exception as error:  # noqa: BLE001
            # LA PLAYLIST FALLO. Se intenta la COLA DE REPRODUCCION, que es
            # otro endpoint y --con una app en modo desarrollo-- el unico que
            # Spotify deja usar. Para lo que hace tinker es incluso mejor: la
            # canción elegida va a lo que esta sonando ahora.
            try:
                await cliente.en_cola(acceso, spotify_id)
                fila.estado = "ADDED"
                fila.enviado_en = ahora
                fila.ultimo_error = "a la cola de reproduccion (la playlist esta bloqueada)"
                agregadas += 1
                continue
            except Exception as otro:  # noqa: BLE001
                registro.info("ni playlist ni cola: %s", str(otro)[:160])
            texto = str(error)[:300]
            # Un 401 con el token recien renovado quiere decir que el permiso
            # se revoco: se olvida el token para que el proximo ciclo lo pida
            # de nuevo en vez de repetir el mismo error.
            if "401" in texto:
                cliente.olvidar_token_usuario()
            _reprogramar(fila, texto)
            fallidas += 1

    sesion.commit()
    return {"agregadas": agregadas, "duplicadas": duplicadas, "fallidas": fallidas,
            "revisadas": len(pendientes)}


def _reprogramar(fila: SpotifyCola, error: str) -> None:
    """Backoff: 1, 5, 15 y 60 minutos. Despues se abandona.

    Se abandona a proposito: una canción que fallo cinco veces no se arregla
    insistiendo, y una cola que crece para siempre esconde el problema.

    CON UNA EXCEPCION: «no hay dispositivo activo» no es un fallo de la
    canción, es que nadie esta escuchando musica en este momento. Eso se
    arregla solo en cuanto alguien le da play, asi que no gasta intentos ni
    lleva a ABANDONED -- se reintenta en un minuto, para siempre si hace falta.
    """
    fila.ultimo_error = error
    if "NO_ACTIVE_DEVICE" in error or "No active device" in error:
        fila.intentos = max(0, fila.intentos - 1)      # no cuenta como intento
        fila.estado = "PENDING"
        fila.proximo_intento = datetime.now(timezone.utc) + timedelta(minutes=1)
        return
    esperas = config.SPOTIFY_ESPERAS_MIN
    if fila.intentos > len(esperas):
        fila.estado = "ABANDONED"
        registro.warning("abandonada %s: %s", fila.cancion_id, error)
        return
    fila.estado = "ERROR"
    minutos = esperas[min(fila.intentos - 1, len(esperas) - 1)]
    fila.proximo_intento = datetime.now(timezone.utc) + timedelta(minutes=minutos)
