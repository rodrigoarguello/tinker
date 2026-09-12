"""De donde salen las candidatas de una tanda.

Tres consultas y ninguna mas en todo el recomendador: el repertorio del
evento, lo que este telefono ya vio, y lo que eligio. Con eso se arma el
`Pozo`, que es lo unico que reciben las reglas y el modelo -- ninguno de los
dos vuelve a la base.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.recomendador.publico import PerfilPublico
from app.modelos import (
    Cancion,
    Evento,
    EventoPerfil,
    Exposicion,
    Participante,
    Rechazo,
    Repertorio,
    Solicitud,
)

registro = logging.getLogger("tinker.pozo")

TAMANO_MINIMO = 5


@dataclass(frozen=True)
class Candidata:
    cancion_id: int
    proveedor_id: str
    titulo: str
    artista: str
    artista_clave: str
    imagen: str | None = None
    genero: str | None = None
    epoca: str | None = None
    idioma: str | None = None
    intensidad: int | None = None
    # 0..1, de Spotify. Es el mainstream-vs-alternativo, y llega hasta aca
    # porque el pozo se arma de `repertorio ⨝ canciones`: una señal guardada en
    # el catalogo global se queda en el panel y no cambia una sola tarjeta.
    popularidad: float | None = None
    anio: int | None = None
    # De un grupo del pool prioritario del evento. Ya NO tiene cuota: entra por
    # un bono de puntaje que se desvanece ronda a ronda.
    prioritaria: bool = False

    def como_json(self) -> dict:
        return {
            "id": self.proveedor_id,
            "titulo": self.titulo,
            "artista": self.artista,
            "imagen": self.imagen,
        }

    def como_linea(self, indice: int) -> str:
        """La forma en que la ve el modelo. El indice es LOCAL: lo unico que el
        modelo puede emitir es un entero de esta lista, asi que no puede
        nombrar una canción que no este en el pozo."""
        etiquetas = " · ".join(
            x for x in (self.genero, self.epoca, self.idioma, str(self.intensidad or "")) if x
        )
        return f"{indice} · {etiquetas} · {self.titulo} — {self.artista}"


@dataclass(frozen=True)
class Pozo:
    disponibles: list[Candidata]     # del repertorio, MENOS lo que ya vio
    elegidas: list[Candidata]        # lo que eligio, en orden de ronda
    vistas: frozenset[int]           # cancion_id de todo lo que se le mostro
    ronda: int
    semilla: int                     # estable por dispositivo: baraja distinto para cada uno
    reinyectadas: int = 0            # cuantas se repitieron por repertorio pobre
    # Cuantas de las cinco tienen que salir del pool prioritario. Sale del
    # evento; en cero, el pool deja de tener cuota.
    prioritarias_objetivo: int = 0
    # ── las señales que antes se medían y no se usaban ──
    # cancion_id -> castigo por haber aparecido demasiado hace poco.
    fatiga: dict[int, float] = field(default_factory=dict)
    # Lo que viene funcionando en el salón: genero -> cuantas veces se eligio.
    # Conteo crudo; se conserva porque lo escribe el job y porque el panel lo
    # mira. Para armar una tanda manda `publico`, que es el mismo dato con dos
    # correcciones: lo reciente pesa mas y lo que eligieron varios pesa mas.
    perfil: dict[str, int] = field(default_factory=dict)
    # EL GUSTO DEL SALON, ponderado. Es lo que convierte al recomendador en un
    # agente colectivo: la eleccion de una persona cambia las cartas de la
    # siguiente, y esto es por donde viaja.
    publico: PerfilPublico = field(default_factory=lambda: PerfilPublico())
    # Generos que la gente del evento viene rechazando en bloque.
    rechazados: dict[str, int] = field(default_factory=dict)
    # Cuantas tandas seguidas rechazo ESTA persona. Dispara el reinicio.
    rechazos_seguidos: int = 0
    # Los generos que ESTA persona descarto en bloque. Distinto de
    # `rechazados`, que es del salon entero: si alguien rechazo cinco urbanas,
    # lo que no hay que devolverle son otras cinco urbanas -- aunque al resto
    # del salon le encanten.
    rechazados_propios: dict[str, int] = field(default_factory=dict)
    # Cuanto de la tanda sigue el gusto detectado (0..1). Del evento.
    explotar: float = 0.8


def _epoca_de(anio: int | None) -> str | None:
    """La decada en dos digitos, del año de la canción. 1972 -> "70"."""
    if not anio or anio < 1950:
        return None
    return f"{(anio // 10 * 10) % 100:02d}"


def semilla_de(dispositivo: str, ronda: int) -> int:
    """Un entero estable a partir del dispositivo y la ronda.

    Estable importa: si la semilla cambiara en cada llamada, recargar la
    pagina reordenaria las tarjetas y la persona creeria que son otras.
    """
    huella = hashlib.sha1(f"{dispositivo}|{ronda}".encode()).digest()
    return int.from_bytes(huella[:4], "big")


def _candidata(fila: Repertorio, cancion: Cancion) -> Candidata:
    return Candidata(
        cancion_id=cancion.id,
        proveedor_id=cancion.proveedor_id,
        titulo=cancion.titulo,
        artista=cancion.artista,
        artista_clave=fila.artista_clave,
        imagen=cancion.imagen,
        genero=fila.genero,
        # La epoca sale del repertorio cuando esta etiquetada y, si no, del año
        # de la canción: Spotify lo manda en cada busqueda y es dato duro.
        epoca=fila.epoca or _epoca_de(cancion.anio),
        idioma=fila.idioma,
        intensidad=fila.intensidad,
        popularidad=cancion.popularidad,
        anio=cancion.anio,
        prioritaria=bool(fila.prioritario),
    )


def armar(sesion: Session, evento: Evento, participante: Participante, ronda: int) -> Pozo:
    repertorio = sesion.execute(
        select(Repertorio, Cancion)
        .join(Cancion, Cancion.id == Repertorio.cancion_id)
        .where(Repertorio.evento_id == evento.id)
        .order_by(Repertorio.orden)
    ).all()
    por_id = {c.id: _candidata(r, c) for r, c in repertorio}

    # DE `exposiciones` Y NO DE `tanda_canciones`, y esto costo una prueba:
    # rehacer una tanda --lo que hace «ninguna me gusta»-- BORRA sus
    # `tanda_canciones`, asi que las cinco rechazadas volvian a aparecer como
    # disponibles. La exposicion sobrevive a la tanda que la mostro, que es
    # justamente lo que hace falta para no repetir nada.
    vistas = set(
        sesion.execute(
            select(Exposicion.cancion_id).where(
                Exposicion.participante_id == participante.id
            )
        ).scalars().all()
    )
    elegidas_ids = sesion.execute(
        select(Solicitud.cancion_id)
        .where(
            Solicitud.evento_id == evento.id,
            Solicitud.participante_id == participante.id,
            Solicitud.ronda > 0,
        )
        .order_by(Solicitud.ronda)
    ).scalars().all()

    disponibles = [c for cid, c in por_id.items() if cid not in vistas]
    elegidas = [por_id[cid] for cid in elegidas_ids if cid in por_id]

    # Repertorio pobre --o quinta ronda de un repertorio justo--: se reinyecta
    # lo VISTO Y NO ELEGIDO, que es lo menos malo de tres opciones malas.
    # Repetir una tarjeta que la persona descarto es peor que una tanda
    # perfecta; una tanda de cuatro tarjetas es peor que las dos cosas.
    reinyectadas = 0
    if len(disponibles) < TAMANO_MINIMO:
        descartadas = [c for cid, c in por_id.items() if cid in vistas and cid not in set(elegidas_ids)]
        faltan = TAMANO_MINIMO - len(disponibles)
        reinyectadas = min(faltan, len(descartadas))
        disponibles += descartadas[:faltan]
        registro.info(
            "repertorio pobre en %s ronda %s: se reinyectaron %d descartadas",
            evento.slug, ronda, reinyectadas,
        )

    return Pozo(
        disponibles=disponibles,
        elegidas=elegidas,
        vistas=frozenset(vistas),
        ronda=ronda,
        semilla=semilla_de(participante.dispositivo, ronda),
        reinyectadas=reinyectadas,
        prioritarias_objetivo=evento.prioritarias_por_tanda or 0,
        **_señales(sesion, evento, participante),
    )


def _señales(sesion: Session, evento: Evento, participante: Participante) -> dict:
    """Fatiga, gusto del salon y rechazos. Tres consultas por indice.

    Estaban calculadas --en `jobs/metricas.py` y en `evento_perfil`-- y no
    llegaban a la tanda: se median y no cambiaban nada. Estas tres consultas
    son lo que las pone a trabajar.
    """
    from app.jobs import metricas
    from app.recomendador import publico

    perfil = sesion.get(EventoPerfil, evento.id)
    # Rechazos SEGUIDOS de esta persona: se cuentan desde su ultima eleccion.
    # Dos tandas rechazadas al hilo son una señal clara; contarlos desde el
    # principio de la sesion castigaria a alguien que rechazo una vez hace
    # veinte minutos.
    ultima = sesion.scalar(
        select(func.max(Solicitud.creado)).where(
            Solicitud.evento_id == evento.id,
            Solicitud.participante_id == participante.id,
            Solicitud.ronda > 0,
        )
    )
    consulta = select(func.count(Rechazo.id)).where(Rechazo.participante_id == participante.id)
    if ultima is not None:
        consulta = consulta.where(Rechazo.creado > ultima)
    seguidos = sesion.scalar(consulta) or 0

    # Los generos que esta persona rechazo, de sus propias exposiciones.
    propios = dict(
        sesion.execute(
            select(Repertorio.genero, func.count(Exposicion.id))
            .join(Exposicion, Exposicion.cancion_id == Repertorio.cancion_id)
            .where(
                Exposicion.participante_id == participante.id,
                Repertorio.evento_id == evento.id,
                Exposicion.resultado == "rechazada",
                Repertorio.genero.is_not(None),
            )
            .group_by(Repertorio.genero)
        ).all()
    )

    return {
        "fatiga": metricas.fatiga(sesion, evento.id),
        "rechazados_propios": propios,
        # El ponderado se calcula en el momento, no se lee guardado: entre la
        # ultima eleccion y esta tanda pudo pasar de todo, y este es el unico
        # lugar donde el costo de recalcularlo compra algo.
        "publico": publico.calcular(sesion, evento.id),
        "perfil": dict((perfil.generos or {}) if perfil else {}),
        "rechazados": dict((perfil.rechazados or {}) if perfil else {}),
        "rechazos_seguidos": int(seguidos),
        "explotar": max(0.0, min(1.0, (evento.explotar_pct or 80) / 100)),
    }


def suficiente(pozo: Pozo) -> bool:
    return len(pozo.disponibles) >= TAMANO_MINIMO
