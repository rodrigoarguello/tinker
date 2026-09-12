"""Las metricas musicales: que se mostro, que se eligio, que se rechazo.

Todo sale de `exposiciones` con GROUP BY. No hay contadores guardados y es a
proposito: un contador hay que mantenerlo sincronizado con la realidad, y el
dia que se desincroniza nadie se entera.

LA REGLA DE LOS UMBRALES: una canción mostrada tres veces y elegida una no
tiene 33% de conversion -- no tiene conversion todavia. Sin ese piso, las
canciones nuevas ganan cualquier ranking por ratio y las buenas de siempre
parecen malas.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app import config
from app.modelos import Cancion, Exposicion


def _conteos(evento_id: int | None = None):
    """El armazon de la consulta: una fila por canción con sus tres conteos."""
    consulta = select(
        Exposicion.cancion_id,
        func.count(Exposicion.id).label("mostrada"),
        func.sum(case((Exposicion.resultado == "elegida", 1), else_=0)).label("elegida"),
        func.sum(case((Exposicion.resultado == "rechazada", 1), else_=0)).label("rechazada"),
        func.count(func.distinct(Exposicion.participante_id)).label("personas"),
    ).group_by(Exposicion.cancion_id)
    if evento_id is not None:
        consulta = consulta.where(Exposicion.evento_id == evento_id)
    return consulta


def de_cancion(sesion: Session, cancion_id: int, evento_id: int | None = None) -> dict:
    fila = sesion.execute(
        _conteos(evento_id).where(Exposicion.cancion_id == cancion_id)
    ).first()
    if fila is None:
        return {"mostrada": 0, "elegida": 0, "rechazada": 0, "personas": 0,
                "conversion": None, "ratio_rechazo": None}
    return _armar(fila)


def _armar(fila) -> dict:
    mostrada = int(fila.mostrada or 0)
    elegida = int(fila.elegida or 0)
    rechazada = int(fila.rechazada or 0)
    confiable = mostrada >= config.MINIMO_EXPOSICIONES
    return {
        "cancion_id": fila.cancion_id,
        "mostrada": mostrada,
        "elegida": elegida,
        "rechazada": rechazada,
        "personas": int(fila.personas or 0),
        # None y no 0.0 cuando no hay muestra: son cosas distintas y mezclarlas
        # es como ordenar un ranking por datos que no existen.
        "conversion": round(elegida / mostrada, 3) if confiable else None,
        "ratio_rechazo": round(rechazada / mostrada, 3) if confiable else None,
        "confiable": confiable,
    }


def todas(sesion: Session, evento_id: int | None = None) -> dict[int, dict]:
    return {
        fila.cancion_id: _armar(fila)
        for fila in sesion.execute(_conteos(evento_id)).all()
    }


def fatiga(sesion: Session, evento_id: int) -> dict[int, float]:
    """Cuanto castigo tiene cada canción por haber aparecido demasiado.

    Una canción puede tener conversion excelente y haber salido seis veces en
    la ultima media hora. Su score baja UN RATO --y se recupera solo, porque la
    ventana se corre con el reloj y no hay nada que resetear--.
    """
    desde = datetime.now(timezone.utc) - timedelta(minutes=config.FATIGA_VENTANA_MIN)
    filas = sesion.execute(
        select(Exposicion.cancion_id, func.count(Exposicion.id))
        .where(Exposicion.evento_id == evento_id, Exposicion.mostrada_en >= desde)
        .group_by(Exposicion.cancion_id)
    ).all()
    castigos = {}
    for cancion_id, veces in filas:
        exceso = max(0, int(veces) - config.FATIGA_TOLERANCIA)
        if exceso:
            castigos[cancion_id] = min(config.FATIGA_TOPE, exceso * config.FATIGA_PESO)
    return castigos


def ranking(sesion: Session, evento_id: int | None = None, tope: int = 20) -> dict:
    """Los cuadros del dashboard. Una sola pasada por las metricas."""
    metricas = todas(sesion, evento_id)
    if not metricas:
        return {"mas_mostradas": [], "mas_elegidas": [], "mas_rechazadas": [],
                "mejor_conversion": [], "peor_conversion": []}

    titulos = {
        c.id: {"titulo": c.titulo, "artista": c.artista}
        for c in sesion.execute(
            select(Cancion).where(Cancion.id.in_(metricas.keys()))
        ).scalars().all()
    }

    def vestir(filas):
        return [{**titulos.get(f["cancion_id"], {}), **f} for f in filas]

    valores = list(metricas.values())
    confiables = [f for f in valores if f["confiable"]]
    return {
        "mas_mostradas": vestir(sorted(valores, key=lambda f: -f["mostrada"])[:tope]),
        "mas_elegidas": vestir(sorted(valores, key=lambda f: -f["elegida"])[:tope]),
        "mas_rechazadas": vestir(sorted(valores, key=lambda f: -f["rechazada"])[:tope]),
        "mejor_conversion": vestir(
            sorted(confiables, key=lambda f: -(f["conversion"] or 0))[:tope]
        ),
        "peor_conversion": vestir(
            sorted(confiables, key=lambda f: (f["conversion"] or 0))[:tope]
        ),
        "minimo_exposiciones": config.MINIMO_EXPOSICIONES,
        "canciones_medidas": len(valores),
        "canciones_confiables": len(confiables),
    }
