"""Lo que ve el televisor: totales, ranking, ultimas y gustos.

Todo se cuenta en el momento con cuatro consultas. Para un evento de mil
pedidos eso son milisegundos, y evita el problema real de un contador
guardado: que quede mintiendo.
"""
from __future__ import annotations

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.modelos import Cancion, Evento, Participante, Repertorio, Solicitud
from app.recomendador import publico

TOPE_RANKING = 10
TOPE_RECIENTES = 8


def votos(sesion: Session, evento_id: int, cancion_id: int) -> int:
    return sesion.scalar(
        select(func.count(Solicitud.id)).where(
            Solicitud.evento_id == evento_id, Solicitud.cancion_id == cancion_id
        )
    ) or 0


def _cancion_json(cancion: Cancion, cuenta: int | None = None) -> dict:
    datos = {
        "id": cancion.proveedor_id,
        "proveedor": cancion.proveedor,
        "titulo": cancion.titulo,
        "artista": cancion.artista,
        "album": cancion.album,
        "imagen": cancion.imagen,
    }
    if cuenta is not None:
        datos["votos"] = cuenta
    return datos


def totales(sesion: Session, evento_id: int) -> dict:
    personas = sesion.scalar(
        select(func.count(Participante.id)).where(Participante.evento_id == evento_id)
    ) or 0
    pedidos = sesion.scalar(
        select(func.count(Solicitud.id)).where(Solicitud.evento_id == evento_id)
    ) or 0
    canciones = sesion.scalar(
        select(func.count(func.distinct(Solicitud.cancion_id))).where(
            Solicitud.evento_id == evento_id
        )
    ) or 0
    return {"personas": personas, "pedidos": pedidos, "canciones": canciones}


def ranking(sesion: Session, evento_id: int, tope: int = TOPE_RANKING) -> list[dict]:
    filas = sesion.execute(
        select(Cancion, func.count(Solicitud.id).label("cuenta"))
        .join(Solicitud, Solicitud.cancion_id == Cancion.id)
        .where(Solicitud.evento_id == evento_id)
        .group_by(Cancion.id)
        # El desempate por id no es capricho: sin el, dos canciones con los
        # mismos votos se intercambian de lugar en cada refresco y el top
        # parpatea en la pantalla sin que nada haya cambiado.
        .order_by(desc("cuenta"), Cancion.id)
        .limit(tope)
    ).all()
    return [_cancion_json(cancion, cuenta) for cancion, cuenta in filas]


def recientes(sesion: Session, evento_id: int, tope: int = TOPE_RECIENTES) -> list[dict]:
    filas = sesion.execute(
        select(Cancion, Solicitud.creado)
        .join(Solicitud, Solicitud.cancion_id == Cancion.id)
        .where(Solicitud.evento_id == evento_id)
        .order_by(desc(Solicitud.creado), desc(Solicitud.id))
        .limit(tope)
    ).all()
    return [_cancion_json(cancion) | {"cuando": creado.isoformat()} for cancion, creado in filas]


def artistas(sesion: Session, evento_id: int, tope: int = 5) -> list[dict]:
    filas = sesion.execute(
        select(Cancion.artista, func.count(Solicitud.id).label("cuenta"))
        .join(Solicitud, Solicitud.cancion_id == Cancion.id)
        .where(Solicitud.evento_id == evento_id)
        .group_by(Cancion.artista)
        .order_by(desc("cuenta"), Cancion.artista)
        .limit(tope)
    ).all()
    return [{"artista": artista, "votos": cuenta} for artista, cuenta in filas]


def gustos(sesion: Session, evento_id: int) -> list[dict]:
    """Los generos de lo que la gente ELIGIO, no de lo que declaro.

    Antes salia de una encuesta previa de once pastillas. Sale de las
    elecciones desde que el telefono pasó a ser un juego, y es mejor dato: lo
    que alguien toca en una fiesta dice mas que lo que marca en un formulario
    antes de empezar.
    """
    filas = sesion.execute(
        select(Repertorio.genero, func.count(Solicitud.id).label("cuenta"))
        .join(Solicitud, Solicitud.cancion_id == Repertorio.cancion_id)
        .where(
            Solicitud.evento_id == evento_id,
            Repertorio.evento_id == evento_id,
            Repertorio.genero.is_not(None),
        )
        .group_by(Repertorio.genero)
        .order_by(desc("cuenta"), Repertorio.genero)
    ).all()
    total = sum(cuenta for _, cuenta in filas) or 1
    return [
        {"genero": genero, "cuenta": cuenta, "porcentaje": round(cuenta * 100 / total)}
        for genero, cuenta in filas
    ]


def completo(sesion: Session, evento: Evento) -> dict:
    """El paquete entero que pide la pantalla al abrirse."""
    return {
        "evento": {
            "slug": evento.slug,
            "nombre": evento.nombre,
            "lugar": evento.lugar,
            "estado": evento.estado,
            "playlist_url": evento.playlist_url,
        },
        "totales": totales(sesion, evento.id),
        "ranking": ranking(sesion, evento.id),
        "recientes": recientes(sesion, evento.id),
        "artistas": artistas(sesion, evento.id),
        "gustos": gustos(sesion, evento.id),
        # El perfil PONDERADO, que es lo que la pantalla muestra moviendose.
        # Se lee guardado y no se recalcula: en cada eleccion ya se refresco, y
        # la pantalla pide el panorama entero cada vez que alguien se conecta.
        "perfil": publico.leer(sesion, evento.id),
    }
