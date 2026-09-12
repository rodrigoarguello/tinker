"""Los trabajos de fondo. NINGUNO corre dentro de un pedido del juego.

Es el principio que ordena todo este bloque: descubrir musica, calcular
scores, rellenar el catalogo, mover estados de rotacion y hablar con Spotify
pasan LEJOS del telefono de la gente. El juego tiene que seguir funcionando
aunque no haya internet, fallen todas las fuentes, falle Spotify y fallen los
modelos -- el repertorio ya persistido siempre es el respaldo.

Cada job:
  · atrapa sus propios errores y nunca levanta;
  · deja anotado como le fue en `fuentes_estado`, que es lo que se ve en
    /admin;
  · es idempotente: correrlo dos veces no duplica nada.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import config
from app.bd import Sesion
from app.descubrimiento import AppleMusic, Deezer, importar
from app.jobs import metricas
from app.modelos import (
    ACTIVOS,
    Cancion,
    CatalogoMusical,
    Evento,
    EventoPerfil,
    Exposicion,
    FuenteEstado,
    Repertorio,
    Solicitud,
)

registro = logging.getLogger("tinker.jobs")


def anotar(sesion: Session, nombre: str, estado: str, **datos) -> None:
    """Como le fue a una fuente. Una fuente caida no puede romper tinker ni
    pasar desapercibida: las dos cosas a la vez son el motivo de esta tabla."""
    fila = sesion.get(FuenteEstado, nombre)
    if fila is None:
        fila = FuenteEstado(nombre=nombre)
        sesion.add(fila)
    fila.estado = estado
    fila.ultima_ejecucion = datetime.now(timezone.utc)
    fila.encontradas = datos.get("encontradas", 0)
    fila.nuevas = datos.get("nuevas", 0)
    fila.duracion_ms = datos.get("duracion_ms")
    fila.ultimo_error = datos.get("error")
    sesion.commit()


async def _descubrir(nombre: str, traer) -> dict:
    """El armazon de cualquier job de descubrimiento.

    `traer` es una corrutina que devuelve `CancionDescubierta`. Todo lo demas
    --medir, importar, anotar, no levantar nunca-- es igual para todas las
    fuentes y vive aca una sola vez.
    """
    arranque = time.monotonic()
    try:
        crudas = await traer()
    except Exception as error:  # noqa: BLE001
        registro.warning("%s fallo: %s", nombre, error)
        with Sesion() as sesion:
            anotar(sesion, nombre, "ERROR", error=str(error)[:400],
                   duracion_ms=int((time.monotonic() - arranque) * 1000))
        return {"error": str(error)[:200]}

    with Sesion() as sesion:
        resultado = importar.al_catalogo(sesion, crudas)
        # DEGRADED y no ERROR cuando no trajo nada: la fuente contesto, pero no
        # sirvio. La diferencia importa cuando alguien mira el panel para
        # decidir si hay algo roto.
        estado = "OK" if crudas else "DEGRADED"
        anotar(sesion, nombre, estado, encontradas=resultado["unicas"],
               nuevas=resultado["nuevas"],
               duracion_ms=int((time.monotonic() - arranque) * 1000))
    registro.info("%s: %s", nombre, resultado)
    return resultado


# ── los jobs de descubrimiento ────────────────────────────────────────────

async def descubrir_paraguay() -> dict:
    """Lo que suena HOY en Paraguay, mas la musica paraguaya de siempre.

    Es el job mas importante de los tres: es la unica fuente gratuita que da
    tendencias de Paraguay, y es lo que impide que el catalogo se vuelva una
    radio internacional.
    """
    async def traer():
        apple, deezer = AppleMusic(), Deezer()
        salida = await apple.buscar_nuevas(pais="py", limite=50)
        for termino in ("musica paraguaya", "polka paraguaya", "guarania",
                        "cumbia paraguaya", "rock paraguayo"):
            encontradas = await deezer.por_busqueda(termino, "paraguaya", limite=20)
            # Marcadas como paraguayas por la BUSQUEDA, no por el pais de la
            # fuente: Deezer no dice de donde es un artista.
            salida += [
                type(c)(**{**c.__dict__, "pais": "py"}) for c in encontradas
            ]
        return salida

    return await _descubrir("paraguay", traer)


async def descubrir_global() -> dict:
    async def traer():
        apple = AppleMusic()
        salida = []
        for pais in config.PAISES_TENDENCIA:
            if pais == "py":
                continue
            salida += await apple.buscar_nuevas(pais=pais, limite=40)
        salida += await Deezer().buscar_nuevas(limite=50)
        return salida

    return await _descubrir("global", traer)


async def descubrir_generos() -> dict:
    return await _descubrir("generos", lambda: Deezer().por_generos(limite_por_genero=25))


# ── scoring, rotacion y refill ────────────────────────────────────────────

def puntuar() -> dict:
    """El score global de cada canción del catalogo.

    Es el score MUSICAL, distinto del puntaje por persona que calcula
    `reglas._puntaje`. Este dice «que tan buena es esta canción para una
    fiesta»; el otro, «que tan buena es para VOS ahora».
    """
    pesos = config.PESOS_SCORE
    ahora = datetime.now(timezone.utc)
    with Sesion() as sesion:
        medidas = metricas.todas(sesion)
        filas = sesion.execute(select(CatalogoMusical)).scalars().all()
        for fila in filas:
            medida = medidas.get(fila.cancion_id, {})
            conversion = medida.get("conversion")
            rechazo = medida.get("ratio_rechazo")
            dias = max(0, (ahora - fila.primera_deteccion).days)
            # La novedad se apaga en dos semanas. Una canción nueva merece una
            # oportunidad; dos meses despues, que se sostenga por lo que rinde.
            novedad = max(0.0, 1 - dias / 14)

            score = (
                pesos["popularidad_py"] * (fila.popularidad_py or 0)
                + pesos["popularidad_global"] * (fila.popularidad_global or 0)
                + pesos["tendencia"] * (1.0 if fila.es_tendencia else 0.0)
                + pesos["novedad"] * novedad
                + pesos["paraguaya"] * (1.0 if fila.es_paraguaya else 0.0)
            )
            # La conversion solo entra cuando hay muestra suficiente. Sin eso,
            # una canción con una exposicion y una eleccion tendria el score
            # mas alto del catalogo.
            if conversion is not None:
                score += pesos["conversion"] * conversion
            if rechazo is not None:
                score -= pesos["rechazo"] * rechazo
            fila.score_actual = round(score, 4)
            fila.ultima_actualizacion = ahora
        sesion.commit()
        return {"puntuadas": len(filas)}


def rotar() -> dict:
    """Mueve los estados de rotacion. NUNCA borra nada.

    Una canción que hoy nadie quiere puede ser la de la fiesta del mes que
    viene: el peor estado posible es ARCHIVED, y de ahi se vuelve.
    """
    cambios: dict[str, int] = {}
    ahora = datetime.now(timezone.utc)
    with Sesion() as sesion:
        medidas = metricas.todas(sesion)
        for fila in sesion.execute(select(CatalogoMusical)).scalars().all():
            medida = medidas.get(fila.cancion_id, {})
            mostrada = medida.get("mostrada", 0)
            rechazo = medida.get("ratio_rechazo")
            anterior = fila.estado

            if fila.estado == "QUARANTINE":
                continue      # de la cuarentena se sale a mano
            elif (rechazo is not None
                  and mostrada >= config.CUARENTENA_EXPOSICIONES
                  and rechazo > config.CUARENTENA_RECHAZO):
                fila.estado = "QUARANTINE"
            elif (rechazo is not None
                  and mostrada >= config.BAJA_ROTACION_EXPOSICIONES
                  and rechazo > config.BAJA_ROTACION_RECHAZO):
                fila.estado = "LOW_ROTATION"
            elif fila.anio and fila.anio < ahora.year - 20 and (
                medida.get("conversion") or 0
            ) >= 0.2:
                # Un tema viejo que la gente sigue eligiendo no es una
                # tendencia caida: es un clasico, y no tiene que competir por
                # novedad con lo de esta semana.
                fila.estado = "CLASSIC"
            elif fila.es_tendencia and fila.score_actual >= 3.0:
                fila.estado = "HOT"
            elif fila.estado == "NEW" and (ahora - fila.primera_deteccion).days >= 14:
                fila.estado = "STABLE"
            elif fila.estado in ("HOT", "RISING") and not fila.es_tendencia:
                fila.estado = "STABLE"

            if fila.estado != anterior:
                cambios[f"{anterior}->{fila.estado}"] = cambios.get(
                    f"{anterior}->{fila.estado}", 0
                ) + 1
        sesion.commit()
    return {"cambios": cambios}


def activas(sesion: Session) -> int:
    return sesion.scalar(
        select(func.count(CatalogoMusical.id)).where(CatalogoMusical.estado.in_(ACTIVOS))
    ) or 0


async def rellenar() -> dict:
    """Si el catalogo activo bajo del minimo, sale a buscar hasta el objetivo.

    NUNCA bloquea una sesion de QR: corre en el planificador, y si tarda diez
    minutos el juego sigue andando con lo que ya hay.
    """
    with Sesion() as sesion:
        cuantas = activas(sesion)
        paraguayas = sesion.scalar(
            select(func.count(CatalogoMusical.id)).where(
                CatalogoMusical.es_paraguaya.is_(True),
                CatalogoMusical.estado.in_(ACTIVOS),
            )
        ) or 0

    falta_paraguay = paraguayas < config.PISO_PARAGUAY
    if cuantas >= config.MIN_CATALOGO_ACTIVO and not falta_paraguay:
        return {"activas": cuantas, "accion": "no hacia falta"}

    registro.info(
        "refill: %d activas (minimo %d), %d paraguayas (piso %d)",
        cuantas, config.MIN_CATALOGO_ACTIVO, paraguayas, config.PISO_PARAGUAY,
    )
    # Paraguay primero SIEMPRE. Si el refill se llenara de tendencias globales,
    # en dos semanas el catalogo seria una radio internacional: el piso
    # paraguayo existe para que eso no pase sin que nadie lo decida.
    resultado = await descubrir_paraguay()
    with Sesion() as sesion:
        cuantas = activas(sesion)
    if cuantas < config.OBJETIVO_CATALOGO_ACTIVO:
        resultado = await descubrir_generos()
    with Sesion() as sesion:
        cuantas = activas(sesion)
    if cuantas < config.OBJETIVO_CATALOGO_ACTIVO:
        await descubrir_global()
        with Sesion() as sesion:
            cuantas = activas(sesion)
    return {"activas": cuantas, "objetivo": config.OBJETIVO_CATALOGO_ACTIVO,
            "accion": "rellenado"}


# ── el perfil colectivo del evento ────────────────────────────────────────

def perfilar() -> dict:
    """Que le gusta a cada evento en vivo, no a cada persona.

    Se recalcula entero cada vez en vez de ir sumando: son dos consultas
    agrupadas sobre indices, y un acumulador que se desincroniza es peor que
    una consulta que tarda cincuenta milisegundos.
    """
    with Sesion() as sesion:
        eventos = sesion.execute(
            select(Evento).where(Evento.estado.in_(("previo", "en_vivo")))
        ).scalars().all()
        for evento in eventos:
            elegidas = sesion.execute(
                select(
                    Repertorio.genero, Repertorio.epoca, Repertorio.idioma,
                    Repertorio.artista_clave, Repertorio.intensidad,
                    func.count(Solicitud.id),
                )
                .join(Solicitud, Solicitud.cancion_id == Repertorio.cancion_id)
                .where(
                    Solicitud.evento_id == evento.id,
                    Repertorio.evento_id == evento.id,
                    Solicitud.ronda > 0,
                )
                .group_by(
                    Repertorio.genero, Repertorio.epoca, Repertorio.idioma,
                    Repertorio.artista_clave, Repertorio.intensidad,
                )
            ).all()

            generos, decadas, idiomas, artistas = {}, {}, {}, {}
            intensidades, total = [], 0
            for genero, epoca, idioma, artista, intensidad, cuantas in elegidas:
                total += cuantas
                if genero:
                    generos[genero] = generos.get(genero, 0) + cuantas
                if epoca:
                    decadas[epoca] = decadas.get(epoca, 0) + cuantas
                if idioma:
                    idiomas[idioma] = idiomas.get(idioma, 0) + cuantas
                if artista:
                    artistas[artista] = artistas.get(artista, 0) + cuantas
                if intensidad:
                    intensidades += [intensidad] * cuantas

            rechazados = dict(
                sesion.execute(
                    select(Repertorio.genero, func.count(Exposicion.id))
                    .join(Exposicion, Exposicion.cancion_id == Repertorio.cancion_id)
                    .where(
                        Exposicion.evento_id == evento.id,
                        Repertorio.evento_id == evento.id,
                        Exposicion.resultado == "rechazada",
                        Repertorio.genero.is_not(None),
                    )
                    .group_by(Repertorio.genero)
                ).all()
            )

            perfil = sesion.get(EventoPerfil, evento.id)
            if perfil is None:
                perfil = EventoPerfil(evento_id=evento.id)
                sesion.add(perfil)
            perfil.generos = generos
            perfil.decadas = decadas
            perfil.idiomas = idiomas
            perfil.artistas = dict(sorted(artistas.items(), key=lambda x: -x[1])[:30])
            perfil.rechazados = rechazados
            perfil.intensidad_promedio = (
                sum(intensidades) / len(intensidades) if intensidades else None
            )
            perfil.elecciones = total
            perfil.actualizado = datetime.now(timezone.utc)
        sesion.commit()
        return {"eventos": len(eventos)}


# ── Spotify ───────────────────────────────────────────────────────────────

# La huella de lo ultimo que se le empujo a CADA televisor. Se publica solo
# cuando cambia: quince segundos de latido inutil por pantalla no le agregan
# nada a nadie, y la tarjeta de «sonando» no tiene que parpadear sin motivo.
_HUELLA_SONANDO: dict[str, str] = {}


def _huella(estado: dict) -> str:
    sonando = estado.get("sonando") or {}
    return "|".join([
        f"{sonando.get('id')}:{sonando.get('votos')}",
        *[f"{p['id']}:{p['votos']}" for p in estado.get("siguen", [])],
    ])


async def sonando() -> dict:
    """Le cuenta al televisor lo que esta sonando y lo que sigue.

    NO LLAMA A SPOTIFY SI NADIE ESTA MIRANDO. Con la app en modo desarrollo y
    un 429 QUOTA_EXCEEDED ya visto hoy, una llamada cada quince segundos
    sostenida toda la noche para nadie es justo lo que no hay que gastar.
    """
    from app.spotify import reproduccion
    from app.tiempo_real.hub import hub

    with Sesion() as sesion:
        vivos = sesion.execute(
            select(Evento).where(Evento.estado == "en_vivo")
        ).scalars().all()
        mirando = [evento for evento in vivos if hub.conectados(evento.slug)]
        if not mirando:
            _HUELLA_SONANDO.clear()
            return {}

        # La primera lectura sale a Spotify; las demas usan el cache, asi que
        # N televisores de N eventos siguen costando UNA llamada.
        primero, publicados = True, 0
        for evento in mirando:
            estado = await reproduccion.leer(sesion, evento, forzar=primero)
            primero = False
            huella = _huella(estado)
            if _HUELLA_SONANDO.get(evento.slug) == huella:
                continue
            _HUELLA_SONANDO[evento.slug] = huella
            await hub.publicar(evento.slug, {"tipo": "sonando", **estado})
            publicados += 1
        return {"pantallas": publicados} if publicados else {}


async def spotify() -> dict:
    """Vacia la cola hacia la playlist. Es el job que hace que la musica suene.

    Corre cada minuto y no le importa si no hay nada: sin cuenta conectada
    devuelve en un milisegundo y sin tocar la red.
    """
    from app.spotify import cola

    with Sesion() as sesion:
        return await cola.procesar(sesion, limite=30)
