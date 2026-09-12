"""Lo unico del descubrimiento que escribe en la base.

Los proveedores devuelven datos y no saben que existe PostgreSQL; aca se
decide que entra, como se deduplica contra lo que ya hay y en que estado nace.

DOS DESTINOS Y NO SE CONFUNDEN:

  · `al_catalogo`  -> `catalogo_musical`. Lo que tinker CONOCE. No se juega.
  · `al_repertorio` -> `repertorio` de un evento. Lo que se PUEDE elegir hoy.

Una canción descubierta automaticamente entra al catalogo y espera. Pasa al
repertorio cuando alguien lo pide o cuando el refill lo decide.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.catalogo import clave_artista, id_curado
from app.descubrimiento.base import CancionDescubierta
from app.descubrimiento.deduplicar import deduplicar
from app.descubrimiento.normalizar import normalizar
from app.modelos import Cancion, CatalogoMusical, Evento, Repertorio

registro = logging.getLogger("tinker.importar")

# Paises cuyas canciones se marcan como latinas / brasileras. Es una etiqueta
# gruesa y a proposito: sirve para cuotas, no para musicologia.
LATINOS = {"py", "ar", "uy", "cl", "pe", "bo", "co", "mx", "es", "ve", "ec"}


def _de_chart(cruda: CancionDescubierta) -> bool:
    """Si la canción viene de un CHART --lo que suena ahora-- y no del top
    historico de un artista ni de una busqueda por genero."""
    return bool(cruda.extra.get("chart")) and (cruda.ranking or 999) <= 50


def _anio_de(cruda: CancionDescubierta) -> int | None:
    if cruda.anio:
        return cruda.anio
    if cruda.fecha:
        return cruda.fecha.year
    return None


def _epoca_de(anio: int | None) -> str | None:
    """La decada, en dos digitos. 1972 -> "70", 2015 -> "10".

    Estuvo mal desde el primer dia: `anio // 10 % 100` no es la decada sino los
    tres primeros digitos del año modulo cien, asi que 1972 daba "97" y 1985
    daba "98" -- valores que NO existen en `EPOCAS`, de modo que la etiqueta se
    guardaba corrupta y ninguna comparacion por epoca podia dar verdadera.
    Hay que dividir, redondear a la decada y RECIEN despues tomar dos digitos.
    """
    if not anio or anio < 1950:
        return None
    return f"{(anio // 10 * 10) % 100:02d}"


def cancion_de(sesion: Session, cruda: CancionDescubierta) -> Cancion:
    """La fila de identidad, creandola si hace falta.

    La identidad se acuña con el titulo y el artista NORMALIZADOS, no con los
    crudos: asi "Everlong - Remastered 2011" no crea una segunda fila al lado
    de "Everlong".
    """
    titulo, artista, _, _ = normalizar(cruda)
    proveedor_id = id_curado(titulo, artista)
    cancion = sesion.scalar(
        select(Cancion).where(Cancion.proveedor == "curado", Cancion.proveedor_id == proveedor_id)
    )
    if cancion is not None:
        # Una fuente con caratula mejora una fila que no la tenia. No se pisan
        # titulo ni artista: la identidad no cambia.
        if cruda.imagen and not cancion.imagen:
            cancion.imagen = cruda.imagen
        return cancion

    cancion = Cancion(
        proveedor="curado",
        proveedor_id=proveedor_id,
        titulo=titulo,
        artista=artista,
        imagen=cruda.imagen,
    )
    sesion.add(cancion)
    try:
        sesion.flush()
    except IntegrityError:
        sesion.rollback()
        cancion = sesion.scalar(
            select(Cancion).where(
                Cancion.proveedor == "curado", Cancion.proveedor_id == proveedor_id
            )
        )
    return cancion


def al_catalogo(sesion: Session, descubiertas: list[CancionDescubierta]) -> dict:
    """Incorpora lo descubierto al catalogo global. Nunca al juego."""
    limpias = deduplicar(descubiertas)
    ahora = datetime.now(timezone.utc)
    nuevas, actualizadas = 0, 0

    for cruda in limpias:
        cancion = cancion_de(sesion, cruda)
        if cancion is None:
            continue
        _, _, version, _ = normalizar(cruda)
        fila = sesion.scalar(
            select(CatalogoMusical).where(CatalogoMusical.cancion_id == cancion.id)
        )
        anio = _anio_de(cruda)
        es_py = (cruda.pais or "").lower() == "py"

        if fila is None:
            fila = CatalogoMusical(
                cancion_id=cancion.id,
                fuente_principal=cruda.fuente,
                pais=cruda.pais,
                genero=cruda.genero_estimado,
                anio=anio,
                epoca=_epoca_de(anio),
                version=version,
                es_paraguaya=es_py,
                es_latina=(cruda.pais or "").lower() in LATINOS,
                es_brasilera=(cruda.pais or "").lower() == "br"
                or cruda.genero_estimado == "brasilera",
                # SOLO desde un chart. El ranking del top de un artista es su
                # orden interno --«Everlong es el tema 1 de Foo Fighters»-- y
                # no dice nada de lo que suena esta semana. Sin esta
                # distincion, 402 de 405 canciones quedaron marcadas como
                # tendencia y la señal dejo de significar algo.
                es_tendencia=_de_chart(cruda),
                estado="NEW",
            )
            sesion.add(fila)
            nuevas += 1
        else:
            actualizadas += 1
            fila.ultima_deteccion = ahora
            # Volver a aparecer en un chart es informacion: la canción sigue
            # sonando. Que DEJE de aparecer tambien lo es, y de eso se encarga
            # el job de rotacion.
            if _de_chart(cruda):
                fila.es_tendencia = True
            if cruda.genero_estimado and not fila.genero:
                fila.genero = cruda.genero_estimado
            if es_py:
                fila.es_paraguaya = True

        if cruda.popularidad is not None:
            if es_py:
                fila.popularidad_py = max(fila.popularidad_py or 0, cruda.popularidad)
            else:
                fila.popularidad_global = max(fila.popularidad_global or 0, cruda.popularidad)

    sesion.commit()
    return {"recibidas": len(descubiertas), "unicas": len(limpias),
            "nuevas": nuevas, "ya_estaban": actualizadas}


def al_repertorio(
    sesion: Session,
    evento: Evento,
    descubiertas: list[CancionDescubierta],
    prioritario: bool = False,
    genero: str | None = None,
) -> dict:
    """Incorpora canciones al repertorio de UN evento: lo que se puede elegir.

    Pasa tambien por el catalogo global: una canción que se juega es una
    canción que tinker conoce, y separar eso obligaria a mantener dos verdades.
    """
    limpias = deduplicar(descubiertas)
    al_catalogo(sesion, limpias)

    orden = sesion.scalar(
        select(func.coalesce(func.max(Repertorio.orden), 0)).where(
            Repertorio.evento_id == evento.id
        )
    ) or 0
    entraron, ya_estaban = 0, 0

    for cruda in limpias:
        cancion = cancion_de(sesion, cruda)
        if cancion is None:
            continue
        existente = sesion.scalar(
            select(Repertorio).where(
                Repertorio.evento_id == evento.id, Repertorio.cancion_id == cancion.id
            )
        )
        if existente is not None:
            ya_estaban += 1
            # Un tema que ya estaba y ahora llega por un grupo prioritario
            # ASCIENDE. Al reves no: quitar la prioridad es una decision de
            # quien organiza, no un efecto de que el tema aparezca en un chart.
            if prioritario and not existente.prioritario:
                existente.prioritario = True
            continue

        catalogo = sesion.scalar(
            select(CatalogoMusical).where(CatalogoMusical.cancion_id == cancion.id)
        )
        orden += 1
        sesion.add(
            Repertorio(
                evento_id=evento.id,
                cancion_id=cancion.id,
                orden=orden,
                linea=f"{cancion.titulo} — {cancion.artista}",
                artista_clave=clave_artista(cancion.artista),
                genero=genero or cruda.genero_estimado or (catalogo.genero if catalogo else None),
                epoca=catalogo.epoca if catalogo else None,
                prioritario=prioritario,
            )
        )
        entraron += 1

    sesion.commit()
    return {"entraron": entraron, "ya_estaban": ya_estaban, "unicas": len(limpias)}
