"""Limpiar lo que llega de afuera antes de que toque la base.

Las fuentes devuelven la misma canción escrita de ocho maneras:

    Everlong
    Everlong - Remastered 2011
    Everlong (Live at Wembley)
    Everlong (feat. Nobody) [Radio Edit]

Sin esto, el repertorio termina con cuatro «Everlong» distintas y la regla de
«no repetir» las deja pasar a las cuatro: para la base son canciones
diferentes.
"""
from __future__ import annotations

import re

from app.catalogo import clave_artista, plano

# El orden importa: `remaster` antes que `remix` porque "Remastered Remix"
# existe y lo primero que se detecta gana.
VERSIONES = (
    ("LIVE", re.compile(r"\b(live|en vivo|ao vivo|directo|unplugged session)\b", re.I)),
    ("ACOUSTIC", re.compile(r"\b(acoustic|ac[uú]stic[ao]|unplugged)\b", re.I)),
    ("REMASTER", re.compile(r"\b(remaster(ed)?|remasteriz\w+)\b", re.I)),
    ("REMIX", re.compile(r"\b(remix|rmx|edit|version|versi[oó]n|mix)\b", re.I)),
    ("COVER", re.compile(r"\b(cover|tribute|karaoke|homenaje)\b", re.I)),
)

# Lo que se saca del titulo sin cambiar de que canción se trata.
RUIDO = re.compile(
    r"""\s*
    (?:
        [\(\[\-–—]\s*
        (?:feat\.?|ft\.?|con|with|prod\.?\s*by|remaster(?:ed)?[^\)\]]*|
           \d{4}\s*remaster[^\)\]]*|radio\s*edit|single\s*version|
           album\s*version|bonus\s*track|explicit|deluxe)
        [^\)\]]*[\)\]]?
    )
    """,
    re.I | re.X,
)

# Un titulo que despues de limpiar queda vacio no se limpia: se deja como vino.
_PARENTESIS = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]\s*$")


def version_de(titulo: str) -> str:
    """ORIGINAL, REMIX, LIVE, ACOUSTIC, COVER, REMASTER u OTHER."""
    for etiqueta, patron in VERSIONES:
        if patron.search(titulo):
            return etiqueta
    return "ORIGINAL"


def limpiar_titulo(titulo: str) -> str:
    """Saca el ruido de edicion sin tocar el nombre de la canción."""
    limpio = RUIDO.sub("", titulo or "").strip(" -–—")
    # Un parentesis final que quedo colgando --"(Remastered"-- es basura.
    if limpio.count("(") != limpio.count(")") or limpio.count("[") != limpio.count("]"):
        limpio = _PARENTESIS.sub("", limpio).strip(" -–—([")
    return limpio.strip() or (titulo or "").strip()


def limpiar_artista(artista: str) -> str:
    """El artista principal, legible. `clave_artista` da la version para
    comparar; esta da la que se muestra."""
    texto = re.split(r"\s*(?:,|&|\bfeat\.?\b|\bft\.?\b|\bx\b)\s+", (artista or "").strip(), maxsplit=1)
    return (texto[0] if texto else artista or "").strip()[:300] or "—"


def huella(titulo: str, artista: str) -> str:
    """La clave con la que se detectan duplicados ANTES de insertar.

    Titulo limpio y normalizado + artista principal normalizado. Es lo que hace
    que "Everlong - Remastered 2011" de Foo Fighters y "Everlong" de Foo
    Fighters sean la misma fila.
    """
    return f"{plano(limpiar_titulo(titulo))}|{clave_artista(artista)}"


def normalizar(cruda) -> tuple[str, str, str, str]:
    """(titulo, artista, version, huella) listos para insertar."""
    titulo = limpiar_titulo(cruda.titulo)
    artista = limpiar_artista(cruda.artista)
    return titulo, artista, version_de(cruda.titulo), huella(cruda.titulo, cruda.artista)
