"""La API que usan los telefonos y el televisor. Sin sesion, a proposito.

El telefono ya no busca: juega. Cinco tandas de cinco canciones, una eleccion
por tanda, y cada eleccion cae en la pantalla del salon en el acto.

Sobre el bloqueo: estos endpoints son `async` porque el hub se espera, pero las
consultas a la base son sincronicas y corren en el loop. Para una fiesta
--cientos de elecciones por hora, no por segundo, y consultas de milisegundos--
es la eleccion correcta. Lo que NUNCA ocurre aca es una llamada al modelo: esa
vive en el prefetch, y el motivo esta escrito en `recomendador/prefetch.py`.
"""
from __future__ import annotations

import io
import logging
import re
from datetime import datetime, timezone

import segno
from html import escape

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import panorama, recomendador
from app.bd import con_sesion
from app.modelos import Cancion, Evento, Participante, Rechazo, Solicitud
from app.recomendador import modelo, tandas
from app.recomendador import publico as publico_perfil
from app.spotify import cola as spotify_cola
from app.spotify import reproduccion
from app.recomendador.prefetch import prefetch
from app.tiempo_real.hub import hub

registro = logging.getLogger("tinker.api")

ruteador = APIRouter(prefix="/api")

SLUG = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
DISPOSITIVO = re.compile(r"^[A-Za-z0-9-]{8,64}$")


# ───────────────────────────── helpers ─────────────────────────────

def evento_o_404(sesion: Session, slug: str) -> Evento:
    if not SLUG.match(slug or ""):
        raise HTTPException(404, {"error": "evento_inexistente"})
    evento = sesion.scalar(select(Evento).where(Evento.slug == slug))
    if evento is None:
        raise HTTPException(404, {"error": "evento_inexistente"})
    return evento


def participante_de(sesion: Session, evento: Evento, dispositivo: str) -> Participante:
    """Devuelve el participante de este telefono, creandolo si es su primera vez.

    La carrera es real y pasa: el navegador dispara `entrar` y el poll casi
    juntos. El unique de la base decide, y el segundo en llegar se queda con la
    fila del primero en vez de romper.
    """
    if not DISPOSITIVO.match(dispositivo or ""):
        raise HTTPException(422, {"error": "dispositivo_invalido"})
    consulta = select(Participante).where(
        Participante.evento_id == evento.id, Participante.dispositivo == dispositivo
    )
    participante = sesion.scalar(consulta)
    if participante:
        return participante
    participante = Participante(evento_id=evento.id, dispositivo=dispositivo)
    sesion.add(participante)
    try:
        sesion.commit()
    except IntegrityError:
        sesion.rollback()
        participante = sesion.scalar(consulta)
        if participante is None:
            raise
    return participante


def _evento_json(evento: Evento) -> dict:
    return {
        "slug": evento.slug,
        "nombre": evento.nombre,
        "lugar": evento.lugar,
        "estado": evento.estado,
        "rondas": evento.rondas,
        "playlist_url": evento.playlist_url,
        "abierto": evento.estado in ("previo", "en_vivo"),
    }


async def _empujar_eleccion(sesion: Session, evento: Evento, cancion_id: int, proveedor_id: str) -> None:
    """A la pantalla del salon, en el acto. Cada eleccion, no cada persona que
    termina: el televisor tiene que reaccionar mientras la gente juega."""
    cancion = sesion.get(Cancion, cancion_id)
    votos = panorama.votos(sesion, evento.id, cancion_id)
    await hub.publicar(
        evento.slug,
        {
            "tipo": "nueva" if votos == 1 else "voto",
            "cancion": {
                "id": proveedor_id,
                "titulo": cancion.titulo,
                "artista": cancion.artista,
                "imagen": cancion.imagen,
                "votos": votos,
            },
            "totales": panorama.totales(sesion, evento.id),
        },
    )

    # Y EL SALON APRENDE, en el acto. Es el nucleo del agente colectivo: lo que
    # esta persona acaba de elegir cambia lo que van a ver las que vienen
    # despues, y la pantalla lo muestra moviendose.
    #
    # Se recalcula entero --no incrementos-- porque son cientos de filas
    # agrupadas por indice, y un contador guardado que se desincroniza miente
    # sin avisar. Si alguna vez pesa, se cachea; medir antes de optimizar.
    #
    # Nunca tumba una eleccion: la canción ya esta guardada y el perfil es una
    # mejora del proximo reparto, no parte del gesto de la persona.
    try:
        perfil = publico_perfil.refrescar(sesion, evento.id)
        await hub.publicar(evento.slug, {"tipo": "perfil", "perfil": perfil.como_json()})
    except Exception:  # noqa: BLE001
        registro.exception("no pude refrescar el perfil del salon; la eleccion ya esta guardada")


def _juego_o_422(sesion: Session, evento: Evento, participante: Participante) -> dict:
    """El estado del juego, con la tanda de la ronda en curso.

    Tres caminos, y la diferencia entre ellos es de cuanto se puede esperar:

      · Ronda 1 -> se arma ACA, determinista. No hay ninguna señal todavia que
        un modelo pueda interpretar, y es la pantalla donde se gana o se pierde
        a la gente: tiene que aparecer sin espera.
      · Rondas 2 a 5 con modelo -> se encarga al prefetch y se devuelve
        `tanda: null`. El telefono hace poll y el servidor tiene `PACIENCIA`
        segundos para contestar con algo mejor.
      · Sin modelo configurado -> se arma aca, determinista, y viaja en esta
        misma respuesta. El juego entero se juega asi, sin una sola clave.
    """
    datos = recomendador.estado(sesion, evento, participante)
    if datos["estado"] != "jugando" or datos["tanda"] is not None:
        return datos

    ronda = datos["ronda"]
    if ronda > 1 and modelo.disponible() and prefetch.activo:
        prefetch.encargar(evento.id, participante.id, ronda)
        datos["preparando"] = True
        return datos

    servida = recomendador.asegurar(sesion, evento, participante, ronda)
    if servida is None:
        raise HTTPException(422, {"error": "repertorio_insuficiente"})
    datos["tanda"] = {
        "ronda": ronda,
        "canciones": [c.como_json() for c in servida],
        # Tambien por este camino. La ronda 1 --y cualquier respaldo-- se sirve
        # aca y no por `estado()`, asi que agregar el mensaje en un solo lado
        # dejaba muda justo a la primera tanda, que es la que mas se ve.
        "mensaje": tandas.mensaje_de(sesion, participante.id, ronda),
    }
    return datos


# ───────────────────────────── el juego ─────────────────────────────

class Entrada(BaseModel):
    dispositivo: str = Field(min_length=8, max_length=64)


@ruteador.post("/e/{slug}/entrar")
async def entrar(slug: str, cuerpo: Entrada, sesion: Session = Depends(con_sesion)) -> dict:
    """La primera llamada, y la unica que hace falta para reconstruir TODO
    despues de una recarga: el evento, lo elegido y la tanda en curso.

    Idempotente: entrar diez veces no crea diez participantes, no arma diez
    tandas y no pierde una sola eleccion.
    """
    evento = evento_o_404(sesion, slug)
    participante = participante_de(sesion, evento, cuerpo.dispositivo)
    if evento.estado == "cerrado":
        return {
            "evento": _evento_json(evento),
            "juego": recomendador.estado(sesion, evento, participante),
        }
    return {"evento": _evento_json(evento), "juego": _juego_o_422(sesion, evento, participante)}


@ruteador.get("/e/{slug}/tanda")
async def ver_tanda(
    slug: str, dispositivo: str = "", ronda: int = 0, sesion: Session = Depends(con_sesion),
) -> dict:
    """El poll de la tanda siguiente, con FECHA LIMITE.

    Tres respuestas posibles, y la tercera es la que convierte «una fiesta no
    puede depender de que conteste una API» de principio en mecanismo:

      1. La tanda existe (el prefetch llego) -> las cinco tarjetas.
      2. No existe y la eleccion anterior es reciente -> «reintenta en 500 ms».
      3. No existe y ya se acabo la paciencia -> se arma la determinista ACA
         MISMO, se guarda y se devuelve.

    El plazo sale de `solicitudes.creado`, que ya esta en la base: no hace
    falta estado extra y sobrevive a un reinicio del contenedor.
    """
    evento = evento_o_404(sesion, slug)
    participante = participante_de(sesion, evento, dispositivo)
    # El techo incluye las rondas extra de ESTE telefono: quien pidio «elegir
    # cinco mas» tiene que poder pedir la tanda de la ronda 6.
    techo = evento.rondas + (participante.rondas_extra or 0)
    if not 1 <= ronda <= techo:
        raise HTTPException(422, {"error": "ronda_invalida"})

    servida = tandas.leer(sesion, evento.id, participante.id, ronda)
    if servida is not None:
        return {
            "lista": True,
            "ronda": ronda,
            "canciones": [c.como_json() for c in servida],
            "mensaje": tandas.mensaje_de(sesion, participante.id, ronda),
        }

    anterior = sesion.scalar(
        select(Solicitud.creado).where(
            Solicitud.evento_id == evento.id,
            Solicitud.participante_id == participante.id,
            Solicitud.ronda == ronda - 1,
        )
    )
    if anterior is not None:
        esperando = (datetime.now(timezone.utc) - anterior).total_seconds()
        if esperando < recomendador.PACIENCIA:
            return {"lista": False, "ronda": ronda, "reintentar_en_ms": 500}

    nueva = recomendador.asegurar(sesion, evento, participante, ronda)
    if nueva is None:
        raise HTTPException(422, {"error": "repertorio_insuficiente"})
    return {
        "lista": True,
        "ronda": ronda,
        "canciones": [c.como_json() for c in nueva],
        "mensaje": tandas.mensaje_de(sesion, participante.id, ronda),
    }


class Eleccion(BaseModel):
    dispositivo: str = Field(min_length=8, max_length=64)
    ronda: int = Field(ge=1, le=10)
    cancion: str = Field(min_length=1, max_length=120)


@ruteador.post("/e/{slug}/elegir")
async def elegir(slug: str, cuerpo: Eleccion, sesion: Session = Depends(con_sesion)) -> dict:
    evento = evento_o_404(sesion, slug)
    if evento.estado == "cerrado":
        raise HTTPException(409, {"error": "evento_cerrado"})
    participante = participante_de(sesion, evento, cuerpo.dispositivo)

    antes = recomendador.estado(sesion, evento, participante)
    if antes["estado"] == "completado":
        raise HTTPException(409, {"error": "juego_completado"})

    try:
        resultado = recomendador.elegir(sesion, evento, participante, cuerpo.ronda, cuerpo.cancion)
    except ValueError as error:
        raise HTTPException(409, {"error": str(error)})

    if not resultado["repetida"]:
        await _empujar_eleccion(
            sesion, evento, resultado["cancion"].cancion_id, resultado["cancion"].proveedor_id
        )

    # Las cinco exposiciones de esta tanda quedan cerradas: una `elegida` y
    # cuatro `no_elegida`. Es un UPDATE local por indice -- de las «consultas
    # locales rapidas» que el camino caliente si puede permitirse.
    tanda_id = tandas.tanda_id_de(sesion, participante.id, cuerpo.ronda)
    if tanda_id is not None:
        tandas.resolver_exposiciones(sesion, tanda_id, resultado["cancion"].cancion_id)

    # Y a la cola de Spotify, si la politica del evento dice que corresponde.
    # ENCOLAR, no enviar: mandar a Spotify desde aca seria poner una API ajena
    # en el medio del gesto de una persona en una fiesta.
    try:
        if spotify_cola.corresponde(sesion, evento, resultado["cancion"].cancion_id):
            spotify_cola.encolar(sesion, evento, resultado["cancion"].cancion_id)
    except Exception:  # noqa: BLE001
        registro.exception("no pude encolar para Spotify; la eleccion ya esta guardada")

    # Y LA TANDA SIGUIENTE EMPIEZA A COCINARSE AHORA. Antes se encargaba
    # recien cuando el telefono pedia la ronda nueva, y para entonces la
    # persona ya estaba mirando un esqueleto: el modelo tenia que contestar
    # dentro de la paciencia o no servia. Encargandola al primer toque, la
    # persona todavia va a marcar otras tarjetas y recien despues va a tocar
    # «otras cinco» -- diez o veinte segundos de ventaja, gratis.
    #
    # No multiplica el gasto: `_nacer` deduplica por (participante, ronda) y
    # el UNIQUE de la base es el segundo cerrojo. Sigue habiendo A LO SUMO una
    # llamada por persona y ronda, por mas veces que se toque.
    siguiente = cuerpo.ronda + 1
    techo = evento.rondas + (participante.rondas_extra or 0)
    if siguiente <= techo and modelo.disponible() and prefetch.activo:
        prefetch.encargar(evento.id, participante.id, siguiente)

    # NO se avanza de ronda: la persona sigue viendo la misma tanda y puede
    # marcar mas. Avanzar es un gesto aparte --el boton «otras cinco»-- y ese
    # es justamente el cambio: de cinco tarjetas te pueden gustar tres.
    return {"evento": _evento_json(evento), "juego": _juego_o_422(sesion, evento, participante)}


@ruteador.delete("/e/{slug}/elegidas/{cancion_id}")
async def quitar(
    slug: str, cancion_id: str, dispositivo: str = "", sesion: Session = Depends(con_sesion),
) -> dict:
    """Saca una eleccion y LIBERA esa ronda: vuelve la misma tanda y se elige
    de nuevo entre las mismas cinco.

    Quitar NUNCA arma una tanda nueva ni llama al modelo. Eso cierra de raiz el
    ciclo «elegir → quitar → elegir», que es el vector de gasto infinito mas
    obvio de todo el diseño.
    """
    evento = evento_o_404(sesion, slug)
    if evento.estado == "cerrado":
        raise HTTPException(409, {"error": "evento_cerrado"})
    participante = participante_de(sesion, evento, dispositivo)

    cancion = sesion.scalar(select(Cancion).where(Cancion.proveedor_id == cancion_id))
    if cancion is not None:
        borradas = sesion.execute(
            delete(Solicitud).where(
                Solicitud.evento_id == evento.id,
                Solicitud.participante_id == participante.id,
                Solicitud.cancion_id == cancion.id,
            )
        ).rowcount
        sesion.commit()
        if borradas:
            await hub.publicar(
                evento.slug, {"tipo": "baja", "totales": panorama.totales(sesion, evento.id)}
            )
    return {"evento": _evento_json(evento), "juego": _juego_o_422(sesion, evento, participante)}


@ruteador.post("/e/{slug}/avanzar")
async def avanzar(slug: str, cuerpo: Entrada, sesion: Session = Depends(con_sesion)) -> dict:
    """«Mostrame otras cinco», sin rechazar las de ahora.

    Es distinto de `/rechazar`: ahi la persona dice que ninguna le gusto --señal
    fuerte-- y acá simplemente terminó con esta tanda. Las que no marcó quedan
    como `no_elegida`, que es una señal debil y correcta.
    """
    evento = evento_o_404(sesion, slug)
    if evento.estado == "cerrado":
        raise HTTPException(409, {"error": "evento_cerrado"})
    participante = participante_de(sesion, evento, cuerpo.dispositivo)

    anterior = participante.ronda_actual or 1
    tanda_id = tandas.tanda_id_de(sesion, participante.id, anterior)
    if tanda_id is not None:
        # Lo marcado queda `elegida` --ya se resolvio al elegir-- y el resto
        # pasa a `no_elegida`.
        # `elecciones` devuelve (ronda, Cancion): el id de la canción es `.id`,
        # no `.cancion_id` -- eso es de Solicitud. Con el nombre equivocado,
        # «Otras cinco» tiraba un 500 y la prueba lo encontro antes de la
        # proxima fiesta.
        elegidas = {
            cancion.id for _, cancion in tandas.elecciones(sesion, evento.id, participante.id)
        }
        tandas.resolver_exposiciones(sesion, tanda_id, None, marcadas=elegidas)

    recomendador.avanzar(sesion, evento, participante)
    return {"evento": _evento_json(evento), "juego": _juego_o_422(sesion, evento, participante)}


RONDAS_POR_TANDA_EXTRA = 5


@ruteador.post("/e/{slug}/seguir")
async def seguir(slug: str, cuerpo: Entrada, sesion: Session = Depends(con_sesion)) -> dict:
    """«Dejame elegir otras mas». Cinco rondas mas para este telefono.

    No cambia el juego de nadie mas: las rondas extra son de esta persona. Y
    tiene techo --el check de la tabla-- porque «sin limite» y «un bucle que
    gasta llamadas al modelo para siempre» son la misma cosa.
    """
    evento = evento_o_404(sesion, slug)
    if evento.estado == "cerrado":
        raise HTTPException(409, {"error": "evento_cerrado"})
    participante = participante_de(sesion, evento, cuerpo.dispositivo)

    techo = evento.rondas + (participante.rondas_extra or 0)
    if techo + RONDAS_POR_TANDA_EXTRA > 50:
        raise HTTPException(409, {"error": "demasiadas_rondas"})
    participante.rondas_extra = (participante.rondas_extra or 0) + RONDAS_POR_TANDA_EXTRA
    sesion.commit()

    return {"evento": _evento_json(evento), "juego": _juego_o_422(sesion, evento, participante)}


class RechazoTanda(BaseModel):
    dispositivo: str = Field(min_length=8, max_length=64)
    ronda: int = Field(ge=1, le=10)


@ruteador.post("/e/{slug}/rechazar")
async def rechazar(slug: str, cuerpo: RechazoTanda, sesion: Session = Depends(con_sesion)) -> dict:
    """«Ninguna me gusta». Otras cinco, y que no se parezcan.

    Es informacion que un sistema que solo mira elecciones nunca tiene: cinco
    canciones que perdieron contra una sexta no dicen nada, pero cinco que la
    persona descarto en bloque dicen mucho.

    No avanza la ronda: la persona sigue debiendo esa eleccion. Lo que cambia
    es la tanda -- y si viene rechazando seguido, tambien el rumbo.
    """
    evento = evento_o_404(sesion, slug)
    if evento.estado == "cerrado":
        raise HTTPException(409, {"error": "evento_cerrado"})
    participante = participante_de(sesion, evento, cuerpo.dispositivo)

    tanda_id = tandas.tanda_id_de(sesion, participante.id, cuerpo.ronda)
    if tanda_id is None:
        raise HTTPException(409, {"error": "tanda_inexistente"})

    sesion.add(
        Rechazo(
            evento_id=evento.id,
            participante_id=participante.id,
            tanda_id=tanda_id,
            ronda=cuerpo.ronda,
        )
    )
    try:
        sesion.commit()
    except IntegrityError:
        sesion.rollback()      # doble toque: ya estaba rechazada

    tandas.resolver_exposiciones(sesion, tanda_id, None, rechazada=True)

    # Las cinco quedan fuera de esta sesion: ya se mostraron, y `pozo.armar`
    # descarta todo lo visto. Lo unico que hace falta es una tanda nueva para
    # la MISMA ronda, y eso se resuelve reemplazando la anterior.
    nueva = recomendador.rehacer_tanda(sesion, evento, participante, cuerpo.ronda)
    if nueva is None:
        raise HTTPException(422, {"error": "repertorio_insuficiente"})

    return {
        "evento": _evento_json(evento),
        "juego": recomendador.estado(sesion, evento, participante),
    }


# ───────────────────── buscar por grupo ─────────────────────
#
# El unico teclado del juego, y es opcional: quien quiere un tema de un grupo
# que no salio en ninguna tanda, lo busca. Lo que se elige de aca entra al
# repertorio del evento, asi que despues puede aparecerle a otra gente.


@ruteador.get("/e/{slug}/grupo")
async def temas_del_grupo(slug: str, q: str = "", sesion: Session = Depends(con_sesion)) -> dict:
    """Los temas mas conocidos de un grupo.

    Salen de Deezer, que responde sin credenciales y devuelve temas REALES del
    artista. No se le pide a un modelo que los invente: un titulo inventado es
    una canción que despues nadie puede poner.
    """
    evento_o_404(sesion, slug)
    consulta = (q or "").strip()
    if len(consulta) < 2:
        return {"grupo": consulta, "temas": []}

    from app.descubrimiento import Deezer

    deezer = Deezer()
    encontrados = await deezer.top_del_artista(consulta, limite=12)

    # SEMEJANZAS. Si el grupo no aparece --o trae poco-- el modelo propone
    # artistas parecidos y los temas se buscan en Deezer: el modelo dice A
    # QUIEN parecerse, el catalogo dice QUE existe. Pedirle titulos al modelo
    # y usarlos directo llenaria el repertorio de canciones inventadas.
    parecidos: list[dict] = []
    if len(encontrados) < 4:
        parecidos = await _parecidos(deezer, consulta)

    def _json(c) -> dict:
        return {"titulo": c.titulo, "artista": c.artista, "imagen": c.imagen}

    return {
        "grupo": consulta,
        "temas": [_json(c) for c in encontrados],
        "parecidos": parecidos,
    }


ESQUEMA_PARECIDOS = {
    "type": "object",
    # Sin minItems/maxItems: la salida estructurada no los admite. La cantidad
    # la pide el prompt y la recorta Python.
    "properties": {"artistas": {"type": "array", "items": {"type": "string"}}},
    "required": ["artistas"],
    "additionalProperties": False,
}


async def _parecidos(deezer, consulta: str) -> list[dict]:
    """Temas de artistas parecidos, verificados contra el catalogo.

    Nunca levanta: si el modelo no esta o no contesta, se devuelve vacio y el
    buscador simplemente dice que no encontro nada. Un buscador sin sugerencias
    es peor que uno con; uno que rompe la pantalla es peor que los dos.
    """
    if not modelo.disponible():
        return []
    try:
        datos, _ = await modelo._pedir(
            "Sos un DJ que conoce música popular de Paraguay, Argentina, Brasil y el mundo.",
            f'Nombrá 5 artistas o grupos parecidos a "{consulta}", que la gente de una '
            "fiesta reconozca. Solo los nombres, sin explicar. Si no conocés al que te "
            "nombran, proponé 5 que suenen parecido a lo que sugiere ese nombre.",
            ESQUEMA_PARECIDOS,
            6.0,
        )
    except Exception as error:  # noqa: BLE001
        registro.info("sin parecidos para %r: %s", consulta, error)
        return []

    nombres = [n for n in (datos.get("artistas") or []) if isinstance(n, str)][:5]
    salida: list[dict] = []
    for nombre in nombres:
        temas = await deezer.top_del_artista(nombre, limite=2)
        for tema in temas:
            salida.append({"titulo": tema.titulo, "artista": tema.artista, "imagen": tema.imagen})
        if len(salida) >= 8:
            break
    return salida[:8]


class EleccionBuscada(BaseModel):
    dispositivo: str = Field(min_length=8, max_length=64)
    titulo: str = Field(min_length=1, max_length=300)
    artista: str = Field(min_length=1, max_length=300)


@ruteador.post("/e/{slug}/elegir-buscada")
async def elegir_buscada(
    slug: str, cuerpo: EleccionBuscada, sesion: Session = Depends(con_sesion)
) -> dict:
    """Registra una canción que la persona busco por grupo.

    Entra al repertorio del evento --no prioritaria-- porque una canción que
    alguien pidio a mano es exactamente el tipo de canción que le puede gustar
    a otro: el repertorio aprende de lo que la gente busca.
    """
    evento = evento_o_404(sesion, slug)
    if evento.estado == "cerrado":
        raise HTTPException(409, {"error": "evento_cerrado"})
    participante = participante_de(sesion, evento, cuerpo.dispositivo)

    from app.descubrimiento import CancionDescubierta, importar

    descubierta = CancionDescubierta(
        titulo=cuerpo.titulo, artista=cuerpo.artista, fuente="busqueda"
    )
    importar.al_repertorio(sesion, evento, [descubierta], prioritario=False)

    from app.catalogo import id_curado
    from app.descubrimiento.normalizar import limpiar_artista, limpiar_titulo

    proveedor_id = id_curado(limpiar_titulo(cuerpo.titulo), limpiar_artista(cuerpo.artista))
    cancion = sesion.scalar(
        select(Cancion).where(Cancion.proveedor == "curado", Cancion.proveedor_id == proveedor_id)
    )
    if cancion is None:
        raise HTTPException(422, {"error": "no_se_pudo_agregar"})

    ya = sesion.scalar(
        select(Solicitud).where(
            Solicitud.evento_id == evento.id,
            Solicitud.participante_id == participante.id,
            Solicitud.cancion_id == cancion.id,
        )
    )
    if ya is None:
        sesion.add(
            Solicitud(
                evento_id=evento.id,
                participante_id=participante.id,
                cancion_id=cancion.id,
                ronda=participante.ronda_actual or 1,
            )
        )
        try:
            sesion.commit()
            await _empujar_eleccion(sesion, evento, cancion.id, cancion.proveedor_id)
            if spotify_cola.corresponde(sesion, evento, cancion.id):
                spotify_cola.encolar(sesion, evento, cancion.id)
        except IntegrityError:
            sesion.rollback()

    return {"evento": _evento_json(evento), "juego": _juego_o_422(sesion, evento, participante)}


# ────────────────── el consentimiento de Spotify ──────────────────
#
# PUBLICO a proposito, y no es un descuido. Spotify redirige el navegador de la
# persona hasta aca, y si esta ruta exigiera sesion de Authelia el codigo se
# perderia --que es exactamente lo que estaba pasando--. Lo que la protege es
# el `state`: un valor que emitimos nosotros, de un solo uso y con
# vencimiento. Sin `state` valido no se guarda nada.


@ruteador.get("/admin/spotify/callback")
async def spotify_callback(
    code: str = "", state: str = "", error: str = "",
    sesion: Session = Depends(con_sesion),
) -> HTMLResponse:
    from app.modelos import SpotifyCuenta
    from app.spotify import cliente

    def pagina(mensaje: str, codigo: int = 200) -> HTMLResponse:
        return HTMLResponse(
            "<!doctype html><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            "<link rel=stylesheet href=/estaticos/fuentes.css>"
            "<link rel=stylesheet href=/estaticos/tinker.css>"
            f"<main class=movil><div class='cierre portada'>{mensaje}</div></main>",
            status_code=codigo,
        )

    if error or not code:
        return pagina(f"<h2>No se autorizó</h2><p>{escape(error) or 'sin código'}</p>", 400)
    if not cliente.consumir_estado(state):
        # Vencido, repetido o inventado. Las tres cosas se responden igual.
        return pagina(
            "<h2>El enlace ya no vale</h2>"
            "<p>Pedí uno nuevo desde el panel y volvé a intentar.</p>", 400
        )

    esquema = "https"
    redireccion = f"{esquema}://tinker.onda.study/api/admin/spotify/callback"
    try:
        datos = await cliente.canjear_codigo(code, redireccion)
        yo = await cliente.quien_soy(datos["access_token"])
    except Exception as fallo:  # noqa: BLE001
        registro.warning("spotify callback: %s", fallo)
        return pagina("<h2>No se pudo completar</h2><p>Probá de nuevo.</p>", 400)

    sesion.execute(delete(SpotifyCuenta))
    sesion.add(
        SpotifyCuenta(
            usuario=yo.get("display_name") or yo.get("id"),
            refresh_token=datos["refresh_token"],
            scopes=datos.get("scope"),
        )
    )
    sesion.commit()
    cliente.olvidar_token_usuario()
    registro.info("spotify conectado como %s", yo.get("id"))
    return pagina(
        "<h2>¡Listo!</h2><p>Spotify quedó conectado como <b>"
        f"{escape(yo.get('display_name') or yo.get('id') or '')}</b>.</p>"
        "<p>Las canciones elegidas empiezan a entrar a la playlist "
        "en el próximo minuto.</p>"
    )


# ───────────────────────────── pantalla ─────────────────────────────

@ruteador.get("/e/{slug}/sonando")
async def ver_sonando(slug: str, sesion: Session = Depends(con_sesion)) -> dict:
    """Lo que suena AHORA y lo que sigue, anotado con los votos del evento.

    Sale del cache que refresca el job: si hay tres televisores abiertos, los
    tres leen la misma llamada a Spotify. Sin eso, cada pantalla que se conecta
    es un pedido mas a una API que ya nos esta limitando.
    """
    evento = evento_o_404(sesion, slug)
    return await reproduccion.leer(sesion, evento)


@ruteador.get("/e/{slug}/panorama")
async def ver_panorama(slug: str, sesion: Session = Depends(con_sesion)) -> dict:
    """Todo lo que muestra el televisor. La pantalla lo pide UNA vez al abrir y
    despues vive del WebSocket; tambien es la red de seguridad si el socket se
    cae y hay que reconstruir el estado."""
    evento = evento_o_404(sesion, slug)
    return panorama.completo(sesion, evento)


@ruteador.get("/e/{slug}/qr.svg")
async def qr(slug: str, pedido: Request, sesion: Session = Depends(con_sesion)) -> Response:
    """El QR del evento, generado en el servidor.

    Nunca cambia durante el evento y no depende de ninguna libreria en el
    navegador: el televisor del salon puede ser cualquier cosa con un navegador
    viejo, y un QR que no dibuja es un evento sin gente.
    """
    evento = evento_o_404(sesion, slug)
    # Detras del proxy, request.url dice http://tinker_web:8000. Las cabeceras
    # que manda nginx son las que saben el nombre publico.
    esquema = pedido.headers.get("x-forwarded-proto", pedido.url.scheme)
    anfitrion = pedido.headers.get("x-forwarded-host") or pedido.headers.get("host", "")
    destino = f"{esquema}://{anfitrion}/{evento.slug}"
    # `save(kind="svg")` y NO `svg_inline()`: el segundo omite el xmlns porque
    # esta pensado para incrustar el SVG dentro del HTML. Servido por <img>,
    # un SVG sin xmlns no lo dibuja NINGUN navegador, y no da error: queda el
    # recuadro del alt, que es lo que aparecio en la primera prueba.
    salida = io.BytesIO()
    segno.make(destino, error="m").save(salida, kind="svg", scale=10, dark="#000000", light=None)
    return Response(
        content=salida.getvalue(),
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=300"},
    )
