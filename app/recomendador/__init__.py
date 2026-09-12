"""El juego: cinco tandas de cinco canciones, una eleccion por tanda.

Esta es la fachada. Las rutas importan esto y nada de adentro.

La regla que ordena el modulo entero: **el modelo aporta juicio, Python impone
el contrato.** Una tanda sale siempre, con modelo o sin modelo, con repertorio
generoso o justo, con la API caida o sin una sola clave configurada. Lo unico
que cambia es que tan buena es.
"""
from __future__ import annotations

import asyncio
import logging
import time

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.bd import Sesion
from app.modelos import Evento, Participante, Solicitud, Tanda, TandaCancion
from app.recomendador import mensajes, modelo
from app.recomendador import pozo as _pozo
from app.recomendador import reglas, tandas
from app.recomendador.pozo import Candidata, Pozo

registro = logging.getLogger("tinker.juego")

# Lo que un telefono puede esperar una tanda antes de que se le arme una
# determinista. No es un timeout tecnico: es cuanto aguanta alguien parado en
# una fiesta mirando un esqueleto.
PACIENCIA = 6.0

# El piso del repertorio de un evento. Cinco rondas de cinco tarjetas son 25
# canciones distintas por persona; la regla de «un artista por tanda» necesita
# margen encima de eso. Un repertorio de treinta no falla al cargarlo: falla en
# la QUINTA ronda, en el salon, con gente esperando. Por eso se valida al
# pegar la lista y no al jugar.
MINIMO_REPERTORIO = 40


def ronda_en_curso(participante: Participante, techo: int) -> int | None:
    """La tanda que esta mirando, o None si el juego termino.

    ANTES se derivaba de las elecciones --la primera ronda sin eleccion-- y
    eso dejo de funcionar cuando se permitio elegir VARIAS canciones de cada
    tanda: hay rondas con tres elecciones y rondas con ninguna, asi que no hay
    de donde derivarla. Ahora es un dato del participante y avanza cuando la
    persona pide otras cinco.
    """
    actual = max(1, participante.ronda_actual or 1)
    return None if actual > techo else actual


def armar_local(pozo_: Pozo) -> list[Candidata]:
    """Una tanda sin modelo. Ronda 1 y respaldo de todo lo demas.

    La cuota del pool prioritario se aplica TAMBIEN aca, y esto costó una
    prueba: `imponer` la aplicaba, pero la ronda 1 y el respaldo no pasan por
    `imponer` --arman la tanda directo-- y salian con cero temas de los grupos
    elegidos. La regla es del producto, no del camino por el que se llegó.
    """
    if pozo_.ronda == 1 or not pozo_.elegidas:
        tanda = reglas.diversa(pozo_)
    else:
        tanda = reglas.afin(pozo_)
    return reglas.asegurar_prioritarias(tanda, pozo_)


def asegurar(
    sesion: Session, evento: Evento, participante: Participante, ronda: int,
) -> list[Candidata] | None:
    """La tanda de esa ronda. Si no existe, la arma DETERMINISTA y la guarda.

    Es lo que sirve la ronda 1 --que nunca llama al modelo, porque todavia no
    hay ninguna señal que interpretar-- y lo que rescata cualquier ronda cuyo
    prefetch no llego a tiempo.

    None si el repertorio no alcanza para armar una tanda: eso es un evento mal
    cargado y lo tiene que decir la ruta, no fingir una pantalla.
    """
    ya = tandas.leer(sesion, evento.id, participante.id, ronda)
    if ya is not None:
        return ya

    pozo_ = _pozo.armar(sesion, evento, participante, ronda)
    if not _pozo.suficiente(pozo_):
        registro.warning(
            "repertorio insuficiente en %s: %d disponibles para la ronda %d",
            evento.slug, len(pozo_.disponibles), ronda,
        )
        return None

    propuesta = armar_local(pozo_)
    origen = "semilla" if ronda == 1 else "respaldo"
    linea = mensajes.para(ronda, pozo_.publico, pozo_.semilla)
    if not tandas.guardar(
        sesion, evento.id, participante.id, ronda, propuesta, origen, mensaje=linea
    ):
        # Otro la escribio en el medio --el prefetch, o la otra pestaña del
        # mismo telefono--. La que gano es la buena.
        return tandas.leer(sesion, evento.id, participante.id, ronda)
    return propuesta


def rehacer_tanda(
    sesion: Session, evento: Evento, participante: Participante, ronda: int,
) -> list[Candidata] | None:
    """Otras cinco para la misma ronda, despues de un rechazo.

    La tanda anterior se BORRA --su fila, no sus exposiciones--: el unique
    (participante, ronda) no admite dos, y las exposiciones ya quedaron
    registradas con su rechazo. Asi lo mostrado sigue contando aunque la tanda
    que lo mostro ya no exista.

    Las cinco rechazadas no vuelven: `pozo.armar` descarta todo lo visto.
    """
    vieja = sesion.scalar(
        select(Tanda).where(Tanda.participante_id == participante.id, Tanda.ronda == ronda)
    )
    if vieja is not None:
        sesion.execute(
            TandaCancion.__table__.delete().where(TandaCancion.tanda_id == vieja.id)
        )
        sesion.execute(Tanda.__table__.delete().where(Tanda.id == vieja.id))
        sesion.commit()
    return asegurar(sesion, evento, participante, ronda)


def estado(sesion: Session, evento: Evento, participante: Participante) -> dict:
    """Todo lo que el telefono necesita para pintarse entero.

    NUNCA llama al modelo y nunca hace mas de tres consultas: es lo que corre
    en cada recarga de pagina, y una recarga en medio de una fiesta tiene que
    ser instantanea.
    """
    elegidas = tandas.elecciones(sesion, evento.id, participante.id)
    # El techo incluye las rondas extra que pidio esta persona.
    techo = evento.rondas + (participante.rondas_extra or 0)
    ronda = ronda_en_curso(participante, techo)

    datos = {
        "estado": "completado" if ronda is None else "jugando",
        "ronda": ronda,
        "rondas": techo,
        "rondas_base": evento.rondas,
        "elegidas": [
            {
                "id": cancion.proveedor_id,
                "titulo": cancion.titulo,
                "artista": cancion.artista,
                "imagen": cancion.imagen,
                "ronda": r,
            }
            for r, cancion in elegidas
        ],
        "tanda": None,
    }
    if ronda is not None:
        servida = tandas.leer(sesion, evento.id, participante.id, ronda)
        if servida is not None:
            datos["tanda"] = {
                "ronda": ronda,
                "canciones": [c.como_json() for c in servida],
                # Lo unico que el agente dice en voz alta. Va con la tanda y no
                # aparte: es de ESA tanda, y si viajara suelto se desfasaria al
                # recargar justo en el medio de un cambio de ronda.
                "mensaje": tandas.mensaje_de(sesion, participante.id, ronda),
            }
    return datos


def elegir(
    sesion: Session, evento: Evento, participante: Participante, ronda: int, cancion_id: str,
) -> dict:
    """Registra la eleccion. Devuelve `{ok, cancion, repetida}` o levanta ValueError.

    La canción tiene que estar entre las CINCO que el servidor le mostro a este
    telefono en esta ronda. Es la version fuerte de la vieja regla "los datos
    salen del catalogo, nunca del pedido": antes se podia pedir cualquier id
    del catalogo, ahora solo uno de los cinco servidos.
    """
    servida = tandas.leer(sesion, evento.id, participante.id, ronda)
    if servida is None:
        raise ValueError("tanda_inexistente")
    elegida = next((c for c in servida if c.proveedor_id == cancion_id), None)
    if elegida is None:
        raise ValueError("fuera_de_tanda")

    sesion.add(
        Solicitud(
            evento_id=evento.id,
            participante_id=participante.id,
            cancion_id=elegida.cancion_id,
            ronda=ronda,
        )
    )
    try:
        sesion.commit()
        return {"cancion": elegida, "repetida": False}
    except IntegrityError:
        # Dos toques seguidos, o la misma canción ya elegida en otra tanda. No
        # es un error: se responde como si hubiera funcionado.
        sesion.rollback()
        return {"cancion": elegida, "repetida": True}


def avanzar(sesion: Session, evento: Evento, participante: Participante) -> int | None:
    """Otras cinco. Devuelve la ronda nueva, o None si el juego termino.

    Lo pide la persona con un boton y no pasa solo al elegir: desde que se
    pueden marcar varias de una tanda, avanzar automaticamente al primer toque
    le robaria las otras cuatro.
    """
    techo = evento.rondas + (participante.rondas_extra or 0)
    participante.ronda_actual = (participante.ronda_actual or 1) + 1
    sesion.commit()
    return None if participante.ronda_actual > techo else participante.ronda_actual


# ── la parte cara: el modelo ─────────────────────────────────────────────
#
# Todo lo de aca abajo lo llama SOLO el prefetch, nunca una ruta. Ver el
# comentario de `prefetch.py`.

# Cuantas candidatas ve el modelo. Un prompt con ochocientas canciones es caro,
# lento y peor: el pozo va recortado y ya preseleccionado por las reglas, asi
# que lo unico que queda por decidir es lo que un modelo hace mejor que un
# puntaje -- que cinco van bien juntas para ESTA persona.
TOPE_POZO = 60


def _recortar(pozo_: Pozo) -> list[Candidata]:
    """Las candidatas que se le muestran al modelo, estratificadas por genero.

    Sin estratificar, un repertorio con doscientas cumbias y veinte rocks le
    llegaria al modelo como una lista de cumbias, y la diversidad que le
    pedimos en el prompt no tendria de donde salir.
    """
    if len(pozo_.disponibles) <= TOPE_POZO:
        return list(pozo_.disponibles)

    por_genero: dict[str, list[Candidata]] = {}
    for candidata in pozo_.disponibles:
        por_genero.setdefault(candidata.genero or "otros", []).append(candidata)

    elegidas: list[Candidata] = []
    vuelta = 0
    while len(elegidas) < TOPE_POZO:
        agregadas = 0
        for grupo in por_genero.values():
            if vuelta < len(grupo) and len(elegidas) < TOPE_POZO:
                elegidas.append(grupo[vuelta])
                agregadas += 1
        if not agregadas:
            break
        vuelta += 1
    return elegidas


def _salon(pozo_: Pozo) -> str:
    """El estado del salon, en el formato mas corto que se entienda.

    Seis señales que el sistema ya medía y que el modelo NUNCA veía: el perfil
    colectivo, la energía, las décadas, cuánta gente hay, la franja horaria y
    lo último que sonó. Estaban cargadas en el `Pozo` y sólo influían en el
    puntaje determinista -- el modelo elegía a ciegas sobre el salón.
    """
    perfil = pozo_.publico
    if not perfil.elecciones:
        return "El salón recién arranca: sos la primera lectura de la noche."

    def barra(datos, tope=4):
        return ", ".join(
            f"{k} {round(v * 100)}%"
            for k, v in sorted(datos.items(), key=lambda x: -x[1])[:tope]
        ) or "—"

    lineas = [
        f"Gente con el teléfono en la mano: {perfil.personas}. "
        f"Elecciones en total: {perfil.elecciones}. Franja: {perfil.franja}.",
        f"Géneros del salón: {barra(perfil.generos)}",
    ]
    if perfil.decadas:
        lineas.append(f"Décadas: {barra(perfil.decadas, 3)}")
    if perfil.energia is not None:
        lineas.append(f"Energía del salón: {round(perfil.energia * 100)}/100")
    if perfil.dominantes:
        lineas.append(
            f"DOMINA: {' y '.join(perfil.dominantes)}. Las dos cartas abiertas "
            f"NO pueden ser de esos géneros."
        )
    if perfil.recientes:
        ultimas = "; ".join(f"{r['titulo']} — {r['artista']}" for r in perfil.recientes[:5])
        lineas.append(f"Lo último que eligió el salón: {ultimas}")
    if perfil.confianza < 1.0:
        lineas.append(
            f"Ojo: con {perfil.personas} persona(s), el gusto del salón todavía "
            f"vale poco. Pesá más lo que eligió esta persona."
        )
    return "\n".join(lineas)


def _pedido(pozo_: Pozo, candidatas: list[Candidata]) -> str:
    """El texto que ve el modelo.

    Cada candidata es una linea con un INDICE LOCAL, no el id de la base. Dos
    motivos: cuesta dos tokens en vez de veinte, y hace que el modelo sea
    fisicamente incapaz de nombrar una canción que no este en el pozo -- solo
    puede emitir enteros que nosotros mapeamos de vuelta.
    """
    # CON el pozo. Sin el, el prompt calculaba una mezcla y el respaldo
    # determinista otra: `explotar_pct` y el reinicio de diversidad movian una
    # rama del sistema y no la otra.
    proporcion = reglas.mezcla(pozo_.ronda, pozo_)
    reparto = reglas.ranuras(pozo_)
    elegidas = "\n".join(
        f"  · {c.titulo} — {c.artista}"
        + (f"  [{c.genero}]" if c.genero else "")
        for c in pozo_.elegidas
    ) or "  (todavía no eligió nada)"
    pozo_texto = "\n".join(c.como_linea(i) for i, c in enumerate(candidatas))
    return (
        f"Ronda {pozo_.ronda}. Reparto de las cinco: "
        f"{reparto.count('individual') or reparto.count('explorar')} siguiendo a esta persona, "
        f"{reparto.count('colectivo')} siguiendo al salón, "
        f"{reparto.count('contraste')} abiertas.\n"
        f"Mezcla objetivo: {round(proporcion * 100)}% afinidad / "
        f"{round((1 - proporcion) * 100)}% explorar.\n\n"
        f"EL SALÓN:\n{_salon(pozo_)}\n\n"
        f"ESTA PERSONA ya eligió:\n{elegidas}\n\n"
        f"Pozo (índice · género · época · idioma · intensidad · tema — artista):\n{pozo_texto}"
    )


def _contexto(evento_id: int, participante_id: int, ronda: int):
    """Lee todo lo que hace falta para la llamada, con su PROPIA sesion.

    Devuelve None cuando no hay nada que hacer: la tanda ya existe, el evento
    cerro, el repertorio no alcanza, o se llego al tope de gasto del evento.
    """
    with Sesion() as sesion:
        evento = sesion.get(Evento, evento_id)
        participante = sesion.get(Participante, participante_id)
        if evento is None or participante is None or evento.estado == "cerrado":
            return None
        if tandas.leer(sesion, evento_id, participante_id, ronda) is not None:
            return None
        if tandas.llamadas_al_modelo(sesion, evento_id) >= evento.tope_ia:
            registro.warning(
                "evento %s llego a su tope de %d llamadas: las tandas siguen "
                "saliendo deterministas", evento.slug, evento.tope_ia
            )
            return None
        pozo_ = _pozo.armar(sesion, evento, participante, ronda)
        if not _pozo.suficiente(pozo_):
            return None
        return pozo_


def _guardar(evento_id, participante_id, ronda, tanda, origen, uso, porque, ms, mensaje, lectura):
    with Sesion() as sesion:
        return tandas.guardar(
            sesion, evento_id, participante_id, ronda, tanda, origen, uso, porque, ms,
            mensaje, lectura,
        )


async def preparar_con_modelo(
    evento_id: int, participante_id: int, ronda: int, espera_cola: float = 0.0,
) -> None:
    """Arma la tanda con el modelo y la guarda. Nunca levanta hacia arriba.

    Si el modelo falla, tarda de mas o devuelve basura, se guarda igual una
    tanda determinista: el criterio de aceptacion de todo este modulo es que la
    persona NUNCA vea un error.
    """
    arranque = time.monotonic()
    pozo_ = await asyncio.to_thread(_contexto, evento_id, participante_id, ronda)
    if pozo_ is None:
        return

    candidatas = _recortar(pozo_)
    propuesta: list[Candidata] = []
    porque, uso, origen = None, None, "respaldo"
    sugerencia = None

    if modelo.disponible():
        try:
            presupuesto = max(0.0, modelo.PRESUPUESTO_TOTAL - espera_cola)
            sugerencia = await modelo.sugerir(_pedido(pozo_, candidatas), presupuesto)
            propuesta = [
                candidatas[i] for i in sugerencia.indices if 0 <= i < len(candidatas)
            ]
            porque, uso, origen = sugerencia.porque, sugerencia.uso, "modelo"
        except (modelo.Rechazado, modelo.SinProveedor) as error:
            registro.info("sin sugerencia para la ronda %s: %s", ronda, error)
            porque, uso, sugerencia = None, None, None

    tanda = reglas.imponer(propuesta, pozo_)

    # El mensaje del modelo SOLO si pasa el chequeo de forma; si no, el
    # determinista. Es la misma regla de siempre --el modelo aporta juicio,
    # Python impone el contrato-- aplicada a lo unico que el agente dice en voz
    # alta, que es justo donde una frase mala se ve.
    linea = mensajes.del_modelo(sugerencia.mensaje if sugerencia else None, tanda)
    if not linea:
        linea = mensajes.para(ronda, pozo_.publico, pozo_.semilla)

    ms = int((time.monotonic() - arranque) * 1000)
    await asyncio.to_thread(
        _guardar, evento_id, participante_id, ronda, tanda, origen, uso, porque, ms,
        linea, (sugerencia.lectura if sugerencia else None),
    )
    registro.info("tanda ronda %s lista en %d ms (%s)", ronda, ms, origen)
