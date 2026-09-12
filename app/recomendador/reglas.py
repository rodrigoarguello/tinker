"""El contrato de una tanda. NO toca la base y NO toca la red.

Es la pieza que hace que el modelo pueda equivocarse sin consecuencias: toma
lo que haya --cinco indices buenos, una lista vacia, cuarenta con repetidas,
numeros inventados-- y devuelve EXACTAMENTE cinco candidatas validas.

LA PRECEDENCIA, que hay que tener escrita porque un repertorio pobre no puede
satisfacer todas las reglas a la vez:

    exactamente 5  >  no repetir  >  un artista por tanda  >  generos distintos
                   >  epoca / idioma / intensidad

Cuando algo no entra, cede lo de la derecha. **"Exactamente 5" no cede nunca**:
una tanda de cuatro tarjetas es una pantalla rota en medio de una fiesta.

LA FORMA DE LA TANDA, de la ronda 2 en adelante: **tres adaptadas y dos
abiertas**. Las tres siguen el gusto detectado --de la persona y, a medida que
hay con que, del salon--. Las dos abiertas no son un adorno: son la unica via
que tiene alguien para torcer una noche que se encerro en un genero, y por eso
nunca salen de los dos generos dominantes.

EL PUNTAJE, EN TRES TERMINOS Y CON LOS CASTIGOS AFUERA. La mezcla es gusto; los
castigos son el contrato. Si los castigos entraran ponderados, un colectivo muy
dominante podria pasarle por encima a «esta persona ya rechazo cinco de este
genero», que es exactamente lo que el reinicio de diversidad existe para
impedir.
"""
from __future__ import annotations

import random

from app import config
from app.recomendador.pozo import Candidata, Pozo
from app.recomendador.publico import PerfilPublico

TAMANO_TANDA = 5

# Cuantas del mismo genero admite una tanda. En la ronda 1 se aprieta: es la
# ronda de exploracion y su trabajo es preguntar "¿cual de estos cinco mundos
# es el tuyo?". Despues se afloja, porque a esa altura ya sabemos algo.
TOPE_POR_GENERO = {1: 2}
TOPE_POR_GENERO_NORMAL = 3

# Cuantos generos distintos tiene que haber en una tanda, cuando el pozo da
# para eso. No es una regla aparte: con el tope de dos por genero de la ronda
# 1, cinco tarjetas ya obligan a tres. Esta escrito para que las pruebas lo
# puedan afirmar sin deducirlo de otra constante.
MIN_GENEROS = 3

# La proporcion de afinidad por ronda. Arranca explorando y termina
# confirmando: en la ronda 2 sabemos una sola cosa de la persona y encerrarla
# ahi es la forma mas rapida de aburrirla. Hoy gobierna el PROMPT y el reparto
# de ranuras; el reparto fino lo hace `ranuras()`.
MEZCLA_POR_RONDA = {1: 0.0, 2: 0.4, 3: 0.5, 4: 0.6, 5: 0.7}
MEZCLA_POR_DEFECTO = 0.7

# Cuanto pesa el gusto frente a los castigos. Los castigos estan en una escala
# absoluta (hasta -8 la fatiga, -6 un rechazo propio); el gusto sale 0..1 de la
# mezcla y hay que traerlo a la misma escala. Con 6.0, el gusto puede ganarle a
# una tanda repetida pero NUNCA a un rechazo fuerte -- que es el orden correcto.
PESO_GUSTO = 6.0

# Los cuatro modos de ranura. Cada uno es una MEZCLA de los tres terminos, no
# un termino solo: el salon influye tambien en las cartas individuales, y por
# eso su presencia crece de a poco y no de golpe cuando le toca una ranura.
#
#                    individual · colectivo · contraste
MEZCLAS = {
    "explorar":   (0.00, 0.35, 0.65),   # ronda 1: no hay señal propia todavia
    "individual": (0.70, 0.20, 0.10),
    "colectivo":  (0.25, 0.65, 0.10),
    "contraste":  (0.10, 0.10, 0.80),   # las dos abiertas
}


def tope_genero(ronda: int) -> int:
    """El tope base de la ronda, sin mirar al salon."""
    return TOPE_POR_GENERO.get(ronda, TOPE_POR_GENERO_NORMAL)


def tope_de(genero: str | None, ronda: int, perfil: PerfilPublico | None = None) -> int:
    """El tope de ESTE genero en esta tanda.

    Baja a dos cuando el genero ya domina el salon. Es la regla de la camara de
    eco, y vive aca --en el tope, que ya sabe ceder-- y no como una regla dura
    nueva: si tres personas eligen Metallica, la tanda siguiente trae Metallica,
    Foo Fighters, Guns N' Roses, Red Hot Chili Peppers y una inesperada
    compatible en energia; no cinco de metal.
    """
    base = tope_genero(ronda)
    if perfil is not None and perfil.domina(genero):
        return min(base, 2)
    return base


def mezcla(ronda: int, pozo: Pozo | None = None) -> float:
    """Cuanto de la tanda sigue el gusto detectado.

    Tres cosas la mueven, en este orden:

      · la ronda -- temprano se explora, tarde se confirma;
      · el evento -- `explotar_pct` la escala, asi una discoteca puede pesar
        mas a lo conocido que un bar;
      · el REINICIO DE DIVERSIDAD -- si la persona viene rechazando tandas
        enteras, la afinidad se apaga casi por completo: seguir insistiendo con
        lo que viene rechazando es la forma mas rapida de que guarde el
        telefono.
    """
    base = MEZCLA_POR_RONDA.get(ronda, MEZCLA_POR_DEFECTO)
    if pozo is None:
        return base
    base *= (pozo.explotar / 0.8) if pozo.explotar else 0.0
    if reiniciar_diversidad(pozo):
        base *= 0.25
    return max(0.0, min(1.0, base))


def reiniciar_diversidad(pozo: Pozo) -> bool:
    """Si hay que cambiar de rumbo.

    Se dispara con rechazos SEGUIDOS: alguien que descarto dos tandas enteras
    no esta pidiendo mas de lo mismo con otro nombre. Cuando esta activo, las
    adaptadas bajan de tres a una y las abiertas mandan -- si rechazo cinco
    urbanas, lo que NO hay que devolverle son otras cinco urbanas.
    """
    return pozo.rechazos_seguidos >= config.RECHAZOS_PARA_RESET


# ── el reparto de las cinco ranuras ──────────────────────────────────────

def ranuras(pozo: Pozo) -> list[str]:
    """Los cinco modos de esta tanda, en orden.

    Ronda 1: cinco exploraciones. Ronda 2 en adelante: tres adaptadas y dos
    abiertas, y dentro de las adaptadas el salon va ganando lugar a medida que
    hay con que. Se mueve de a una carta entera porque son tres ranuras; la
    gradacion fina vive en los pesos, que puntuan TODAS las candidatas.
    """
    if pozo.ronda <= 1:
        return ["explorar"] * TAMANO_TANDA

    adaptadas = config.ADAPTADAS
    if reiniciar_diversidad(pozo):
        # Viene rechazando tandas enteras: se abre casi todo.
        adaptadas = min(adaptadas, 1)

    cuota = config.COLECTIVO_DE_ADAPTADAS.get(pozo.ronda, config.COLECTIVO_POR_DEFECTO)
    # Lo que el colectivo cede por falta de confianza se lo lleva el individual:
    # con tres personas en el salon, "el gusto de todos" es el gusto de tres.
    colectivas = int(adaptadas * cuota * pozo.publico.confianza + 0.5)
    colectivas = max(0, min(adaptadas, colectivas))

    return (
        ["individual"] * (adaptadas - colectivas)
        + ["colectivo"] * colectivas
        + ["contraste"] * (TAMANO_TANDA - adaptadas)
    )


# ── comprobacion, para las pruebas y para el log ─────────────────────────

def violaciones(tanda: list[Candidata], pozo: Pozo) -> list[str]:
    """Las reglas que rompe una tanda, por nombre. Vacia = valida.

    Existe para dos cosas: que las pruebas digan QUE se rompio en vez de
    "assert False", y que una tanda rara en produccion deje un log que se
    entienda sin reproducirla.
    """
    fallas = []
    if len(tanda) != TAMANO_TANDA:
        fallas.append(f"cantidad:{len(tanda)}")
    ids = [c.cancion_id for c in tanda]
    if len(set(ids)) != len(ids):
        fallas.append("repetidas")
    disponibles = {c.cancion_id for c in pozo.disponibles}
    if any(i not in disponibles for i in ids):
        fallas.append("fuera_del_pozo")
    artistas = [c.artista_clave for c in tanda]
    if len(set(artistas)) != len(artistas):
        fallas.append("artista_repetido")
    generos = [c.genero for c in tanda if c.genero]
    for genero in set(generos):
        if generos.count(genero) > tope_de(genero, pozo.ronda, pozo.publico):
            fallas.append(f"genero_excedido:{genero}")
    return fallas


# ── los tres terminos del gusto ──────────────────────────────────────────

# Cuantas veces puede aparecer un genero entre lo ELEGIDO antes de que deje de
# sumar afinidad. Es la regla de «no te encierres»: a la tercera cumbia
# elegida, proponer una cuarta ya no es acompañar el gusto, es no ofrecer nada.
SATURACION_GENERO = 2


def _senal(pozo: Pozo) -> dict:
    """Lo que sabemos de la persona: generos, epocas, idiomas, intensidad
    promedio y --lo mas importante para la variedad-- que artistas ya se
    llevo."""
    generos: dict[str, int] = {}
    for c in pozo.elegidas:
        if c.genero:
            generos[c.genero] = generos.get(c.genero, 0) + 1
    intensidades = [c.intensidad for c in pozo.elegidas if c.intensidad]
    return {
        "generos": set(generos),
        "conteo_generos": generos,
        "epocas": {c.epoca for c in pozo.elegidas if c.epoca},
        "idiomas": {c.idioma for c in pozo.elegidas if c.idioma},
        "artistas": {c.artista_clave for c in pozo.elegidas},
        "intensidad": sum(intensidades) / len(intensidades) if intensidades else None,
        "fatiga": pozo.fatiga,
        "publico": pozo.publico,
        "rechazados": pozo.rechazados,
        "rechazados_propios": pozo.rechazados_propios,
        "ronda": pozo.ronda,
    }


def individual(candidata: Candidata, senal: dict) -> float:
    """Cuanto se parece a lo que ESTA persona eligio. 0..1.

    Cada sumando pide su etiqueta; si falta, no cuenta ni a favor ni en contra
    -- se reparte el peso entre los que si estan. Un repertorio sin `idioma` no
    tiene que puntuar peor que uno con.
    """
    puntos, peso = 0.0, 0.0
    if candidata.genero:
        peso += 0.45
        if candidata.genero in senal["generos"]:
            veces = senal["conteo_generos"].get(candidata.genero, 0)
            # Y la afinidad SATURA. En una partida real salieron cuatro
            # romanticas de cinco: acompañar el gusto es una cosa, encerrar a la
            # persona en el primer genero que toco es otra.
            puntos += 0.45 if veces < SATURACION_GENERO else 0.0
    if candidata.epoca:
        peso += 0.20
        if candidata.epoca in senal["epocas"]:
            puntos += 0.20
    if candidata.idioma:
        peso += 0.15
        if candidata.idioma in senal["idiomas"]:
            puntos += 0.15
    if candidata.intensidad and senal["intensidad"] is not None:
        peso += 0.20
        distancia = abs(candidata.intensidad - senal["intensidad"]) / 4.0
        puntos += 0.20 * max(0.0, 1.0 - distancia)
    return puntos / peso if peso else 0.0


def colectivo(candidata: Candidata, perfil: PerfilPublico) -> float:
    """Cuanto se parece a lo que viene eligiendo el SALON. 0..1.

    Es la formula del pedido, con los pesos en `config.PESOS_COLECTIVO`. Igual
    que el individual: cada termino pide su dato y, si no esta, se reparte.
    """
    if not perfil.elecciones:
        return 0.0
    pesos = config.PESOS_COLECTIVO
    puntos, total = 0.0, 0.0

    if candidata.genero:
        total += pesos["genero"]
        puntos += pesos["genero"] * perfil.generos.get(candidata.genero, 0.0)
    if candidata.artista_clave:
        total += pesos["artista"]
        puntos += pesos["artista"] * perfil.artistas.get(candidata.artista_clave, 0.0)
    if candidata.epoca:
        total += pesos["decada"]
        puntos += pesos["decada"] * perfil.decadas.get(candidata.epoca, 0.0)
    if candidata.intensidad and perfil.energia is not None:
        total += pesos["energia"]
        propia = (candidata.intensidad - 1) / 4.0
        puntos += pesos["energia"] * max(0.0, 1.0 - abs(propia - perfil.energia))
    if candidata.popularidad is not None and perfil.mainstream is not None:
        total += pesos["popularidad"]
        puntos += pesos["popularidad"] * max(
            0.0, 1.0 - abs(candidata.popularidad - perfil.mainstream)
        )
        # Y el valor de descubrimiento: lo que esta por DEBAJO del mainstream
        # del salon suma cuando el salon viene mostrando apertura.
        total += pesos["descubrimiento"]
        if candidata.popularidad < perfil.mainstream:
            puntos += pesos["descubrimiento"] * perfil.descubrimiento

    return puntos / total if total else 0.0


def contraste(candidata: Candidata, senal: dict, evitar_dominantes: bool = True) -> float:
    """Cuanto ABRE esta candidata. 0..1.

    Es el termino de las dos cartas abiertas, y lo que impide la camara de eco:
    premia lo que la persona todavia no probo Y lo que el salon todavia no
    convirtio en dominante.

    `evitar_dominantes` se apaga en la RONDA 1, y eso lo enseño una prueba: sin
    apagarlo, la tanda de quien recien llega salia SIN el genero que el salon
    viene eligiendo, que es lo contrario de lo que hace falta. En la ronda 1 no
    hay de que salir --la persona no eligio nada todavia-- y el gusto del salon
    es la unica pista que existe. La camara de eco es un riesgo de las rondas
    2 en adelante, y ahi las dos cartas abiertas la cortan sin ayuda.
    """
    perfil: PerfilPublico = senal["publico"]
    puntos = 0.0
    if candidata.genero:
        if candidata.genero not in senal["generos"]:
            puntos += 0.40
        if not evitar_dominantes or candidata.genero not in perfil.dominantes:
            puntos += 0.30
    else:
        # Sin genero no se puede afirmar que abra, pero tampoco que encierre.
        puntos += 0.35
    if candidata.artista_clave not in senal["artistas"]:
        puntos += 0.15
    if perfil.frecuencia_tema.get(candidata.cancion_id, 0) == 0:
        puntos += 0.15
    return min(1.0, puntos)


def castigos(candidata: Candidata, senal: dict, en_tanda: list[Candidata]) -> float:
    """Lo que resta, en escala absoluta. Nunca se pondera.

    Son el contrato, no el gusto: una tanda con la misma canción dos veces o
    con un genero que la persona rechazo cinco veces esta mal aunque el salon
    entero la este pidiendo.
    """
    resta = 0.0

    # FATIGA. Una canción puede tener conversion excelente y haber salido seis
    # veces en la ultima media hora. El castigo se va solo: la ventana se corre
    # con el reloj y no hay nada que resetear.
    resta += senal["fatiga"].get(candidata.cancion_id, 0.0)

    # Los generos que el evento viene RECHAZANDO en bloque pesan en contra para
    # todos: si el salon dice que no a un genero, no hace falta que cada persona
    # lo descubra por su cuenta.
    if candidata.genero and senal["rechazados"].get(candidata.genero):
        resta += 1.5

    # Los que ESTA persona descarto pesan MUCHO mas. Es el nucleo del reinicio
    # de diversidad: quien rechazo cinco urbanas no quiere otras cinco.
    if candidata.genero:
        propios = senal["rechazados_propios"].get(candidata.genero, 0)
        if propios:
            resta += min(6.0, 2.0 * propios)

    # Un artista que la persona YA se llevo pesa en contra, incluso en modo
    # afinidad. Se descubrio mirando una partida entera: eligiendo siempre la
    # primera tarjeta, dos de las cinco finales eran de Fito Paez. La regla de
    # «un artista por tanda» no lo cubre --son tandas distintas-- y cinco
    # canciones con un artista repetido es una seleccion pobre.
    if candidata.artista_clave in senal["artistas"]:
        resta += 4.0

    # Y lo que ya esta en la tanda que se arma: cinco cumbias de los 2000 no es
    # ni afin ni diversa, es monotona.
    if candidata.genero and candidata.genero in {c.genero for c in en_tanda if c.genero}:
        resta += 3.0
    if candidata.epoca and candidata.epoca in {c.epoca for c in en_tanda if c.epoca}:
        resta += 1.0
    if candidata.idioma and candidata.idioma in {c.idioma for c in en_tanda if c.idioma}:
        resta += 0.5
    return resta


def bono_prioritaria(candidata: Candidata, ronda: int) -> float:
    """El empuje de los grupos que pidio el dueño del evento.

    NO es una cuota: es un bono que se desvanece. Mientras no sabemos nada, su
    lista es la mejor apuesta que hay; para la ronda 5, lo que el salon mostro
    vale mas que una lista escrita antes de que llegara nadie. Asi las bandas
    dejan de aparecer porque estan obligadas y empiezan a aparecer porque ganan.
    """
    if not candidata.prioritaria:
        return 0.0
    return config.BONO_PRIORITARIA.get(ronda, config.BONO_PRIORITARIA_DEFECTO)


def _puntaje(candidata: Candidata, senal: dict, en_tanda: list[Candidata], modo: str) -> float:
    """Cuanto suma esta candidata a la tanda que se esta armando.

    La ranura decide el ENFASIS --cuanto pesa cada termino-- y no apaga a los
    otros dos: por eso el salon influye tambien en las cartas individuales, y
    por eso su presencia crece de a poco en vez de aparecer de golpe.
    """
    perfil: PerfilPublico = senal["publico"]
    w_ind, w_col, w_con = MEZCLAS.get(modo, MEZCLAS["individual"])

    # Lo que el colectivo cede por falta de confianza se lo lleva el individual.
    # Con una sola persona en el salon, el termino colectivo casi no existe --y
    # tiene que ser asi: "el gusto de todos" no puede ser el gusto de uno.
    cedido = w_col * (1.0 - perfil.confianza)
    w_col -= cedido
    w_ind += cedido

    gusto = (
        w_ind * individual(candidata, senal)
        + w_col * colectivo(candidata, perfil)
        + w_con * contraste(candidata, senal, evitar_dominantes=modo != "explorar")
    )
    return (
        gusto * PESO_GUSTO
        + bono_prioritaria(candidata, senal["ronda"])
        - castigos(candidata, senal, en_tanda)
    )


def _elegir_por_puntaje(
    candidatas: list[Candidata], pozo: Pozo, cuantas: int,
    modo: str, ya: list[Candidata],
) -> list[Candidata]:
    """Greedy: en cada paso, la que mas suma a lo que ya hay.

    Greedy y no exhaustivo a proposito. Con cinco lugares y un pozo de
    trescientas, la combinatoria optima cuesta y la diferencia no se ve en una
    pantalla de telefono; lo que si se ve es medio segundo de espera.
    """
    azar = random.Random(pozo.semilla)
    senal = _senal(pozo)
    perfil = pozo.publico
    tanda = list(ya)
    restantes = [c for c in candidatas if c.cancion_id not in {x.cancion_id for x in tanda}]

    while len(tanda) < len(ya) + cuantas and restantes:
        artistas = {c.artista_clave for c in tanda}
        conteo_generos: dict[str, int] = {}
        for c in tanda:
            if c.genero:
                conteo_generos[c.genero] = conteo_generos.get(c.genero, 0) + 1

        posibles = [
            c for c in restantes
            if c.artista_clave not in artistas
            and (
                not c.genero
                or conteo_generos.get(c.genero, 0) < tope_de(c.genero, pozo.ronda, perfil)
            )
        ]
        # UNA CARTA ABIERTA NO PUEDE SER DEL GENERO DOMINANTE. Es la regla que
        # impide la camara de eco, y por eso vive como filtro y no solo como
        # puntaje: aunque el gusto este clarisimo, dos de cinco ofrecen otra
        # cosa. Cede si el repertorio no da para tanto.
        if modo == "contraste":
            evitar = set(perfil.dominantes) | senal["generos"]
            abiertas = [c for c in posibles if c.genero not in evitar]
            if abiertas:
                posibles = abiertas
        # Si las reglas blandas dejaron el pozo sin nada, CEDEN. Primero el
        # tope de genero, y sólo si aun asi no hay nada, el artista unico.
        # "Exactamente cinco" esta arriba de las dos.
        if not posibles:
            posibles = [c for c in restantes if c.artista_clave not in artistas]
        if not posibles:
            posibles = restantes

        # El azar como DESEMPATE, no como criterio: con pocas etiquetas muchas
        # candidatas puntuan igual, y sin esto la tanda seria el orden de la
        # planilla para todo el mundo.
        elegida = max(posibles, key=lambda c: (_puntaje(c, senal, tanda, modo), azar.random()))
        tanda.append(elegida)
        restantes.remove(elegida)

    return tanda[len(ya):]


# ── las dos formas de armar una tanda sin modelo ─────────────────────────

def diversa(pozo: Pozo, ya: list[Candidata] | None = None) -> list[Candidata]:
    """Maxima diversidad, con el salon inclinando. Es la ronda 1.

    No es diversidad ciega: el gusto del salon entra con peso propio, asi el
    primero que llega a las tres de la mañana no empieza de cero. Lo que no
    entra es la afinidad individual, porque todavia no hay ninguna.
    """
    ya = ya or []
    faltan = TAMANO_TANDA - len(ya)
    return _elegir_por_puntaje(pozo.disponibles, pozo, faltan, "explorar", ya)


def afin(
    pozo: Pozo, proporcion: float | None = None, ya: list[Candidata] | None = None,
) -> list[Candidata]:
    """El respaldo de las rondas 2 a 5: tres adaptadas y dos abiertas.

    `ya` son las que la tanda ya tiene --las que acepto `imponer` del modelo--
    y NO se devuelven: se respetan para no repetir artista ni canción, y
    consumen ranuras desde el principio.

    Si la persona no eligio nada con etiquetas todavia, degrada solo a
    `diversa`: sin señal no hay afinidad que calcular.
    """
    ya = ya or []
    if not pozo.elegidas:
        return diversa(pozo, ya=ya)

    # Las ranuras que quedan, despues de las que el modelo ya lleno.
    pendientes = ranuras(pozo)[len(ya):]
    salida: list[Candidata] = []
    for modo in pendientes:
        nueva = _elegir_por_puntaje(pozo.disponibles, pozo, 1, modo, ya + salida)
        if not nueva:
            break
        salida += nueva
    return salida


def asegurar_prioritarias(tanda: list[Candidata], pozo: Pozo) -> list[Candidata]:
    """Mete en la tanda las que falten del pool prioritario.

    YA NO SE USA POR DEFECTO: `prioritarias_por_tanda` vale 0 y los grupos del
    dueño del evento entran por `bono_prioritaria`, ganando en vez de estando
    obligados. La cuota dura queda disponible --un evento puede pedirla-- y por
    eso la funcion sigue aca, entera y probada.

    Cuando se usa: se reemplazan las NO prioritarias desde el final --lo que el
    modelo puso primero es lo que mas le importaba-- y nunca se rompe «un
    artista por tanda» para cumplir la cuota.
    """
    objetivo = min(pozo.prioritarias_objetivo, TAMANO_TANDA)
    if objetivo <= 0:
        return tanda
    if sum(1 for c in tanda if c.prioritaria) >= objetivo:
        return tanda

    en_tanda = {c.cancion_id for c in tanda}
    candidatas = [
        c for c in pozo.disponibles if c.prioritaria and c.cancion_id not in en_tanda
    ]
    if not candidatas:
        return tanda
    random.Random(pozo.semilla + 13).shuffle(candidatas)

    salida = list(tanda)
    # De atras hacia adelante, solo las que no son prioritarias.
    for posicion in range(len(salida) - 1, -1, -1):
        if sum(1 for c in salida if c.prioritaria) >= objetivo or not candidatas:
            break
        if salida[posicion].prioritaria:
            continue
        # Los artistas de la tanda SIN contar la que se esta por ir: si no se
        # la descuenta, su propio artista bloquea su reemplazo.
        artistas = {c.artista_clave for i, c in enumerate(salida) if i != posicion}
        eleccion = next((c for c in candidatas if c.artista_clave not in artistas), None)
        if eleccion is None:
            continue
        candidatas.remove(eleccion)
        salida[posicion] = eleccion
    return salida


# ── LA funcion del modulo ────────────────────────────────────────────────

def imponer(propuesta: list[Candidata], pozo: Pozo) -> list[Candidata]:
    """Lo que sea que haya -> exactamente cinco candidatas validas.

    Es el corazon de "el modelo aporta juicio, Python impone el contrato". Todo
    lo que el modelo puede hacer mal se filtra aca, en este orden:
    """
    disponibles = {c.cancion_id: c for c in pozo.disponibles}
    tanda: list[Candidata] = []
    artistas: set[str] = set()
    generos: dict[str, int] = {}

    for candidata in propuesta:
        if len(tanda) >= TAMANO_TANDA:
            break
        # 1. lo que no esta en el pozo: indices inventados, canciones de otro
        #    evento, cosas que esta persona ya vio.
        if candidata.cancion_id not in disponibles:
            continue
        # 2. repetidas dentro de la propia propuesta.
        if any(c.cancion_id == candidata.cancion_id for c in tanda):
            continue
        # 3. dos del mismo artista.
        if candidata.artista_clave in artistas:
            continue
        # 4. el tope de genero de esta ronda, que baja a dos si el genero ya
        #    domina el salon. El modelo puede querer cinco de metal; el salon
        #    que eligio metal tres veces es justamente el que no los necesita.
        if candidata.genero and generos.get(candidata.genero, 0) >= tope_de(
            candidata.genero, pozo.ronda, pozo.publico
        ):
            continue
        tanda.append(candidata)
        artistas.add(candidata.artista_clave)
        if candidata.genero:
            generos[candidata.genero] = generos.get(candidata.genero, 0) + 1

    # 5. lo que falte, con el puntaje determinista. Cubre el caso "el modelo no
    #    contesto" sin una sola rama distinta: una propuesta vacia entra aca
    #    con la tanda en cero y sale con cinco.
    if len(tanda) < TAMANO_TANDA:
        # `ya=tanda` no es opcional: sin eso, el completado puede devolver una
        # canción que el modelo ya habia acertado, o un segundo tema del mismo
        # artista, y la regla que acabamos de imponer se rompe al completar.
        completar = afin(pozo, ya=tanda) if pozo.elegidas else diversa(pozo, ya=tanda)
        tanda += completar[: TAMANO_TANDA - len(tanda)]

    tanda = tanda[:TAMANO_TANDA]

    # 6. la cuota del pool prioritario, si el evento la pidio. Por defecto no la
    #    pide: los grupos del dueño entran ganando, no obligados.
    tanda = asegurar_prioritarias(tanda, pozo)

    # 7. barajar las posiciones. Sin esto, la primera del modelo queda siempre
    #    arriba y la ultima siempre abajo: el orden dejaria de ser neutral y
    #    los votos se irian a la de arriba.
    random.Random(pozo.semilla + 7).shuffle(tanda)
    return tanda
