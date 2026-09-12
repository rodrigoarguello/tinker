"""El esquema: ocho tablas.

Dos reglas ordenan todo el modelo y conviene leerlas antes de tocar nada.

LA PRIMERA: una fila por canción, muchas solicitudes. Si tres personas eligen
"De musica ligera", en `canciones` hay UNA fila y en `solicitudes` hay tres. El
ranking no se guarda en ningun lado -- se cuenta. Un contador denormalizado es
una segunda verdad que se desincroniza el dia que alguien borre una fila.

LA SEGUNDA: la tanda servida se guarda. Las cinco tarjetas que vio un telefono
en una ronda son filas, no un array en memoria. Eso resuelve cuatro cosas de un
saque: recargar la pagina no pierde nada, el prefetch no puede dispararse dos
veces, un telefono no puede gastar llamadas al modelo sin fin (hay a lo sumo
`rondas` tandas por participante, por forma de los datos), y la fila de la
tanda ES el libro de gastos de la noche.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.bd import Base

ESTADOS = ("previo", "en_vivo", "cerrado")

# El ciclo de vida de una canción en el catalogo global. NEW entra, RISING
# sube, HOT manda hoy, STABLE anda siempre, CLASSIC no depende de la moda,
# LOW_ROTATION descansa, QUARANTINE queda afuera de las recomendaciones
# automaticas y ARCHIVED sale de la rotacion. **Ninguno borra nada**: una
# canción que hoy nadie quiere puede ser la de la fiesta del mes que viene.
ESTADOS_CATALOGO = (
    "NEW", "RISING", "HOT", "STABLE", "CLASSIC", "LOW_ROTATION", "QUARANTINE", "ARCHIVED",
)
ACTIVOS = ("NEW", "RISING", "HOT", "STABLE", "CLASSIC")

VERSIONES = ("ORIGINAL", "REMIX", "LIVE", "ACOUSTIC", "COVER", "REMASTER", "OTHER")

# Lo que le pasa a una canción despues de que se mostro. `no_elegida` es una
# señal debil --otra gano--; `rechazada` es fuerte --la persona dijo que
# ninguna de las cinco le gustaba--. Confundirlas es castigar a una canción
# por haber competido contra otra mejor.
RESULTADOS = ("pendiente", "elegida", "no_elegida", "rechazada")

# Quien armo una tanda. Es la primera pregunta cuando una tanda sale rara.
ORIGENES = ("semilla", "modelo", "respaldo")

# Las etiquetas con las que el recomendador arma diversidad. Viven en el codigo
# y no en tablas de catalogo: son listas cortas que cambian una vez al año, y
# una tabla para eso agrega un join a cada consulta sin dar nada a cambio.
GENEROS = (
    "cumbia", "rock", "pop", "reggaeton", "paraguaya", "brasilera",
    "ranchera", "80-90", "electronica", "romantica", "otros",
)
IDIOMAS = ("es", "pt", "en", "gn", "otro")
EPOCAS = ("60", "70", "80", "90", "00", "10", "20")


class Evento(Base):
    """Una fiesta. El QR pertenece al evento y no cambia nunca: lo que cambia
    es lo que el evento devuelve segun su estado."""

    __tablename__ = "eventos"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(60), unique=True)
    nombre: Mapped[str] = mapped_column(String(160))
    lugar: Mapped[str | None] = mapped_column(String(160))
    fecha: Mapped[date | None] = mapped_column(Date)
    estado: Mapped[str] = mapped_column(String(10), default="previo", server_default="previo")
    # Cuantas veces juega cada telefono. Por evento y no fijo en el codigo:
    # con `rondas = 2` una demo en un stand se juega en veinte segundos y las
    # pruebas corren en un tercio del tiempo. El producto dice cinco.
    rondas: Mapped[int] = mapped_column(Integer, default=5, server_default="5")
    # Techo de llamadas al modelo de TODO el evento. Alcanzado, las tandas
    # siguen saliendo --deterministas-- y nadie ve un error. Es el corte del
    # bucle de gasto, no un limite de calidad.
    tope_ia: Mapped[int] = mapped_column(Integer, default=1500, server_default="1500")
    # Lo que el dueño pego en /admin, tal cual. Sirve para reimportar sin
    # volver a buscar el archivo, y para ver que se quiso cargar cuando una
    # linea no se pudo resolver.
    notas_repertorio: Mapped[str | None] = mapped_column(Text)
    playlist_url: Mapped[str | None] = mapped_column(Text)
    # El contexto: una boda y una discoteca no quieren los mismos pesos. No
    # crea reglas rigidas -- mueve el puntaje, no lo reemplaza.
    tipo_evento: Mapped[str] = mapped_column(String(14), default="FIESTA", server_default="FIESTA")
    # Cuanto de cada tanda sigue el gusto detectado y cuanto explora. La
    # proporcion la impone Python; pedirsela al modelo y confiar es como
    # pedirle que devuelva exactamente cinco.
    explotar_pct: Mapped[int] = mapped_column(SmallInteger, default=80, server_default="80")
    # Cuando entra una canción a la playlist de Spotify. Nulo = lo que diga la
    # configuracion global.
    spotify_modo: Mapped[str | None] = mapped_column(String(20))
    spotify_votos: Mapped[int | None] = mapped_column(SmallInteger)
    # Cuantas de las cinco tarjetas SE OBLIGAN a salir del pool prioritario.
    # CERO por defecto desde que el motor es adaptativo: una cuota dura de tres
    # de cinco le dejaba al agente dos cartas para adaptarse, y si el salon
    # converge a otro genero la lista lo impide en vez de acompañarlo. Los
    # grupos del dueño entran igual, por `reglas.bono_prioritaria`: un empuje
    # fuerte mientras no sabemos nada, que se desvanece cuando ya sabemos.
    # En uno o mas, vuelve la cuota dura para quien la quiera.
    prioritarias_por_tanda: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")
    creado: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint(f"estado in {ESTADOS}", name="ck_eventos_estado"),
        CheckConstraint("rondas between 1 and 10", name="ck_eventos_rondas"),
        CheckConstraint("tope_ia >= 0", name="ck_eventos_tope_ia"),
    )


class Participante(Base):
    """Un telefono. NO una persona: no sabemos quien es y no queremos saberlo.

    `dispositivo` es el UUID que genera el navegador y guarda en localStorage.
    Si alguien borra los datos del sitio, vuelve como un participante nuevo, y
    esta bien: el precio de no tener login.
    """

    __tablename__ = "participantes"

    id: Mapped[int] = mapped_column(primary_key=True)
    evento_id: Mapped[int] = mapped_column(ForeignKey("eventos.id", ondelete="CASCADE"))
    dispositivo: Mapped[str] = mapped_column(String(64))
    # Rondas de mas para quien termino y quiere seguir. Por PARTICIPANTE y no
    # del evento: subir `eventos.rondas` a mitad de fiesta le cambiaria el
    # juego a todos, incluida la gente que ya termino.
    rondas_extra: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")
    # La tanda que esta mirando. Desde que se pueden elegir VARIAS canciones de
    # cada tanda, la ronda ya no se puede derivar de las elecciones: hay rondas
    # con tres y rondas con ninguna.
    ronda_actual: Mapped[int] = mapped_column(SmallInteger, default=1, server_default="1")
    creado: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    evento: Mapped[Evento] = relationship()

    __table_args__ = (
        # Un dispositivo es un participante POR EVENTO. El mismo telefono en la
        # fiesta del sabado y en la del domingo son dos participantes.
        UniqueConstraint("evento_id", "dispositivo", name="uq_participante_dispositivo"),
    )


class Cancion(Base):
    """La identidad de una canción. UNA fila por canción, global.

    `proveedor` + `proveedor_id` es la identidad y no cambia nunca: hay
    solicitudes apuntandole. `curado` es la identidad que se acuña cuando una
    canción entra pegada a mano, con un hash del titulo y el artista -- asi el
    mismo tema cargado en dos eventos distintos es la MISMA fila, y por lo
    tanto la misma en el ranking.

    Aca NO van las etiquetas (genero, epoca, idioma): esas son del repertorio
    de cada evento, porque el dueño de una fiesta tiene que poder corregir
    "esto aca es cumbia" sin cambiarselo a otro.
    """

    __tablename__ = "canciones"

    id: Mapped[int] = mapped_column(primary_key=True)
    proveedor: Mapped[str] = mapped_column(String(20))
    proveedor_id: Mapped[str] = mapped_column(String(120))
    titulo: Mapped[str] = mapped_column(String(300))
    artista: Mapped[str] = mapped_column(String(300))
    album: Mapped[str | None] = mapped_column(String(300))
    imagen: Mapped[str | None] = mapped_column(Text)
    duracion_ms: Mapped[int | None] = mapped_column(Integer)
    # Lo que Spotify manda con cada canción y que se tiraba. Viven aca y no en
    # `catalogo_musical` porque el pozo de una tanda se arma de
    # `repertorio ⨝ canciones`: una señal puesta en el catalogo global se queda
    # en el panel y no cambia una sola tarjeta.
    #
    # `popularidad` queda NULL mientras la app este en modo Development --ahi
    # Spotify no manda el campo-- y el puntaje colectivo se reparte sin el.
    popularidad: Mapped[float | None] = mapped_column(Float)   # 0..1
    anio: Mapped[int | None] = mapped_column(SmallInteger)
    explicito: Mapped[bool | None] = mapped_column(Boolean)
    # El puente para el dia que haya credenciales de Spotify: se ENRIQUECE, no
    # se reemplaza. Una canción que entro como `curado` no puede cambiar de
    # identidad, asi que un pase de re-resolucion llena esto y el resultado
    # empieza a emitir `spotify:track:` sin tocar una sola solicitud.
    spotify_id: Mapped[str | None] = mapped_column(String(40))

    __table_args__ = (
        UniqueConstraint("proveedor", "proveedor_id", name="uq_cancion_proveedor"),
        CheckConstraint(
            "popularidad is null or popularidad between 0 and 1",
            name="ck_cancion_popularidad",
        ),
    )


class Repertorio(Base):
    """Lo que se puede elegir en ESTE evento, y como es cada canción.

    El repertorio es la unica fuente: la lista curada que pega el dueño en
    /admin es todo lo que la gente puede llegar a ver. Sin esto, la fiesta de
    un quinceañero y la de una empresa elegirian del mismo pozo.
    """

    __tablename__ = "repertorio"

    id: Mapped[int] = mapped_column(primary_key=True)
    evento_id: Mapped[int] = mapped_column(ForeignKey("eventos.id", ondelete="CASCADE"))
    cancion_id: Mapped[int] = mapped_column(ForeignKey("canciones.id", ondelete="CASCADE"))
    orden: Mapped[int] = mapped_column(Integer)
    # La linea pegada, exacta. Es lo que se muestra cuando algo no se pudo
    # resolver: "no encontre esto" con el texto de la persona y no una version
    # normalizada que no reconoce.
    linea: Mapped[str] = mapped_column(Text)
    # El artista NORMALIZADO. "Shakira" y "Shakira, Bizarrap" son el mismo
    # artista para la regla de «no dos del mismo en una tanda», y normalizar
    # trescientas filas en cada tanda es trabajo repetido.
    artista_clave: Mapped[str] = mapped_column(String(120))
    genero: Mapped[str | None] = mapped_column(String(20))
    epoca: Mapped[str | None] = mapped_column(String(4))
    idioma: Mapped[str | None] = mapped_column(String(4))
    # 1 lenta ... 5 reventada. Puntaje blando, nunca regla dura: una etiqueta
    # que falta no puede impedir que salga una tanda.
    intensidad: Mapped[int | None] = mapped_column(SmallInteger)
    # De un grupo del pool prioritario. Tiene cuota dura por tanda: no compite
    # por puntaje, entra.
    prioritario: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    creado: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    cancion: Mapped[Cancion] = relationship()

    __table_args__ = (
        UniqueConstraint("evento_id", "cancion_id", name="uq_repertorio"),
        CheckConstraint(
            "intensidad is null or intensidad between 1 and 5",
            name="ck_repertorio_intensidad",
        ),
        # El pozo se lee filtrando por evento y estratificando por genero.
        Index("ix_repertorio_pozo", "evento_id", "genero"),
        Index("ix_repertorio_artista", "evento_id", "artista_clave"),
    )


class Tanda(Base):
    """Las cinco tarjetas que se le sirvieron a un telefono en una ronda.

    NO tiene `estado` ni `elegida_id`, y eso es deliberado: si una tanda fue
    usada se sabe mirando `solicitudes`. Guardarlo aparte seria una segunda
    verdad, y el bug aparece al quitar un chip -- la solicitud borrada y
    `elegida_id` apuntando a la canción vieja.
    """

    __tablename__ = "tandas"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    evento_id: Mapped[int] = mapped_column(ForeignKey("eventos.id", ondelete="CASCADE"))
    participante_id: Mapped[int] = mapped_column(ForeignKey("participantes.id", ondelete="CASCADE"))
    ronda: Mapped[int] = mapped_column(SmallInteger)
    origen: Mapped[str] = mapped_column(String(10))
    proveedor: Mapped[str | None] = mapped_column(String(10))
    modelo: Mapped[str | None] = mapped_column(String(60))
    tokens_entrada: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    tokens_salida: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    ms: Mapped[int | None] = mapped_column(Integer)
    # La linea corta que SI se le mostro a la persona ("el rock esta creciendo
    # entre las elecciones"), y lo que el modelo leyo del salon en ese momento.
    # Guardarlas es lo que permite reconstruir despues que entendio el agente a
    # las once y media, que es una pregunta que se hace siempre y tarde.
    mensaje: Mapped[str | None] = mapped_column(String(160))
    lectura: Mapped[dict | None] = mapped_column(JSON)
    # La frase del modelo explicando su tanda. No se le muestra a nadie: es
    # para entender una tanda rara sin volver a llamar a la API.
    porque: Mapped[str | None] = mapped_column(Text)
    creada: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint("ronda between 1 and 10", name="ck_tandas_ronda"),
        CheckConstraint(f"origen in {ORIGENES}", name="ck_tandas_origen"),
        # ESTA es la restriccion que sostiene el diseño entero: hace el
        # prefetch idempotente, hace gratis recargar la pagina, y pone un techo
        # duro al gasto por telefono sin un solo contador.
        UniqueConstraint("participante_id", "ronda", name="uq_tanda_ronda"),
    )


class TandaCancion(Base):
    """Una tarjeta de una tanda. Cinco por tanda, en orden de pantalla."""

    __tablename__ = "tanda_canciones"

    tanda_id: Mapped[int] = mapped_column(ForeignKey("tandas.id", ondelete="CASCADE"))
    posicion: Mapped[int] = mapped_column(SmallInteger)
    cancion_id: Mapped[int] = mapped_column(ForeignKey("canciones.id", ondelete="CASCADE"))

    cancion: Mapped[Cancion] = relationship()

    __table_args__ = (
        # Sin `id` propia: `(tanda, posicion)` ya es la identidad natural Y el
        # orden de las tarjetas en la pantalla. Es la unica tabla del esquema
        # que se aparta de la convencion, y este es el motivo.
        PrimaryKeyConstraint("tanda_id", "posicion"),
        # Una de las reglas duras, puesta en la base: la misma canción no puede
        # aparecer dos veces en la misma tanda. Ni por un error del modelo ni
        # por uno nuestro.
        UniqueConstraint("tanda_id", "cancion_id", name="uq_tanda_sin_repetidas"),
        CheckConstraint("posicion between 1 and 5", name="ck_tanda_posicion"),
    )


class Solicitud(Base):
    """Una eleccion: este telefono eligio esta canción en esta ronda."""

    __tablename__ = "solicitudes"

    id: Mapped[int] = mapped_column(primary_key=True)
    evento_id: Mapped[int] = mapped_column(ForeignKey("eventos.id", ondelete="CASCADE"))
    participante_id: Mapped[int] = mapped_column(ForeignKey("participantes.id", ondelete="CASCADE"))
    cancion_id: Mapped[int] = mapped_column(ForeignKey("canciones.id", ondelete="CASCADE"))
    # En que ronda se eligio. `0` son las filas del mundo viejo --el buscador--
    # y de esas puede haber muchas por participante.
    ronda: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")
    creado: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    cancion: Mapped[Cancion] = relationship()

    __table_args__ = (
        # Impide que la misma canción se vote dos veces desde el mismo
        # telefono. Esta en la base y no solo en el codigo: una validacion que
        # vive unicamente en Python se esquiva con dos toques que llegan a la
        # vez. Ademas cubre "quitar el chip y volver a elegir lo mismo".
        UniqueConstraint("evento_id", "participante_id", "cancion_id", name="uq_solicitud"),
        # NO hay unique por ronda: de una tanda de cinco se pueden elegir
        # varias, y eso es el punto. Lo que sigue habiendo es `uq_solicitud`,
        # que impide votar dos veces la misma canción.
        Index("ix_solicitudes_mias", "evento_id", "participante_id", "ronda"),
        # El ranking es un GROUP BY sobre estas dos columnas y corre cada vez
        # que el televisor se actualiza.
        Index("ix_solicitudes_ranking", "evento_id", "cancion_id"),
        Index("ix_solicitudes_recientes", "evento_id", "creado"),
        CheckConstraint("ronda between 0 and 10", name="ck_solicitudes_ronda"),
    )


class Gusto(Base):
    """Los generos que marcaba un telefono en la pantalla previa.

    ⚠ EN RETIRO. La pantalla de generos ya no existe: el juego entra directo y
    el gusto se deduce de lo que la persona elige, que ademas es mas honesto
    que una encuesta. La tabla sigue aca --sin llenarse-- porque dropearla en
    la misma migracion del cambio grande seria apostar a que nada se revierta.
    La borra la 0003 cuando el juego este confirmado en una fiesta de verdad.
    """

    __tablename__ = "gustos"

    id: Mapped[int] = mapped_column(primary_key=True)
    participante_id: Mapped[int] = mapped_column(ForeignKey("participantes.id", ondelete="CASCADE"))
    genero: Mapped[str] = mapped_column(String(20))

    __table_args__ = (
        UniqueConstraint("participante_id", "genero", name="uq_gusto"),
    )


# ═══════════════════════ inteligencia musical ═══════════════════════
#
# Todo lo de aca abajo es el SEGUNDO motor: el que decide que canciones
# deberian formar parte de un repertorio. El primero --el juego de las cinco
# rondas-- no lo conoce y funciona sin el.


class CatalogoMusical(Base):
    """Lo que tinker sabe de una canción, mas alla de su identidad.

    Separado de `canciones` a proposito: alli vive la identidad --proveedor,
    id, titulo, artista-- y no cambia nunca; aca vive lo que SI cambia, que es
    casi todo: si esta de moda, cuanto rinde, en que estado de rotacion esta.

    Y separado del `repertorio` por otra razon: el repertorio dice que se puede
    elegir EN UN EVENTO. Una canción descubierta automaticamente entra aca y
    **no** al juego; pasa al repertorio de un evento cuando alguien --o el
    refill-- lo decide.
    """

    __tablename__ = "catalogo_musical"

    id: Mapped[int] = mapped_column(primary_key=True)
    cancion_id: Mapped[int] = mapped_column(
        ForeignKey("canciones.id", ondelete="CASCADE"), unique=True
    )
    fuente_principal: Mapped[str] = mapped_column(String(30))
    pais: Mapped[str | None] = mapped_column(String(4))
    idioma: Mapped[str | None] = mapped_column(String(4))
    genero: Mapped[str | None] = mapped_column(String(20))
    subgenero: Mapped[str | None] = mapped_column(String(40))
    epoca: Mapped[str | None] = mapped_column(String(4))
    anio: Mapped[int | None] = mapped_column(SmallInteger)
    intensidad: Mapped[int | None] = mapped_column(SmallInteger)
    ambiente: Mapped[str | None] = mapped_column(String(20))
    version: Mapped[str] = mapped_column(String(10), default="ORIGINAL", server_default="ORIGINAL")
    es_paraguaya: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    es_latina: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    es_brasilera: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    es_clasico: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    es_tendencia: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    popularidad_py: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    popularidad_global: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    score_actual: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    estado: Mapped[str] = mapped_column(String(14), default="NEW", server_default="NEW")
    primera_deteccion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    ultima_deteccion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    ultima_actualizacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    cancion: Mapped[Cancion] = relationship()

    __table_args__ = (
        CheckConstraint(f"estado in {ESTADOS_CATALOGO}", name="ck_catalogo_estado"),
        CheckConstraint(f"version in {VERSIONES}", name="ck_catalogo_version"),
        Index("ix_catalogo_estado", "estado", "score_actual"),
        Index("ix_catalogo_genero", "genero", "estado"),
    )


class Exposicion(Base):
    """Una canción que se le MOSTRO a alguien.

    El sistema ya sabia lo elegido; sin esto no sabe lo ofrecido, y sin las dos
    cosas no hay conversion posible. Una canción con cero elecciones puede ser
    una que nadie quiere o una que nadie vio, y la diferencia lo es todo.
    """

    __tablename__ = "exposiciones"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    evento_id: Mapped[int] = mapped_column(ForeignKey("eventos.id", ondelete="CASCADE"))
    participante_id: Mapped[int] = mapped_column(ForeignKey("participantes.id", ondelete="CASCADE"))
    # SET NULL y no CASCADE: «ninguna me gusta» rehace la tanda, y si la
    # exposicion se fuera con ella, las cinco rechazadas volverian a aparecer
    # como si nunca se hubieran mostrado. La exposicion es el registro de que
    # algo se mostro; la tanda, la unidad que lo mostro.
    tanda_id: Mapped[int | None] = mapped_column(
        ForeignKey("tandas.id", ondelete="SET NULL"), nullable=True
    )
    cancion_id: Mapped[int] = mapped_column(ForeignKey("canciones.id", ondelete="CASCADE"))
    ronda: Mapped[int] = mapped_column(SmallInteger)
    posicion: Mapped[int] = mapped_column(SmallInteger)
    mostrada_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    franja: Mapped[str | None] = mapped_column(String(12))
    resultado: Mapped[str] = mapped_column(String(12), default="pendiente", server_default="pendiente")
    resuelta_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("tanda_id", "cancion_id", name="uq_exposicion"),
        CheckConstraint(f"resultado in {RESULTADOS}", name="ck_exposicion_resultado"),
        Index("ix_exposiciones_fatiga", "evento_id", "mostrada_en"),
        Index("ix_exposiciones_metricas", "cancion_id", "resultado"),
        Index("ix_exposiciones_evento", "evento_id", "cancion_id"),
    )


class Rechazo(Base):
    """«Ninguna me gusta». Una tanda entera descartada a proposito.

    Vale mucho mas que cinco `no_elegida` sueltas: la persona se tomo el
    trabajo de decir que no, y eso es lo que dispara el reinicio de diversidad.
    """

    __tablename__ = "rechazos"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    evento_id: Mapped[int] = mapped_column(ForeignKey("eventos.id", ondelete="CASCADE"))
    participante_id: Mapped[int] = mapped_column(ForeignKey("participantes.id", ondelete="CASCADE"))
    tanda_id: Mapped[int] = mapped_column(ForeignKey("tandas.id", ondelete="CASCADE"))
    ronda: Mapped[int] = mapped_column(SmallInteger)
    creado: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("tanda_id", name="uq_rechazo_tanda"),
        Index("ix_rechazos_seguidos", "participante_id", "creado"),
    )


class EventoPerfil(Base):
    """Lo que le gusta al EVENTO, no a una persona.

    Se usa con peso chico: inclina las tandas de quien recien llega hacia lo
    que viene funcionando en el salon, sin reemplazar lo que esa persona
    elija. Una fiesta tiene un gusto propio y el primero que entra a las tres
    de la mañana no deberia empezar de cero.
    """

    __tablename__ = "evento_perfil"

    evento_id: Mapped[int] = mapped_column(
        ForeignKey("eventos.id", ondelete="CASCADE"), primary_key=True
    )
    generos: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    decadas: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    idiomas: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    artistas: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    paises: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    rechazados: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    intensidad_promedio: Mapped[float | None] = mapped_column(Float)
    elecciones: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    actualizado: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # ── el perfil PONDERADO ───────────────────────────────────────────────
    #
    # Los de arriba son conteos crudos: una eleccion de hace dos horas vale lo
    # mismo que una de hace un minuto, y una canción que eligieron seis
    # personas vale lo mismo que una que eligio una. Para el PANEL eso esta
    # bien --son totales de la noche-- pero para armar la tanda siguiente es
    # justo lo contrario de lo que hace falta.
    #
    # `ponderado` guarda lo mismo con dos correcciones: lo reciente pesa mas
    # (vida media de veinte minutos) y lo que varios eligieron pesa mas (pero
    # no linealmente). Se recalcula en cada eleccion, no cada dos minutos.
    ponderado: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    energia: Mapped[float | None] = mapped_column(Float)      # 0..1
    mainstream: Mapped[float | None] = mapped_column(Float)   # 0..1
    # Cuanto vale este perfil con la gente que hay. Con tres personas, «el
    # gusto del salon» es el gusto de tres: el termino colectivo se escala con
    # esto y lo que cede se lo lleva el gusto individual.
    confianza: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    calculado: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SpotifyCola(Base):
    """Una canción esperando entrar a la playlist.

    La eleccion de la persona YA esta guardada cuando esta fila nace. Que
    Spotify la acepte, la rechace o tarde una hora no cambia nada de lo que
    paso en el telefono: por eso es una cola y no una llamada.
    """

    __tablename__ = "spotify_cola"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    evento_id: Mapped[int] = mapped_column(ForeignKey("eventos.id", ondelete="CASCADE"))
    cancion_id: Mapped[int] = mapped_column(ForeignKey("canciones.id", ondelete="CASCADE"))
    playlist_id: Mapped[str] = mapped_column(String(40))
    spotify_id: Mapped[str | None] = mapped_column(String(40))
    estado: Mapped[str] = mapped_column(String(12), default="PENDING", server_default="PENDING")
    intentos: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")
    ultimo_error: Mapped[str | None] = mapped_column(Text)
    creado: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ultimo_intento: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    proximo_intento: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    enviado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    cancion: Mapped[Cancion] = relationship()

    __table_args__ = (
        CheckConstraint(
            "estado in ('PENDING','SENDING','ADDED','DUPLICATE','ERROR','ABANDONED')",
            name="ck_spotify_estado",
        ),
        # Una canción por playlist, garantizado por la base: dos personas
        # eligiendo lo mismo a la vez son dos pedidos que llegan juntos, y el
        # codigo no puede arbitrar eso.
        UniqueConstraint("playlist_id", "cancion_id", name="uq_spotify_playlist_cancion"),
        Index("ix_spotify_pendientes", "estado", "proximo_intento"),
    )


class SpotifyCuenta(Base):
    """La cuenta que autorizo escribir en la playlist. Una sola fila.

    El refresh token vive aca porque no hay mejor lugar: es un secreto de larga
    vida y la base ya lo es. Borrar la fila desconecta; revocar de verdad se
    hace desde la cuenta de Spotify.
    """

    __tablename__ = "spotify_cuenta"

    id: Mapped[int] = mapped_column(primary_key=True)
    usuario: Mapped[str | None] = mapped_column(String(80))
    refresh_token: Mapped[str] = mapped_column(Text)
    scopes: Mapped[str | None] = mapped_column(Text)
    conectado: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ultimo_uso: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FuenteEstado(Base):
    """Como le fue a cada fuente de descubrimiento la ultima vez.

    Una fuente caida no puede romper tinker ni pasar desapercibida: las dos
    cosas a la vez son el motivo de esta tabla.
    """

    __tablename__ = "fuentes_estado"

    nombre: Mapped[str] = mapped_column(String(40), primary_key=True)
    estado: Mapped[str] = mapped_column(String(10), default="OK", server_default="OK")
    ultima_ejecucion: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    encontradas: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    nuevas: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    duracion_ms: Mapped[int | None] = mapped_column(Integer)
    ultimo_error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint("estado in ('OK','DEGRADED','ERROR','DISABLED')", name="ck_fuente_estado"),
    )


class ArtistaPrioritario(Base):
    """Un grupo que el dueño del evento quiere que suene sí o sí.

    Se carga por NOMBRE --"Foo Fighters"-- y el sistema sale a buscar sus temas
    mas conocidos. La lista se guarda para poder volver a buscar: un grupo saca
    disco nuevo y el repertorio se actualiza sin volver a pegar nada.
    """

    __tablename__ = "artistas_prioritarios"

    id: Mapped[int] = mapped_column(primary_key=True)
    evento_id: Mapped[int] = mapped_column(ForeignKey("eventos.id", ondelete="CASCADE"))
    nombre: Mapped[str] = mapped_column(String(160))
    artista_clave: Mapped[str] = mapped_column(String(120))
    # Lo que el dueño escribio al lado del nombre ("rock alternativo / hard
    # rock"). No se interpreta: se guarda para que se vea en /admin.
    nota: Mapped[str | None] = mapped_column(Text)
    canciones: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    ultima_carga: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    creado: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("evento_id", "artista_clave", name="uq_artista_prioritario"),
    )
