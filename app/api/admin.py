"""Crear eventos, cargarles el repertorio, moverlos de estado y sacar el resultado.

NO tiene autenticacion propia. Vive detras de Authelia, que el proxy exige para
todo /api/admin/ y /admin. La identidad llega en la cabecera Remote-User y se
usa para UNA cosa: dejar escrito en el log quien abrio y quien cerro un evento.
Que la cabecera sea confiable depende de que este contenedor no publique ningun
puerto -- ver el comentario del compose.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, datetime, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, desc, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import config, panorama, recomendador
from app.ajustes import ajustes
from app.bd import con_sesion
from app.catalogo import clave_artista, resolver
from app.catalogo import local as catalogo_local
from app.descubrimiento import Deezer
from app.descubrimiento import importar
from app.jobs import metricas
from app.modelos import (
    ACTIVOS,
    ESTADOS,
    ArtistaPrioritario,
    CatalogoMusical,
    EventoPerfil,
    FuenteEstado,
    Cancion,
    Evento,
    Repertorio,
    Solicitud,
    SpotifyCola,
    SpotifyCuenta,
)
from app.recomendador import etiquetador, tandas
from app.spotify import cliente, cola, reproduccion
from app.tiempo_real.hub import hub

registro = logging.getLogger("tinker.admin")

ruteador = APIRouter(prefix="/api/admin")

SLUG = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

# Los tres guiones que aparecen cuando alguien pega una lista: el largo que
# pone Word, el medio, y el comun. Tambien " by ", que es como las exporta
# alguna app de musica.
SEPARADOR = re.compile(r"\s+[—–-]\s+|\s+by\s+", re.IGNORECASE)
TOPE_TEXTO = 64 * 1024


class EventoNuevo(BaseModel):
    slug: str = Field(min_length=2, max_length=60)
    nombre: str = Field(min_length=3, max_length=160)
    lugar: str | None = Field(default=None, max_length=160)
    fecha: date | None = None
    rondas: int = Field(default=5, ge=1, le=10)


class EventoCambio(BaseModel):
    estado: str | None = None
    nombre: str | None = Field(default=None, max_length=160)
    lugar: str | None = Field(default=None, max_length=160)
    playlist_url: str | None = None
    rondas: int | None = Field(default=None, ge=1, le=10)
    tope_ia: int | None = Field(default=None, ge=0)
    # Estas dos las mandaba el panel desde el principio (`admin.js:284`) y
    # Pydantic las descartaba en silencio por no estar declaradas aca: la
    # perilla se movia en pantalla, se guardaba nada y nadie se enteraba.
    explotar_pct: int | None = Field(default=None, ge=0, le=100)
    prioritarias_por_tanda: int | None = Field(default=None, ge=0, le=5)


class RepertorioNuevo(BaseModel):
    texto: str = Field(max_length=TOPE_TEXTO)
    reemplazar: bool = True


def _cuantas(sesion: Session, evento_id: int) -> int:
    return sesion.scalar(
        select(func.count(Repertorio.id)).where(Repertorio.evento_id == evento_id)
    ) or 0


def _json(evento: Evento, sesion: Session) -> dict:
    return {
        "slug": evento.slug,
        "nombre": evento.nombre,
        "lugar": evento.lugar,
        "fecha": evento.fecha.isoformat() if evento.fecha else None,
        "estado": evento.estado,
        "rondas": evento.rondas,
        "tope_ia": evento.tope_ia,
        "playlist_url": evento.playlist_url,
        "repertorio": _cuantas(sesion, evento.id),
        "totales": panorama.totales(sesion, evento.id),
        "ia": tandas.llamadas_al_modelo(sesion, evento.id),
    }


@ruteador.get("/eventos")
async def listar(sesion: Session = Depends(con_sesion)) -> dict:
    eventos = sesion.execute(select(Evento).order_by(desc(Evento.creado))).scalars().all()
    return {"eventos": [_json(e, sesion) for e in eventos]}


@ruteador.post("/eventos", status_code=201)
async def crear(
    cuerpo: EventoNuevo,
    sesion: Session = Depends(con_sesion),
    remote_user: str = Header(default="?", alias="Remote-User"),
) -> dict:
    if not SLUG.match(cuerpo.slug):
        raise HTTPException(422, {"error": "slug_invalido", "detalle": "minusculas, numeros y guiones"})
    evento = Evento(
        slug=cuerpo.slug,
        nombre=cuerpo.nombre,
        lugar=cuerpo.lugar,
        fecha=cuerpo.fecha,
        rondas=cuerpo.rondas,
    )
    sesion.add(evento)
    try:
        sesion.commit()
    except IntegrityError:
        sesion.rollback()
        raise HTTPException(409, {"error": "slug_ocupado"})
    registro.info("evento %s creado por %s", evento.slug, remote_user)
    return _json(evento, sesion)


@ruteador.patch("/eventos/{slug}")
async def cambiar(
    slug: str,
    cuerpo: EventoCambio,
    sesion: Session = Depends(con_sesion),
    remote_user: str = Header(default="?", alias="Remote-User"),
) -> dict:
    evento = sesion.scalar(select(Evento).where(Evento.slug == slug))
    if evento is None:
        raise HTTPException(404, {"error": "evento_inexistente"})

    if cuerpo.estado is not None:
        if cuerpo.estado not in ESTADOS:
            raise HTTPException(422, {"error": "estado_invalido", "validos": list(ESTADOS)})
        # Un evento sin repertorio no puede ponerse en vivo. Es la falla mas
        # barata de evitar y la mas cara de descubrir: el QR ya esta en la
        # pared y los telefonos entran a una pantalla vacia.
        if cuerpo.estado == "en_vivo" and _cuantas(sesion, evento.id) < recomendador.MINIMO_REPERTORIO:
            raise HTTPException(
                409,
                {
                    "error": "sin_repertorio",
                    "minimo": recomendador.MINIMO_REPERTORIO,
                    "tiene": _cuantas(sesion, evento.id),
                },
            )
        anterior, evento.estado = evento.estado, cuerpo.estado
        registro.info("evento %s: %s -> %s por %s", slug, anterior, cuerpo.estado, remote_user)

    for campo in (
        "nombre", "lugar", "playlist_url", "rondas", "tope_ia",
        "explotar_pct", "prioritarias_por_tanda",
    ):
        valor = getattr(cuerpo, campo)
        if valor is not None:
            setattr(evento, campo, valor)
    sesion.commit()

    # El cambio de estado se empuja: el telefono de la gente tiene que pasar
    # solo de «elegí tus canciones» a «esta es la playlist», sin que nadie
    # recargue nada.
    await hub.publicar(
        evento.slug,
        {"tipo": "estado", "estado": evento.estado, "playlist_url": evento.playlist_url},
    )
    return _json(evento, sesion)


# ─────────────────────────── repertorio ───────────────────────────

def _heredar_etiquetas(sesion: Session, cancion_id: int) -> dict:
    """Las etiquetas que esta canción ya tiene en OTRO evento.

    Etiquetar cuesta una llamada al modelo; la misma canción en la fiesta del
    sabado y en la del domingo no tiene por que pagarse dos veces.
    """
    fila = sesion.scalar(
        select(Repertorio)
        .where(Repertorio.cancion_id == cancion_id, Repertorio.genero.is_not(None))
        .limit(1)
    )
    if fila is None:
        return {}
    return {
        "genero": fila.genero,
        "epoca": fila.epoca,
        "idioma": fila.idioma,
        "intensidad": fila.intensidad,
    }


@ruteador.put("/eventos/{slug}/repertorio")
async def cargar_repertorio(
    slug: str, cuerpo: RepertorioNuevo, sesion: Session = Depends(con_sesion),
) -> dict:
    """La lista curada del evento: «Tema — Artista» por linea.

    Esta lista es LO UNICO que la gente va a poder elegir. No se mezcla con
    ningun catalogo general: la fiesta de un quinceañero y la de una empresa no
    eligen del mismo pozo.
    """
    evento = sesion.scalar(select(Evento).where(Evento.slug == slug))
    if evento is None:
        raise HTTPException(404, {"error": "evento_inexistente"})

    generos_semilla = catalogo_local.generos()
    entraron, ya_estaban, sin_resolver = 0, 0, []

    if cuerpo.reemplazar:
        # Se borra el repertorio, NO las canciones ni las solicitudes: alguien
        # ya pudo haber elegido, y su eleccion sigue contando en el ranking.
        sesion.execute(delete(Repertorio).where(Repertorio.evento_id == evento.id))
        sesion.commit()

    orden = _cuantas(sesion, evento.id)
    for linea in cuerpo.texto.splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#"):
            continue
        partes = SEPARADOR.split(linea, maxsplit=1)
        if len(partes) < 2 or not partes[0].strip() or not partes[1].strip():
            sin_resolver.append(linea)
            continue
        titulo, artista = partes[0].strip(), partes[1].strip()

        pista = await resolver(titulo, artista)
        cancion = sesion.scalar(
            select(Cancion).where(
                Cancion.proveedor == pista.proveedor, Cancion.proveedor_id == pista.proveedor_id
            )
        )
        if cancion is None:
            cancion = Cancion(
                proveedor=pista.proveedor,
                proveedor_id=pista.proveedor_id,
                titulo=pista.titulo,
                artista=pista.artista,
                album=pista.album,
                imagen=pista.imagen,
                duracion_ms=pista.duracion_ms,
                popularidad=pista.popularidad,
                anio=pista.anio,
                explicito=pista.explicito,
                spotify_id=pista.proveedor_id if pista.proveedor == "spotify" else None,
            )
            sesion.add(cancion)
            try:
                sesion.commit()
            except IntegrityError:
                sesion.rollback()
                cancion = sesion.scalar(
                    select(Cancion).where(
                        Cancion.proveedor == pista.proveedor,
                        Cancion.proveedor_id == pista.proveedor_id,
                    )
                )

        etiquetas = _heredar_etiquetas(sesion, cancion.id)
        if not etiquetas and pista.proveedor == "local":
            etiquetas = {"genero": generos_semilla.get(pista.proveedor_id)}

        orden += 1
        sesion.add(
            Repertorio(
                evento_id=evento.id,
                cancion_id=cancion.id,
                orden=orden,
                linea=linea,
                artista_clave=clave_artista(pista.artista),
                **etiquetas,
            )
        )
        try:
            sesion.commit()
            entraron += 1
        except IntegrityError:
            sesion.rollback()
            ya_estaban += 1
            orden -= 1

    evento.notas_repertorio = cuerpo.texto
    sesion.commit()

    total = _cuantas(sesion, evento.id)
    return {
        "total": total,
        "entraron": entraron,
        "ya_estaban": ya_estaban,
        "sin_resolver": sin_resolver[:50],
        "minimo": recomendador.MINIMO_REPERTORIO,
        "alcanza": total >= recomendador.MINIMO_REPERTORIO,
    }


@ruteador.get("/eventos/{slug}/repertorio")
async def ver_repertorio(slug: str, sesion: Session = Depends(con_sesion)) -> dict:
    evento = sesion.scalar(select(Evento).where(Evento.slug == slug))
    if evento is None:
        raise HTTPException(404, {"error": "evento_inexistente"})
    filas = sesion.execute(
        select(Repertorio, Cancion)
        .join(Cancion, Cancion.id == Repertorio.cancion_id)
        .where(Repertorio.evento_id == evento.id)
        .order_by(Repertorio.orden)
    ).all()
    return {
        "total": len(filas),
        "minimo": recomendador.MINIMO_REPERTORIO,
        "canciones": [
            {
                "id": cancion.proveedor_id,
                "titulo": cancion.titulo,
                "artista": cancion.artista,
                "proveedor": cancion.proveedor,
                "genero": rep.genero,
                "epoca": rep.epoca,
                "idioma": rep.idioma,
                "intensidad": rep.intensidad,
            }
            for rep, cancion in filas
        ],
    }


@ruteador.post("/eventos/{slug}/repertorio/etiquetar")
async def etiquetar_repertorio(slug: str, sesion: Session = Depends(con_sesion)) -> dict:
    """Completa las etiquetas que faltan, con el modelo, ANTES de la fiesta.

    Es la unica llamada al modelo del sistema que no la espera nadie, y por eso
    la unica que puede permitirse treinta segundos por lote. Se puede correr
    cuantas veces se quiera: lo ya etiquetado no se vuelve a consultar, y lo
    puesto a mano no se pisa nunca.

    Sin claves configuradas contesta `sin_modelo` y no rompe nada -- el juego
    funciona igual con `genero` solo, peor pero entero.
    """
    evento = sesion.scalar(select(Evento).where(Evento.slug == slug))
    if evento is None:
        raise HTTPException(404, {"error": "evento_inexistente"})
    resultado = await etiquetador.etiquetar(evento.id)
    registro.info("etiquetado de %s: %s", slug, resultado)
    return resultado


@ruteador.get("/eventos/{slug}/resultado")
async def resultado(slug: str, sesion: Session = Depends(con_sesion)) -> dict:
    """El ranking COMPLETO, no el top diez: es la materia prima de la playlist.

    Devuelve el id del proveedor de cada canción, que es lo unico que sirve
    para armarla despues sin volver a buscar por titulo y errarle.
    """
    evento = sesion.scalar(select(Evento).where(Evento.slug == slug))
    if evento is None:
        raise HTTPException(404, {"error": "evento_inexistente"})

    filas = sesion.execute(
        select(Cancion, func.count(Solicitud.id).label("cuenta"))
        .join(Solicitud, Solicitud.cancion_id == Cancion.id)
        .where(Solicitud.evento_id == evento.id)
        .group_by(Cancion.id)
        .order_by(desc("cuenta"), Cancion.id)
    ).all()
    return {
        "evento": _json(evento, sesion),
        "gustos": panorama.gustos(sesion, evento.id),
        "gasto_ia": tandas.gasto(sesion, evento.id),
        "canciones": [
            {
                "posicion": i,
                "id": c.proveedor_id,
                "proveedor": c.proveedor,
                "titulo": c.titulo,
                "artista": c.artista,
                "votos": cuenta,
                "uri": f"spotify:track:{c.spotify_id or c.proveedor_id}"
                if (c.spotify_id or c.proveedor == "spotify")
                else None,
            }
            for i, (c, cuenta) in enumerate(filas, start=1)
        ],
    }


# ═══════════════════════ el pool de grupos ═══════════════════════
#
# «Pasame los temas de estos grupos» resuelto de punta a punta: se pegan los
# nombres, el sistema sale a buscar sus temas mas conocidos y los deja en el
# repertorio marcados como prioritarios. Desde ahi, la cuota por tanda hace el
# resto.

VIÑETA = re.compile(r"^\s*(?:[*\-–—•]|\d+[\.\)])\s*")


class DispositivoNuevo(BaseModel):
    """El dispositivo que el SDK acaba de crear en el navegador del televisor."""

    dispositivo: str = Field(min_length=8, max_length=120)
    sonar: bool = True


class GruposNuevos(BaseModel):
    texto: str = Field(max_length=TOPE_TEXTO)
    temas_por_grupo: int = Field(default=8, ge=1, le=20)
    reemplazar: bool = False


def _parsear_grupos(texto: str) -> list[tuple[str, str | None]]:
    """Cada linea, en (nombre, nota).

    Acepta lo que se pega de verdad: con viñeta o sin ella, con una nota
    despues del guion --«Foo Fighters — rock alternativo / hard rock»-- o solo
    el nombre. La nota no se interpreta: se guarda para que se vea en /admin.
    """
    salida = []
    for linea in texto.splitlines():
        linea = VIÑETA.sub("", linea).strip()
        if not linea or linea.startswith("#"):
            continue
        partes = SEPARADOR.split(linea, maxsplit=1)
        nombre = partes[0].strip()
        nota = partes[1].strip() if len(partes) > 1 else None
        if nombre:
            salida.append((nombre[:160], nota))
    return salida


@ruteador.put("/eventos/{slug}/grupos")
async def cargar_grupos(
    slug: str, cuerpo: GruposNuevos, sesion: Session = Depends(con_sesion),
) -> dict:
    """Los grupos que tienen que sonar sí o sí, con sus temas.

    Se buscan en Deezer, que da el top de cada artista sin credenciales. Un
    grupo que no aparece no rompe nada: queda en `sin_temas` y los demas
    entran igual.
    """
    evento = sesion.scalar(select(Evento).where(Evento.slug == slug))
    if evento is None:
        raise HTTPException(404, {"error": "evento_inexistente"})

    grupos = _parsear_grupos(cuerpo.texto)
    if not grupos:
        raise HTTPException(422, {"error": "sin_grupos"})

    if cuerpo.reemplazar:
        # Se borra la marca de prioritario y la lista de grupos, NO las
        # canciones: alguien ya pudo haberlas elegido y su voto sigue contando.
        sesion.execute(
            Repertorio.__table__.update()
            .where(Repertorio.evento_id == evento.id)
            .values(prioritario=False)
        )
        sesion.execute(delete(ArtistaPrioritario).where(ArtistaPrioritario.evento_id == evento.id))
        sesion.commit()

    proveedor = Deezer()
    entraron_total, sin_temas, detalle = 0, [], []
    ahora = datetime.now(timezone.utc)

    for nombre, nota in grupos:
        temas = await proveedor.top_del_artista(nombre, limite=cuerpo.temas_por_grupo)
        if not temas:
            sin_temas.append(nombre)
            continue
        resultado = importar.al_repertorio(sesion, evento, temas, prioritario=True)
        entraron_total += resultado["entraron"]

        clave = clave_artista(nombre)
        fila = sesion.scalar(
            select(ArtistaPrioritario).where(
                ArtistaPrioritario.evento_id == evento.id,
                ArtistaPrioritario.artista_clave == clave,
            )
        )
        if fila is None:
            fila = ArtistaPrioritario(
                evento_id=evento.id, nombre=nombre, artista_clave=clave, nota=nota
            )
            sesion.add(fila)
        fila.nota = nota or fila.nota
        fila.canciones = resultado["entraron"] + resultado["ya_estaban"]
        fila.ultima_carga = ahora
        sesion.commit()
        detalle.append({"grupo": nombre, "temas": fila.canciones, "nuevos": resultado["entraron"]})
        # Deezer es gratis y sin clave: no se la castiga con treinta pedidos
        # simultaneos.
        await asyncio.sleep(0.25)

    prioritarias = sesion.scalar(
        select(func.count(Repertorio.id)).where(
            Repertorio.evento_id == evento.id, Repertorio.prioritario.is_(True)
        )
    ) or 0
    return {
        "grupos": len(grupos),
        "entraron": entraron_total,
        "sin_temas": sin_temas,
        "detalle": detalle,
        "prioritarias_en_repertorio": prioritarias,
        "por_tanda": evento.prioritarias_por_tanda,
        "total_repertorio": _cuantas(sesion, evento.id),
    }


@ruteador.get("/eventos/{slug}/grupos")
async def ver_grupos(slug: str, sesion: Session = Depends(con_sesion)) -> dict:
    evento = sesion.scalar(select(Evento).where(Evento.slug == slug))
    if evento is None:
        raise HTTPException(404, {"error": "evento_inexistente"})
    filas = sesion.execute(
        select(ArtistaPrioritario)
        .where(ArtistaPrioritario.evento_id == evento.id)
        .order_by(ArtistaPrioritario.nombre)
    ).scalars().all()
    return {
        "por_tanda": evento.prioritarias_por_tanda,
        "grupos": [
            {
                "nombre": f.nombre,
                "nota": f.nota,
                "canciones": f.canciones,
                "ultima_carga": f.ultima_carga.isoformat() if f.ultima_carga else None,
            }
            for f in filas
        ],
    }


# ═══════════════════════ Spotify ═══════════════════════

@ruteador.get("/spotify/estado")
async def spotify_estado(sesion: Session = Depends(con_sesion)) -> dict:
    """Que le falta a Spotify para poder escribir en la playlist.

    Tres cosas y en este orden: credenciales de la app, cuenta autorizada y
    canciones con `spotify_id`. Cada una dice exactamente que hacer.
    """
    cuenta = sesion.scalar(select(SpotifyCuenta).limit(1))
    pendientes = sesion.execute(
        select(SpotifyCola.estado, func.count(SpotifyCola.id)).group_by(SpotifyCola.estado)
    ).all()
    sin_id = sesion.scalar(
        select(func.count(Cancion.id)).where(Cancion.spotify_id.is_(None))
    ) or 0
    con_id = sesion.scalar(
        select(func.count(Cancion.id)).where(Cancion.spotify_id.is_not(None))
    ) or 0
    # Que esta sonando: es lo que decide si la cola puede entregar. Sin un
    # dispositivo activo, Spotify no acepta nada en la cola -- y eso no es un
    # error, es que nadie esta escuchando.
    reproduccion = {}
    if cuenta is not None and cliente.configurado():
        try:
            acceso = await cliente.token_usuario(cuenta.refresh_token)
            reproduccion = await cliente.estado_reproduccion(acceso)
            reproduccion["dispositivos"] = [
                {"nombre": d.get("name"), "activo": bool(d.get("is_active"))}
                for d in await cliente.dispositivos(acceso)
            ]
        except Exception as error:  # noqa: BLE001
            reproduccion = {"error": str(error)[:160]}

    return {
        "credenciales": cliente.configurado(),
        "reproduccion": reproduccion,
        "cuenta": cuenta.usuario if cuenta else None,
        "conectado": cuenta is not None,
        "modo": config.SPOTIFY_ADD_MODE,
        "votos_minimos": config.SPOTIFY_VOTOS_MINIMOS,
        "canciones_con_spotify_id": con_id,
        "canciones_sin_spotify_id": sin_id,
        "cola": {estado: cuantas for estado, cuantas in pendientes},
        "que_falta": (
            "Crear la app en developer.spotify.com y poner SPOTIFY_CLIENT_ID y "
            "SPOTIFY_CLIENT_SECRET en el .env"
            if not cliente.configurado()
            else "Conectar la cuenta con el botón de abajo"
            if cuenta is None
            else None
        ),
    }


@ruteador.get("/spotify/conectar")
async def spotify_conectar(pedido: Request) -> RedirectResponse:
    """Manda a Spotify a pedir el consentimiento. Vuelve al callback."""
    if not cliente.configurado():
        raise HTTPException(409, {"error": "sin_credenciales"})
    esquema = pedido.headers.get("x-forwarded-proto", pedido.url.scheme)
    anfitrion = pedido.headers.get("x-forwarded-host") or pedido.headers.get("host", "")
    redireccion = f"{esquema}://{anfitrion}/api/admin/spotify/callback"
    parametros = urlencode(
        {
            "client_id": ajustes().spotify_id,
            "response_type": "code",
            "redirect_uri": redireccion,
            "state": cliente.nuevo_estado(),
            "scope": cliente.SCOPES,
            # Fuerza la pantalla de consentimiento: si la cuenta ya habia
            # autorizado antes, sin esto Spotify devuelve el codigo sin
            # refresh_token y la conexion queda a medias.
            "show_dialog": "true",
        }
    )
    return RedirectResponse(f"{cliente.AUTORIZAR}?{parametros}")


@ruteador.get("/spotify/enlace")
async def spotify_enlace(pedido: Request) -> dict:
    """La URL de autorizacion, para abrirla donde sea.

    Existe porque el boton de /admin obliga a tener sesion de Authelia EN ESE
    navegador, y conectar Spotify desde el telefono es un caso normal.
    """
    if not cliente.configurado():
        raise HTTPException(409, {"error": "sin_credenciales"})
    esquema = pedido.headers.get("x-forwarded-proto", pedido.url.scheme)
    anfitrion = pedido.headers.get("x-forwarded-host") or pedido.headers.get("host", "")
    parametros = urlencode(
        {
            "client_id": ajustes().spotify_id,
            "response_type": "code",
            "redirect_uri": f"{esquema}://{anfitrion}/api/admin/spotify/callback",
            "state": cliente.nuevo_estado(),
            "scope": cliente.SCOPES,
            "show_dialog": "true",
        }
    )
    return {"url": f"{cliente.AUTORIZAR}?{parametros}"}


@ruteador.delete("/spotify/cuenta")
async def spotify_desconectar(sesion: Session = Depends(con_sesion)) -> dict:
    sesion.execute(delete(SpotifyCuenta))
    sesion.commit()
    cliente.olvidar_token_usuario()
    return {"conectado": False}


@ruteador.post("/eventos/{slug}/spotify/resolver")
async def spotify_resolver(slug: str, sesion: Session = Depends(con_sesion)) -> dict:
    """Le busca el `spotify_id` a las canciones del repertorio.

    Sin esto no hay nada que agregar a ninguna playlist: el repertorio curado
    tiene identidad propia, no la de Spotify. Corre a mano y fuera de la
    fiesta -- son cientos de pedidos y no hay apuro.
    """
    evento = sesion.scalar(select(Evento).where(Evento.slug == slug))
    if evento is None:
        raise HTTPException(404, {"error": "evento_inexistente"})
    if not cliente.configurado():
        raise HTTPException(409, {"error": "sin_credenciales"})

    filas = sesion.execute(
        select(Cancion)
        .join(Repertorio, Repertorio.cancion_id == Cancion.id)
        .where(Repertorio.evento_id == evento.id, Cancion.spotify_id.is_(None))
    ).scalars().all()

    resueltas, sin_encontrar = 0, []
    for cancion in filas:
        pista = await cliente.buscar_pista(cancion.titulo, cancion.artista)
        if pista:
            cancion.spotify_id = pista["id"]
            if not cancion.imagen:
                imagenes = (pista.get("album") or {}).get("images") or []
                cancion.imagen = imagenes[-1]["url"] if imagenes else None
            resueltas += 1
        else:
            sin_encontrar.append(f"{cancion.titulo} — {cancion.artista}")
        await asyncio.sleep(0.1)
    sesion.commit()
    return {"revisadas": len(filas), "resueltas": resueltas,
            "sin_encontrar": sin_encontrar[:40]}


@ruteador.post("/eventos/{slug}/spotify/sincronizar")
async def spotify_sincronizar(slug: str, sesion: Session = Depends(con_sesion)) -> dict:
    """Encola lo ya elegido que todavia no esta en la playlist.

    Sirve para el dia que se conecta Spotify a mitad de fiesta: lo elegido
    antes no se pierde.
    """
    evento = sesion.scalar(select(Evento).where(Evento.slug == slug))
    if evento is None:
        raise HTTPException(404, {"error": "evento_inexistente"})
    encoladas = cola.encolar_lo_elegido(sesion, evento)
    procesadas = await cola.procesar(sesion, limite=50)
    return {"encoladas": encoladas, **procesadas}


@ruteador.get("/spotify/token-reproductor")
async def spotify_token_reproductor(sesion: Session = Depends(con_sesion)) -> dict:
    """El token con el que la PANTALLA se vuelve un parlante de Spotify.

    ESTA RUTA ES LA QUE HACE QUE ESTO SEA SEGURO. El Web Playback SDK necesita
    un token de usuario dentro del navegador, y la pagina del televisor es
    publica: cualquiera con el enlace la abre. Por eso el token no viaja en la
    pagina --viaja por aca, bajo /api/admin/, que el proxy solo deja pasar con
    sesion de Authelia.

    El resultado es que la MISMA url se comporta distinto segun quien la abra:
    quien tiene sesion --la persona que puso la pantalla en el televisor--
    recibe el token y el salon suena desde ahi; cualquier otro recibe 401 y ve
    exactamente lo de antes, la pantalla que muestra y no reproduce.
    """
    cuenta = sesion.scalar(select(SpotifyCuenta).limit(1))
    if cuenta is None or not cliente.configurado():
        raise HTTPException(409, {"error": "spotify_no_conectado"})

    faltan = [s for s in cliente.SCOPES_REPRODUCTOR if s not in (cuenta.scopes or "")]
    if faltan:
        # Explicito y no un fallo del SDK: la cuenta se conecto antes de que
        # existieran estos permisos y hay que volver a autorizar UNA vez.
        raise HTTPException(409, {"error": "faltan_permisos", "scopes": faltan})

    try:
        acceso = await cliente.token_usuario(cuenta.refresh_token)
    except Exception as error:  # noqa: BLE001
        raise HTTPException(502, {"error": "token", "detalle": str(error)[:160]})
    return {"token": acceso}


@ruteador.put("/spotify/dispositivo")
async def spotify_dispositivo(
    cuerpo: DispositivoNuevo, sesion: Session = Depends(con_sesion),
) -> dict:
    """Manda el sonido al dispositivo que acaba de nacer en el televisor.

    Lo hace el servidor y no el navegador: asi el token no tiene que usarse
    para nada mas que inicializar el SDK, y toda escritura a Spotify sigue
    saliendo de un solo lugar.
    """
    cuenta = sesion.scalar(select(SpotifyCuenta).limit(1))
    if cuenta is None:
        raise HTTPException(409, {"error": "spotify_no_conectado"})
    acceso = await cliente.token_usuario(cuenta.refresh_token)
    try:
        await cliente.transferir(acceso, cuerpo.dispositivo, cuerpo.sonar)
    except Exception as error:  # noqa: BLE001
        raise HTTPException(502, {"error": "spotify", "detalle": str(error)[:200]})
    return {"ok": True}


@ruteador.post("/eventos/{slug}/spotify/reproducir")
async def spotify_reproducir(slug: str, sesion: Session = Depends(con_sesion)) -> dict:
    """Pone a sonar el ranking del evento, de la mas elegida a la menos.

    SIN PLAYLIST: `PUT /me/player/play` acepta la lista de URIs directamente, y
    es el camino que Spotify nos dejo abierto. Es disruptivo --reemplaza lo que
    este sonando-- y por eso es un boton y no un job.
    """
    evento = sesion.scalar(select(Evento).where(Evento.slug == slug))
    if evento is None:
        raise HTTPException(404, {"error": "evento_inexistente"})
    try:
        resultado = await reproduccion.sonar_por_votos(sesion, evento)
    except Exception as error:  # noqa: BLE001
        raise HTTPException(502, {"error": "spotify", "detalle": str(error)[:200]})
    if not resultado.get("ok"):
        raise HTTPException(422, {"error": resultado.get("motivo", "no se pudo")})
    return resultado


@ruteador.get("/eventos/{slug}/sonando")
async def spotify_sonando(slug: str, sesion: Session = Depends(con_sesion)) -> dict:
    """Lo mismo que ve el televisor, para el panel."""
    evento = sesion.scalar(select(Evento).where(Evento.slug == slug))
    if evento is None:
        raise HTTPException(404, {"error": "evento_inexistente"})
    return await reproduccion.leer(sesion, evento)


@ruteador.get("/spotify/cola")
async def spotify_cola(sesion: Session = Depends(con_sesion)) -> dict:
    filas = sesion.execute(
        select(SpotifyCola, Cancion)
        .join(Cancion, Cancion.id == SpotifyCola.cancion_id)
        .order_by(desc(SpotifyCola.id))
        .limit(60)
    ).all()
    return {
        "cola": [
            {
                "titulo": c.titulo,
                "artista": c.artista,
                "estado": f.estado,
                "intentos": f.intentos,
                "error": (f.ultimo_error or "")[:160] or None,
                "enviado": f.enviado_en.isoformat() if f.enviado_en else None,
            }
            for f, c in filas
        ]
    }


# ═══════════════════════ inteligencia musical ═══════════════════════

@ruteador.get("/inteligencia")
async def inteligencia(slug: str = "", sesion: Session = Depends(con_sesion)) -> dict:
    """El dashboard musical: catalogo, metricas, fuentes y perfil del evento.

    Todo sale de consultas agrupadas sobre indices. Es una pantalla que se
    mira entre fiestas, no durante: no hay caches ni materializaciones porque
    no hacen falta.
    """
    evento = None
    if slug:
        evento = sesion.scalar(select(Evento).where(Evento.slug == slug))

    por_estado = dict(
        sesion.execute(
            select(CatalogoMusical.estado, func.count(CatalogoMusical.id))
            .group_by(CatalogoMusical.estado)
        ).all()
    )
    por_genero = dict(
        sesion.execute(
            select(CatalogoMusical.genero, func.count(CatalogoMusical.id))
            .where(CatalogoMusical.estado.in_(ACTIVOS))
            .group_by(CatalogoMusical.genero)
            .order_by(desc(func.count(CatalogoMusical.id)))
        ).all()
    )
    activas = sum(por_estado.get(e, 0) for e in ACTIVOS)
    paraguayas = sesion.scalar(
        select(func.count(CatalogoMusical.id)).where(
            CatalogoMusical.es_paraguaya.is_(True), CatalogoMusical.estado.in_(ACTIVOS)
        )
    ) or 0
    tendencia = sesion.execute(
        select(Cancion.titulo, Cancion.artista, CatalogoMusical.popularidad_py,
               CatalogoMusical.estado)
        .join(CatalogoMusical, CatalogoMusical.cancion_id == Cancion.id)
        .where(CatalogoMusical.es_tendencia.is_(True))
        .order_by(desc(CatalogoMusical.popularidad_py), desc(CatalogoMusical.score_actual))
        .limit(15)
    ).all()

    fuentes = sesion.execute(select(FuenteEstado).order_by(FuenteEstado.nombre)).scalars().all()
    fatigadas = metricas.fatiga(sesion, evento.id) if evento else {}
    titulos_fatiga = []
    if fatigadas:
        canciones = sesion.execute(
            select(Cancion).where(Cancion.id.in_(fatigadas.keys()))
        ).scalars().all()
        titulos_fatiga = sorted(
            [
                {"titulo": c.titulo, "artista": c.artista,
                 "castigo": round(fatigadas[c.id], 2)}
                for c in canciones
            ],
            key=lambda f: -f["castigo"],
        )[:15]

    perfil = sesion.get(EventoPerfil, evento.id) if evento else None
    return {
        "catalogo": {
            "activas": activas,
            "minimo": config.MIN_CATALOGO_ACTIVO,
            "objetivo": config.OBJETIVO_CATALOGO_ACTIVO,
            "alcanza": activas >= config.MIN_CATALOGO_ACTIVO,
            "paraguayas": paraguayas,
            "piso_paraguay": config.PISO_PARAGUAY,
            "por_estado": por_estado,
            "por_genero": {(g or "sin etiquetar"): c for g, c in por_genero.items()},
            "cuotas": config.CUOTAS,
        },
        "tendencia_py": [
            {"titulo": t, "artista": a, "popularidad": round(p or 0, 3), "estado": e}
            for t, a, p, e in tendencia
        ],
        "fuentes": [
            {
                "nombre": f.nombre,
                "estado": f.estado,
                "ultima": f.ultima_ejecucion.isoformat() if f.ultima_ejecucion else None,
                "encontradas": f.encontradas,
                "nuevas": f.nuevas,
                "ms": f.duracion_ms,
                "error": (f.ultimo_error or "")[:200] or None,
            }
            for f in fuentes
        ],
        "metricas": metricas.ranking(sesion, evento.id if evento else None, tope=12),
        "fatigadas": titulos_fatiga,
        "perfil": None if perfil is None else {
            "elecciones": perfil.elecciones,
            "generos": perfil.generos,
            "decadas": perfil.decadas,
            "idiomas": perfil.idiomas,
            "artistas": dict(list(perfil.artistas.items())[:12]),
            "rechazados": perfil.rechazados,
            "intensidad": perfil.intensidad_promedio,
        },
        "explorar": {
            "explotar": evento.explotar_pct if evento else int(config.EXPLOTAR * 100),
            "explorar": (100 - evento.explotar_pct) if evento else int(config.EXPLORAR * 100),
        },
    }


@ruteador.post("/jobs/{nombre}")
async def correr_job(nombre: str) -> dict:
    """Dispara un job a mano, sin esperar al planificador.

    Sirve para probar una fuente y para forzar un refill antes de una fiesta.
    Corre en el mismo pedido: es /admin detras de Authelia, no el camino
    caliente del juego.
    """
    from app.jobs.planificador import catalogo as jobs_disponibles

    disponibles = {job.nombre: job for job in jobs_disponibles()}
    if nombre not in disponibles:
        raise HTTPException(404, {"error": "job_inexistente", "validos": list(disponibles)})
    resultado = disponibles[nombre].correr()
    if asyncio.iscoroutine(resultado):
        resultado = await resultado
    return {"job": nombre, "resultado": resultado}


@ruteador.get("/eventos/{slug}/spotify/enlaces")
async def spotify_enlaces(slug: str, sesion: Session = Depends(con_sesion)) -> dict:
    """La lista para armar la playlist a mano, ordenada por votos.

    Existe porque Spotify bloquea el endpoint de contenido de playlists para
    las apps en «Development mode»: la cola de envio automatico esta lista y
    probada, pero hasta que Spotify apruebe la extension de cuota no hay forma
    de escribir en ninguna playlist. Esto es el puente.

    Devuelve los URI --que es lo que Spotify entiende al pegar-- y tambien
    «Tema — Artista» por si hay que buscarlos a mano.
    """
    evento = sesion.scalar(select(Evento).where(Evento.slug == slug))
    if evento is None:
        raise HTTPException(404, {"error": "evento_inexistente"})

    filas = sesion.execute(
        select(Cancion, func.count(Solicitud.id).label("votos"))
        .join(Solicitud, Solicitud.cancion_id == Cancion.id)
        .where(Solicitud.evento_id == evento.id)
        .group_by(Cancion.id)
        .order_by(desc("votos"), Cancion.id)
    ).all()

    con_id = [(c, v) for c, v in filas if c.spotify_id]
    sin_id = [(c, v) for c, v in filas if not c.spotify_id]
    return {
        "canciones": len(filas),
        "uris": [f"spotify:track:{c.spotify_id}" for c, _ in con_id],
        "enlaces": [f"https://open.spotify.com/track/{c.spotify_id}" for c, _ in con_id],
        "lista": [
            {"votos": v, "titulo": c.titulo, "artista": c.artista,
             "uri": f"spotify:track:{c.spotify_id}"}
            for c, v in con_id
        ],
        "sin_spotify": [f"{c.titulo} — {c.artista}" for c, _ in sin_id],
    }
