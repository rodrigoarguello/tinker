"""Las etiquetas del repertorio, puestas en frio y ANTES de la fiesta.

El motor razona sobre cuatro etiquetas --genero, epoca, idioma e intensidad--
y en produccion tres de las cuatro estaban SIEMPRE vacias: ninguna linea del
repositorio las escribia. `genero` salia de la semilla y de los charts;
`idioma` e `intensidad` no salian de ningun lado, asi que el bloque de
afinidad por idioma y por intensidad de `reglas.py` nunca se ejecuto.

Sin etiquetas no hay perfil de publico que valga: se puede contar que genero
eligio la gente y nada mas. Con ellas aparecen decada, idioma y energia, que
son cuatro de las seis dimensiones con las que el agente lee el salon.

POR QUE EN FRIO Y NO AL VUELO. Etiquetar cuesta una llamada al modelo por cada
cuarenta canciones. Hacerlo mientras alguien espera una tanda seria pagar esa
espera con la paciencia de una persona parada en una fiesta; hacerlo antes,
una vez, mejora TODAS las tandas de la noche y no lo espera nadie.

Y LA MISMA REGLA DE SIEMPRE: el modelo aporta juicio, Python impone el
contrato. El vocabulario es cerrado y esta en `modelos.py`; lo que el modelo
conteste fuera de esas listas se descarta y la etiqueta queda NULL. Una
etiqueta inventada es peor que una etiqueta ausente -- la ausente degrada el
puntaje, la inventada lo ensucia.
"""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.bd import Sesion
from app.modelos import EPOCAS, GENEROS, IDIOMAS, Cancion, Repertorio
from app.recomendador import modelo

registro = logging.getLogger("tinker.etiquetador")

# Veinte por llamada. Cuarenta parecia mejor --menos llamadas, menos
# cabeceras-- y en la practica son cuarenta objetos de cuatro campos de salida:
# los tres proveedores daban timeout y no se etiquetaba nada. Veinte contesta
# comodo y, si un lote se pierde, se pierden veinte canciones y no cuarenta.
LOTE = 20

# Generoso a proposito: aca no hay nadie esperando. Es la unica llamada del
# sistema que puede darse ese lujo, y hay que DECIRSELO --el tope de cuatro
# segundos por proveedor es el de una tanda, no el de esto.
PRESUPUESTO = 90.0
TOPE_POR_PROVEEDOR = 40.0

# Un tope para que un repertorio enorme no se convierta en una factura.
TOPE_LOTES = 40


SISTEMA = f"""\
Etiquetás canciones para el motor musical de una fiesta.

Para cada canción de la lista devolvés cuatro etiquetas, y SOLO con estos
valores exactos:

- genero: {" | ".join(GENEROS)}
- epoca: {" | ".join(EPOCAS)}   (la década en que salió: "70" es 1970-1979,
  "00" es 2000-2009, "20" es 2020 en adelante)
- idioma: {" | ".join(IDIOMAS)}   (es=español, pt=portugués, en=inglés,
  gn=guaraní, otro=cualquier otro)
- intensidad: 1 a 5
    1 = lenta, para escuchar
    2 = tranquila
    3 = movida
    4 = para bailar
    5 = reventada

Reglas:
- Contestá por el número de índice que te damos, uno por canción.
- Si no conocés la canción, deducí por el artista y por el título. Un error
  razonable sirve más que un `otros`.
- Usá `otros` sólo cuando de verdad no entre en ninguno.
- No inventes valores fuera de las listas: se descartan."""

ESQUEMA = {
    "type": "object",
    "properties": {
        # Sin `minItems`/`maxItems`: la salida estructurada no los admite y la
        # API contesta 400. La cantidad la pide el prompt y no importa si falla:
        # lo que falte queda NULL, que es donde ya estaba.
        "etiquetas": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "i": {"type": "integer"},
                    "genero": {"type": "string"},
                    "epoca": {"type": "string"},
                    "idioma": {"type": "string"},
                    "intensidad": {"type": "integer"},
                },
                "required": ["i", "genero", "epoca", "idioma", "intensidad"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["etiquetas"],
    "additionalProperties": False,
}


def pendientes(sesion: Session, evento_id: int) -> list[tuple[int, str, str, int | None]]:
    """Las filas del repertorio a las que les falta alguna etiqueta.

    Devuelve `(repertorio_id, titulo, artista, anio)`. El año va porque cuando
    lo tenemos --Spotify lo manda en cada busqueda-- la epoca no hace falta
    adivinarla, y decirselo al modelo evita que la invente.
    """
    filas = sesion.execute(
        select(Repertorio.id, Cancion.titulo, Cancion.artista, Cancion.anio)
        .join(Cancion, Cancion.id == Repertorio.cancion_id)
        .where(
            Repertorio.evento_id == evento_id,
            or_(
                Repertorio.genero.is_(None),
                Repertorio.epoca.is_(None),
                Repertorio.idioma.is_(None),
                Repertorio.intensidad.is_(None),
            ),
        )
        .order_by(Repertorio.orden)
    ).all()
    return [tuple(f) for f in filas]


def _pedido(lote: list[tuple[int, str, str, int | None]]) -> str:
    lineas = []
    for indice, (_, titulo, artista, anio) in enumerate(lote):
        año = f"  ({anio})" if anio else ""
        lineas.append(f"{indice} · {titulo} — {artista}{año}")
    return "Etiquetá estas canciones:\n" + "\n".join(lineas)


def limpiar(cruda: dict) -> dict:
    """Lo que el modelo contesto -> lo que se puede guardar.

    Cada etiqueta se valida contra su vocabulario por separado: que el genero
    venga mal no tiene por que tirar el idioma de la misma canción.
    """
    salida: dict = {}
    genero = cruda.get("genero")
    if isinstance(genero, str) and genero.strip().lower() in GENEROS:
        salida["genero"] = genero.strip().lower()
    epoca = cruda.get("epoca")
    if isinstance(epoca, str) and epoca.strip() in EPOCAS:
        salida["epoca"] = epoca.strip()
    idioma = cruda.get("idioma")
    if isinstance(idioma, str) and idioma.strip().lower() in IDIOMAS:
        salida["idioma"] = idioma.strip().lower()
    intensidad = cruda.get("intensidad")
    if isinstance(intensidad, int) and 1 <= intensidad <= 5:
        salida["intensidad"] = intensidad
    return salida


def _aplicar(evento_id: int, por_fila: dict[int, dict]) -> int:
    """Escribe las etiquetas. Con su PROPIA sesion: corre en un hilo aparte.

    SOLO rellena lo que esta en NULL. Una etiqueta puesta a mano por el dueño
    del evento --o heredada de otro evento donde ya se corrigio-- vale mas que
    lo que adivine el modelo, y no se pisa nunca.
    """
    tocadas = 0
    with Sesion() as sesion:
        filas = sesion.execute(
            select(Repertorio).where(
                Repertorio.evento_id == evento_id, Repertorio.id.in_(list(por_fila))
            )
        ).scalars().all()
        for fila in filas:
            etiquetas = por_fila.get(fila.id) or {}
            cambio = False
            for campo, valor in etiquetas.items():
                if getattr(fila, campo) is None:
                    setattr(fila, campo, valor)
                    cambio = True
            tocadas += 1 if cambio else 0
        sesion.commit()
    return tocadas


async def etiquetar(evento_id: int) -> dict:
    """Etiqueta el repertorio del evento. Nunca levanta hacia arriba.

    Un lote que falla no detiene a los demas: sus canciones se quedan como
    estaban --sin etiqueta-- y el resto del repertorio igual mejora. Se puede
    volver a correr cuantas veces se quiera; lo ya etiquetado no se vuelve a
    consultar porque `pendientes` lo filtra.
    """
    if not modelo.disponible():
        return {"ok": False, "error": "sin_modelo", "pendientes": 0, "etiquetadas": 0}

    faltantes = await asyncio.to_thread(_leer_pendientes, evento_id)
    if not faltantes:
        return {"ok": True, "pendientes": 0, "etiquetadas": 0, "lotes": 0}

    lotes = [faltantes[i : i + LOTE] for i in range(0, len(faltantes), LOTE)][:TOPE_LOTES]
    etiquetadas, fallados = 0, 0

    for numero, lote in enumerate(lotes, start=1):
        try:
            datos, uso = await modelo._pedir(
                SISTEMA, _pedido(lote), ESQUEMA, PRESUPUESTO, TOPE_POR_PROVEEDOR
            )
        except Exception as error:  # noqa: BLE001
            registro.warning("lote %d de %d fallo: %s", numero, len(lotes), error)
            fallados += 1
            continue

        por_fila: dict[int, dict] = {}
        for cruda in datos.get("etiquetas") or []:
            indice = cruda.get("i")
            if not isinstance(indice, int) or not 0 <= indice < len(lote):
                continue          # indice inventado: la fila se queda sin etiqueta
            limpias = limpiar(cruda)
            if limpias:
                por_fila[lote[indice][0]] = limpias

        if por_fila:
            etiquetadas += await asyncio.to_thread(_aplicar, evento_id, por_fila)
        registro.info(
            "lote %d/%d: %d de %d etiquetadas (%s)",
            numero, len(lotes), len(por_fila), len(lote), uso.get("proveedor"),
        )

    return {
        "ok": True,
        "pendientes": len(faltantes),
        "lotes": len(lotes),
        "fallados": fallados,
        "etiquetadas": etiquetadas,
    }


def _leer_pendientes(evento_id: int):
    with Sesion() as sesion:
        return pendientes(sesion, evento_id)
