"""La linea corta que la persona lee arriba de las cinco tarjetas.

Es lo unico que el agente dice en voz alta. Todo lo demas --el perfil del
salon, los tres terminos del puntaje, el reparto de ranuras-- pasa invisible, y
un agente que trabaja bien sin que se note es indistinguible de uno que no
trabaja. Una oracion por tanda alcanza para que se note.

DOS DECISIONES:

  · ES DETERMINISTA. Sale del perfil medido, no del modelo. Asi existe entera
    con cero claves configuradas, que es el estado en el que el juego tiene que
    seguir funcionando. El modelo puede proponer la suya, y se usa SOLO si pasa
    un chequeo de forma -- porque una frase de mas de una linea, o que nombre
    una canción de la tanda, arruina la pantalla y le adelanta el final.

  · NO EXPLICA EL RAZONAMIENTO. "Estamos entendiendo el gusto del grupo" es
    una invitacion; "elegí estas cinco porque tu perfil de afinidad de genero
    es 0.72" es un volcado de log con tipografia linda. Lo interno se guarda en
    `tandas.porque` y no se muestra.
"""
from __future__ import annotations

import random

from app.recomendador.publico import PerfilPublico

TOPE = 80

# La ronda 1 no tiene nada que contar todavia: se invita a jugar.
PRIMERA = (
    "Cinco mundos distintos. Tocá los que te tiren.",
    "Empecemos por acá: elegí lo que te guste.",
    "Marcá todas las que quieras escuchar hoy.",
)

# Cuando la persona es de las primeras del salon. Es verdad y es lindo saberlo.
PIONERA = (
    "Todavía sos de los primeros: acá mandás vos.",
    "El salón está arrancando. Lo que elijas marca el rumbo.",
)

SEGUNDA = (
    "Ya te leí una. Estas cinco salen de ahí.",
    "Con lo que elegiste armé estas cinco.",
)

# De la ronda 4 en adelante el salon pesa de verdad, y se dice.
CON_SALON = (
    "Esta tanda mezcla lo tuyo con lo que está eligiendo el resto.",
    "Mitad tu gusto, mitad el del salón.",
)

GENERICOS = (
    "Estamos entendiendo el gusto del grupo.",
    "Cada elección tuya cambia lo que viene.",
)

# Como se nombra cada genero en una frase. Sin esto quedaria "cumbia está
# creciendo" al lado de "80-90 está creciendo", que se lee raro.
COMO_SE_DICE = {
    "80-90": "lo de los 80 y 90",
    "electronica": "la electrónica",
    "romantica": "lo romántico",
    "paraguaya": "la música paraguaya",
    "brasilera": "lo brasilero",
    "ranchera": "la ranchera",
    "urbano": "lo urbano",
    "otros": "la mezcla",
}


def _nombrar(genero: str) -> str:
    return COMO_SE_DICE.get(genero, genero.capitalize())


def _empezando(texto: str) -> str:
    """Con mayuscula inicial, sin tocar el resto.

    `capitalize()` no sirve: "La electrónica" se convertiria en "La
    electrónica" pero "Los 80 y 90" en "Los 80 y 90" y "RHCP" en "Rhcp".
    """
    return texto[:1].upper() + texto[1:] if texto else texto


def _elegir(opciones, semilla: int) -> str:
    """Una de las variantes, estable por dispositivo y ronda.

    Estable importa: recargar no puede cambiar la frase, o la persona creeria
    que paso algo. Variada importa por lo otro -- cinco tandas con la misma
    oracion se leen como una pantalla rota.
    """
    return random.Random(semilla).choice(opciones)


def para(ronda: int, perfil: PerfilPublico, semilla: int = 0) -> str:
    """La linea de esta tanda. Nunca vacia, en ninguna ronda y sin ninguna clave."""
    if ronda <= 1:
        if perfil.personas <= 1:
            return _elegir(PIONERA, semilla)
        return _elegir(PRIMERA, semilla)

    if perfil.personas <= 1:
        return _elegir(PIONERA, semilla)

    # Lo que de verdad esta pasando en el salon, cuando hay con que decirlo.
    if perfil.elecciones >= 4 and perfil.generos:
        dominante = perfil.dominantes[0]
        if perfil.generos.get(dominante, 0) >= 0.6:
            return _empezando(
                f"{_nombrar(dominante)} está creciendo entre las elecciones."
            )[:TOPE]

    if perfil.energia is not None and perfil.energia >= 0.65:
        return "La energía del salón está subiendo."
    if perfil.energia is not None and perfil.energia <= 0.3 and perfil.elecciones >= 6:
        return "El salón viene tranquilo."

    if ronda == 2:
        return _elegir(SEGUNDA, semilla)
    if ronda >= 4 and perfil.confianza >= 0.5:
        return _elegir(CON_SALON, semilla)
    return _elegir(GENERICOS, semilla)


def del_modelo(crudo: str | None, tanda) -> str | None:
    """La frase del modelo, si sirve para mostrarse. None si no.

    Cuatro motivos para rechazarla, y los cuatro se vieron venir mirando lo que
    contestan los modelos cuando se les pide "una linea corta":

      · larga -- rompe el encabezado del telefono;
      · con saltos de linea -- lo mismo, en dos renglones;
      · nombrando una canción de la tanda -- le adelanta la respuesta a la
        persona y dirige el voto, que es justo lo que el barajado evita;
      · vacia.
    """
    if not crudo:
        return None
    frase = " ".join(crudo.split())
    if not frase or len(frase) > TOPE:
        return None
    bajita = frase.lower()
    for candidata in tanda or []:
        if candidata.titulo and candidata.titulo.lower() in bajita:
            return None
        if candidata.artista and candidata.artista.lower() in bajita:
            return None
    return frase
