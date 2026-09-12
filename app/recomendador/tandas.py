"""Leer y escribir tandas. La unica capa del recomendador que toca la base."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import config
from app.modelos import (
    Cancion,
    Exposicion,
    Repertorio,
    Solicitud,
    Tanda,
    TandaCancion,
)
from app.recomendador.pozo import Candidata

registro = logging.getLogger("tinker.tandas")


def leer(sesion: Session, evento_id: int, participante_id: int, ronda: int) -> list[Candidata] | None:
    """Las cinco tarjetas de esa ronda, en el orden en que se sirvieron, o None.

    El orden importa: si al recargar la pagina las tarjetas salieran barajadas
    de nuevo, la persona creeria que son otras canciones.
    """
    filas = sesion.execute(
        select(TandaCancion, Cancion, Repertorio)
        .join(Tanda, Tanda.id == TandaCancion.tanda_id)
        .join(Cancion, Cancion.id == TandaCancion.cancion_id)
        .outerjoin(
            Repertorio,
            (Repertorio.cancion_id == Cancion.id) & (Repertorio.evento_id == evento_id),
        )
        .where(Tanda.participante_id == participante_id, Tanda.ronda == ronda)
        .order_by(TandaCancion.posicion)
    ).all()
    if not filas:
        return None
    return [
        Candidata(
            cancion_id=cancion.id,
            proveedor_id=cancion.proveedor_id,
            titulo=cancion.titulo,
            artista=cancion.artista,
            artista_clave=rep.artista_clave if rep else "",
            imagen=cancion.imagen,
            genero=rep.genero if rep else None,
            epoca=rep.epoca if rep else None,
            idioma=rep.idioma if rep else None,
            intensidad=rep.intensidad if rep else None,
        )
        for _, cancion, rep in filas
    ]


def guardar(
    sesion: Session,
    evento_id: int,
    participante_id: int,
    ronda: int,
    canciones: list[Candidata],
    origen: str,
    uso: dict | None = None,
    porque: str | None = None,
    ms: int | None = None,
    mensaje: str | None = None,
    lectura: dict | None = None,
) -> bool:
    """Escribe la tanda. False si otro la escribio primero.

    Perder la carrera NO es un error: quiere decir que la tanda ya estaba, que
    es exactamente lo que se queria. Pasa siempre que el prefetch y el poll
    llegan juntos, y es el mismo criterio que ya usa `participante_de`.
    """
    uso = uso or {}
    tanda = Tanda(
        evento_id=evento_id,
        participante_id=participante_id,
        ronda=ronda,
        origen=origen,
        proveedor=uso.get("proveedor"),
        modelo=uso.get("modelo"),
        tokens_entrada=uso.get("entrada", 0),
        tokens_salida=uso.get("salida", 0),
        ms=ms,
        porque=porque,
        mensaje=(mensaje or None),
        lectura=lectura,
    )
    sesion.add(tanda)
    try:
        sesion.flush()
        franja = config.franja(datetime.now(timezone.utc).hour)
        for posicion, candidata in enumerate(canciones, start=1):
            sesion.add(
                TandaCancion(
                    tanda_id=tanda.id,
                    posicion=posicion,
                    cancion_id=candidata.cancion_id,
                )
            )
            # La EXPOSICION. `tanda_canciones` dice que tiene una tanda;
            # esto dice que se le mostro a alguien, cuando y en que posicion.
            # Parecen lo mismo y no lo son: sin esta fila no hay conversion, no
            # hay fatiga, y una canción con cero elecciones puede ser una que
            # nadie quiere o una que nadie vio.
            sesion.add(
                Exposicion(
                    evento_id=evento_id,
                    participante_id=participante_id,
                    tanda_id=tanda.id,
                    cancion_id=candidata.cancion_id,
                    ronda=ronda,
                    posicion=posicion,
                    franja=franja,
                )
            )
        sesion.commit()
        return True
    except IntegrityError:
        sesion.rollback()
        # Perder la carrera no es un error, pero SI es un dato: cada vez que
        # esto pasa con origen "modelo", una tanda pagada se tira y la persona
        # ve la determinista. Sin este log no hay forma de saber que fraccion
        # de la noche salio del modelo -- y era invisible.
        registro.info(
            "tanda ya existente: ronda %s de participante %s (se descarta la de origen %s)",
            ronda, participante_id, origen,
        )
        return False


def elecciones(sesion: Session, evento_id: int, participante_id: int) -> list[tuple[int, Cancion]]:
    """Lo que eligio este telefono, en orden de ronda."""
    filas = sesion.execute(
        select(Solicitud.ronda, Cancion)
        .join(Cancion, Cancion.id == Solicitud.cancion_id)
        .where(
            Solicitud.evento_id == evento_id,
            Solicitud.participante_id == participante_id,
            Solicitud.ronda > 0,
        )
        .order_by(Solicitud.ronda)
    ).all()
    return [(ronda, cancion) for ronda, cancion in filas]


def rondas_elegidas(sesion: Session, evento_id: int, participante_id: int) -> set[int]:
    return set(
        sesion.execute(
            select(Solicitud.ronda).where(
                Solicitud.evento_id == evento_id,
                Solicitud.participante_id == participante_id,
                Solicitud.ronda > 0,
            )
        ).scalars().all()
    )


def llamadas_al_modelo(sesion: Session, evento_id: int) -> int:
    """Cuantas tandas de este evento costaron plata. Usa el indice parcial."""
    return sesion.scalar(
        select(func.count(Tanda.id)).where(Tanda.evento_id == evento_id, Tanda.origen == "modelo")
    ) or 0


def gasto(sesion: Session, evento_id: int) -> dict:
    """El libro de gastos de la noche, para /admin. No hay tabla aparte: cada
    tanda con origen 'modelo' ES una llamada, con su proveedor y sus tokens."""
    filas = sesion.execute(
        select(
            Tanda.origen,
            Tanda.proveedor,
            func.count(Tanda.id),
            func.sum(Tanda.tokens_entrada),
            func.sum(Tanda.tokens_salida),
            func.avg(Tanda.ms),
        )
        .where(Tanda.evento_id == evento_id)
        .group_by(Tanda.origen, Tanda.proveedor)
    ).all()
    return {
        "tandas": sum(f[2] for f in filas),
        "detalle": [
            {
                "origen": origen,
                "proveedor": proveedor,
                "tandas": cuantas,
                "tokens_entrada": int(entrada or 0),
                "tokens_salida": int(salida or 0),
                "ms_promedio": int(ms) if ms else None,
            }
            for origen, proveedor, cuantas, entrada, salida, ms in filas
        ],
    }


def resolver_exposiciones(
    sesion: Session,
    tanda_id: int,
    elegida_id: int | None,
    rechazada: bool = False,
    marcadas: set[int] | None = None,
) -> None:
    """Cierra las cinco exposiciones de una tanda.

    La distincion es el corazon del aprendizaje por rechazo:

      · `elegida`      gano.
      · `no_elegida`   perdio contra otra. Señal DEBIL: la canción puede ser
                       excelente y haber estado al lado de una mejor.
      · `rechazada`    la persona dijo que ninguna de las cinco le gustaba.
                       Señal FUERTE, y es informacion que ningun sistema que
                       solo mire elecciones puede tener.
    """
    ahora = datetime.now(timezone.utc)
    filas = sesion.execute(
        select(Exposicion).where(Exposicion.tanda_id == tanda_id)
    ).scalars().all()
    for fila in filas:
        if fila.resultado != "pendiente":
            continue
        if rechazada:
            fila.resultado = "rechazada"
        elif elegida_id is not None and fila.cancion_id == elegida_id:
            fila.resultado = "elegida"
        elif marcadas and fila.cancion_id in marcadas:
            fila.resultado = "elegida"
        else:
            fila.resultado = "no_elegida"
        fila.resuelta_en = ahora
    sesion.commit()


def mensaje_de(sesion: Session, participante_id: int, ronda: int) -> str | None:
    """La linea corta que acompaña esa tanda. Un SELECT de una columna.

    Se lee aparte de `leer` a proposito: `leer` devuelve candidatas y se usa en
    seis lugares, y cambiarle la forma para agregar un string obligaria a tocar
    los seis. Esta consulta pega en la misma fila por el mismo indice.
    """
    return sesion.scalar(
        select(Tanda.mensaje).where(
            Tanda.participante_id == participante_id, Tanda.ronda == ronda
        )
    )


def tanda_id_de(sesion: Session, participante_id: int, ronda: int) -> int | None:
    return sesion.scalar(
        select(Tanda.id).where(Tanda.participante_id == participante_id, Tanda.ronda == ronda)
    )
