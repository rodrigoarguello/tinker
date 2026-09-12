"""Las perillas del sistema musical, en un solo lugar y leidas del entorno.

Nada de esto es una regla del juego: son pesos y umbrales que se calibran
mirando una fiesta de verdad. Por eso viven aca y no repartidos por el codigo,
y por eso todos tienen un valor por defecto razonable -- un despliegue sin una
sola variable de entorno funciona.
"""
from __future__ import annotations

import os
from functools import lru_cache


def _entero(nombre: str, porDefecto: int) -> int:
    try:
        return int(os.environ.get(nombre, porDefecto))
    except ValueError:
        return porDefecto


def _decimal(nombre: str, porDefecto: float) -> float:
    try:
        return float(os.environ.get(nombre, porDefecto))
    except ValueError:
        return porDefecto


# ── tamaño del catalogo global ────────────────────────────────────────────
#
# El minimo no es un capricho: por debajo de eso, el refill se dispara. El
# objetivo es a donde apunta cuando se dispara. No hay techo -- el sistema
# funciona igual con 300 que con 2000, y cuanto mas grande, mejores tandas.
MIN_CATALOGO_ACTIVO = _entero("TINKER_MIN_CATALOGO", 150)
OBJETIVO_CATALOGO_ACTIVO = _entero("TINKER_OBJETIVO_CATALOGO", 300)

# ── explorar contra explotar ──────────────────────────────────────────────
#
# La proporcion la impone Python, no el modelo. Pedirsela al prompt y confiar
# es como pedirle que devuelva exactamente cinco: a veces sí.
EXPLOTAR = _decimal("TINKER_EXPLOTAR", 0.8)
EXPLORAR = round(1.0 - EXPLOTAR, 2)

# ── fatiga ────────────────────────────────────────────────────────────────
#
# Una canción con conversion excelente puede haber aparecido demasiado en la
# ultima media hora. Su score baja UN RATO y se recupera solo.
FATIGA_VENTANA_MIN = _entero("TINKER_FATIGA_VENTANA", 30)
FATIGA_TOLERANCIA = _entero("TINKER_FATIGA_TOLERANCIA", 3)    # exposiciones "gratis" en la ventana
FATIGA_PESO = _decimal("TINKER_FATIGA_PESO", 1.2)             # cuanto resta cada exposicion de mas
FATIGA_TOPE = _decimal("TINKER_FATIGA_TOPE", 8.0)             # el castigo no es infinito

# ── rechazo y reinicio de diversidad ──────────────────────────────────────
RECHAZOS_PARA_RESET = _entero("TINKER_RECHAZOS_RESET", 2)
# "5 rechazos fuertes seguidos" es la referencia del pedido para una sesion
# larga; en un juego de cinco rondas, esperar cinco rechazos es esperar a que
# la persona se vaya. Dos seguidos ya son una señal clara.

PESO_NO_ELEGIDA = _decimal("TINKER_PESO_NO_ELEGIDA", 0.35)
PESO_RECHAZADA = _decimal("TINKER_PESO_RECHAZADA", 3.0)

# ── el gusto del salon ────────────────────────────────────────────────────
#
# No todas las elecciones valen igual, y eso es lo que separa un contador de un
# perfil. Dos correcciones, las dos con su motivo:

# LO RECIENTE PESA MAS. A los 20 minutos una eleccion vale la mitad; a los 40,
# un cuarto. Una fiesta a las once no es la misma a las dos, y un perfil que
# suma toda la noche por igual describe un promedio que no existio nunca.
PERFIL_VIDA_MEDIA_MIN = _decimal("TINKER_PERFIL_VIDA_MEDIA", 20.0)

# LO QUE ELIGIERON VARIOS PESA MAS, pero no linealmente (es 1 + log2(n)): la
# segunda persona que elige Metallica CONFIRMA; la sexta no aporta seis veces
# mas, y si contara lineal, cinco amigos del mismo gusto se llevarian la noche.

# Con cuanta gente el perfil del salon vale por si solo. Por debajo, el termino
# colectivo se escala y lo que cede se lo lleva el gusto individual: con tres
# personas, "el gusto del salon" es el gusto de tres.
PERSONAS_PARA_CONFIANZA = _entero("TINKER_PERSONAS_CONFIANZA", 4)

# Cuando un genero pasa de esta proporcion del perfil, su tope por tanda baja a
# dos. Es la regla de la camara de eco: si tres personas eligen Metallica, la
# tanda siguiente NO son cinco de metal.
DOMINANCIA = _decimal("TINKER_DOMINANCIA", 0.40)

# La forma de la tanda, de la ronda 2 en adelante: tres siguen el gusto
# detectado, dos estan ahi para poder cambiar de rumbo. Las dos abiertas no son
# un adorno -- son la unica via que tiene alguien para torcer una noche que se
# encerro en un genero.
ADAPTADAS = max(0, min(5, _entero("TINKER_ADAPTADAS", 3)))
ABIERTAS = 5 - ADAPTADAS

# De las adaptadas, cuantas las decide el SALON en vez de la persona. Se mueve
# de a una carta entera porque son tres ranuras; la gradacion fina vive en los
# pesos, que puntuan TODAS las candidatas y no solo su ranura.
COLECTIVO_DE_ADAPTADAS = {2: 0.0, 3: 0.25, 4: 0.45, 5: 0.60}
COLECTIVO_POR_DEFECTO = _decimal("TINKER_COLECTIVO_DEFECTO", 0.60)

# La formula del puntaje colectivo. Configurables a proposito: la correcta no
# se deduce, se mide en una fiesta de verdad.
PESOS_COLECTIVO = {
    "genero":         _decimal("TINKER_CROWD_GENERO", 0.30),
    "artista":        _decimal("TINKER_CROWD_ARTISTA", 0.20),
    "decada":         _decimal("TINKER_CROWD_DECADA", 0.15),
    "energia":        _decimal("TINKER_CROWD_ENERGIA", 0.15),
    "popularidad":    _decimal("TINKER_CROWD_POPULARIDAD", 0.10),
    "descubrimiento": _decimal("TINKER_CROWD_DESCUBRIR", 0.10),
}

# El empuje de los grupos que pidio el dueño del evento. NO es una cuota: es un
# bono que se desvanece. Mientras no sabemos nada, su lista es la mejor apuesta
# que hay; para la ronda 5, lo que el salon mostro vale mas que una lista
# escrita antes de que llegara nadie.
BONO_PRIORITARIA = {1: 3.0, 2: 2.0, 3: 1.2, 4: 0.6, 5: 0.3}
BONO_PRIORITARIA_DEFECTO = _decimal("TINKER_BONO_PRIORITARIA", 0.3)


# ── umbrales de metricas ──────────────────────────────────────────────────
#
# Una canción mostrada tres veces y elegida una no tiene 33% de conversion:
# no tiene conversion todavia. Sin este piso, las canciones nuevas dominan
# cualquier ranking por ratio.
MINIMO_EXPOSICIONES = _entero("TINKER_MINIMO_EXPOSICIONES", 10)

BAJA_ROTACION_EXPOSICIONES = _entero("TINKER_BAJA_EXPOSICIONES", 10)
BAJA_ROTACION_RECHAZO = _decimal("TINKER_BAJA_RECHAZO", 0.70)
CUARENTENA_EXPOSICIONES = _entero("TINKER_CUARENTENA_EXPOSICIONES", 20)
CUARENTENA_RECHAZO = _decimal("TINKER_CUARENTENA_RECHAZO", 0.80)

# ── pesos del score global ────────────────────────────────────────────────
#
# Configurables a proposito: la formula correcta no se deduce, se mide. Estos
# son un punto de partida honesto, no una verdad.
PESOS_SCORE = {
    "popularidad_py": _decimal("TINKER_PESO_POP_PY", 2.0),
    "popularidad_global": _decimal("TINKER_PESO_POP_GLOBAL", 1.0),
    "conversion": _decimal("TINKER_PESO_CONVERSION", 3.0),
    "tendencia": _decimal("TINKER_PESO_TENDENCIA", 1.5),
    "novedad": _decimal("TINKER_PESO_NOVEDAD", 1.0),
    "rechazo": _decimal("TINKER_PESO_RECHAZO", 2.5),
    "paraguaya": _decimal("TINKER_PESO_PARAGUAYA", 0.8),
}

# ── cuotas blandas del catalogo ───────────────────────────────────────────
#
# Las tendencias de hoy convertirian el catalogo en 80% reggaeton en dos
# semanas. Estas cuotas NO gobiernan una tanda --eso es cosa de reglas.py--
# sino que canciones se incorporan cuando el catalogo se rellena.
#
# Son BLANDAS: si una categoria no llega a su cuota porque las fuentes no
# devolvieron nada, el refill no se queda corto; reparte lo que falta.
CUOTAS = {
    "paraguaya": _decimal("TINKER_CUOTA_PARAGUAYA", 0.15),
    "tendencia_py": _decimal("TINKER_CUOTA_TENDENCIA_PY", 0.20),
    "rock": _decimal("TINKER_CUOTA_ROCK", 0.12),
    "pop": _decimal("TINKER_CUOTA_POP", 0.12),
    "cumbia": _decimal("TINKER_CUOTA_CUMBIA", 0.10),
    "urbano": _decimal("TINKER_CUOTA_URBANO", 0.12),
    "tropical": _decimal("TINKER_CUOTA_TROPICAL", 0.06),
    "brasilera": _decimal("TINKER_CUOTA_BRASILERA", 0.05),
    "clasicos": _decimal("TINKER_CUOTA_CLASICOS", 0.08),
}

# El piso de musica paraguaya en el catalogo activo. No obliga a que aparezca
# en cada tanda; impide que desaparezca del catalogo, que es otra cosa.
PISO_PARAGUAY = _entero("TINKER_PISO_PARAGUAY", 20)

# ── Spotify ───────────────────────────────────────────────────────────────
#
# EVERY_SELECTION | FINAL_SELECTION | VOTE_THRESHOLD
SPOTIFY_ADD_MODE = os.environ.get("TINKER_SPOTIFY_MODO", "VOTE_THRESHOLD").strip().upper()
SPOTIFY_VOTOS_MINIMOS = _entero("TINKER_SPOTIFY_VOTOS", 3)
# Los reintentos, en minutos. Se acaban: una canción que fallo cinco veces no
# se arregla insistiendo, y la cola no puede crecer para siempre.
SPOTIFY_ESPERAS_MIN = (1, 5, 15, 60)

# ── descubrimiento ────────────────────────────────────────────────────────
DESCUBRIMIENTO_ACTIVO = os.environ.get("TINKER_DESCUBRIR", "si").strip().lower() in (
    "si", "sí", "1", "true", "yes",
)

# Los jobs de fondo. Se apagan en las pruebas: el TestClient corre el lifespan
# completo, asi que sin esto cada corrida sale a internet a descubrir musica y
# a hablar con Spotify -- lento, sucio y pagado por alguien.
JOBS_ACTIVOS = os.environ.get("TINKER_JOBS", "si").strip().lower() in (
    "si", "sí", "1", "true", "yes",
)
PAISES_TENDENCIA = tuple(
    p.strip() for p in os.environ.get("TINKER_PAISES", "py,ar,br,es,us").split(",") if p.strip()
)
# Cada cuantas horas corre cada job. El planificador los espacia solo.
HORAS_PARAGUAY = _entero("TINKER_HORAS_PY", 6)
HORAS_GLOBAL = _entero("TINKER_HORAS_GLOBAL", 12)
HORAS_GENEROS = _entero("TINKER_HORAS_GENEROS", 24)
HORAS_SCORING = _entero("TINKER_HORAS_SCORING", 1)


@lru_cache
def franja(hora: int) -> str:
    """La franja horaria de un evento. Se guarda con cada exposicion para poder
    aprender despues que funciona a las nueve y que a las tres de la mañana."""
    if 18 <= hora < 21:
        return "APERTURA"
    if 21 <= hora < 23:
        return "CENA"
    if hora >= 23 or hora < 3:
        return "FIESTA"
    if 3 <= hora < 7:
        return "MADRUGADA"
    return "APERTURA"


TIPOS_EVENTO = (
    "BAR", "RESTAURANTE", "BODA", "CUMPLEANOS", "ASADO",
    "FIESTA", "EMPRESARIAL", "DISCOTECA", "REUNION", "OTRO",
)

FRANJAS = ("APERTURA", "CENA", "FIESTA", "MADRUGADA")
