"""La cascada de modelos: gemini -> claude -> openai.

Portada del adaptador de modelos de otro proyecto de esta casa, donde ya se
resolvieron las tres formas de API, la salida estructurada y el orden. Lo que se
cambio, y por que:

  · EL PRESUPUESTO ES OTRO. En focus el techo es nginx (60 s) porque hay una
    persona mirando un spinner. Aca NADIE espera esta llamada: corre en el
    prefetch mientras la persona mira la tanda anterior. El techo real es mas
    duro -- si tarda mas que lo que alguien tarda en tocar una tarjeta, la
    tanda llega tarde y el respaldo determinista ya la reemplazo.

  · EL PRESUPUESTO SE PASA COMO ARGUMENTO, no es una constante: el prefetch le
    entrega lo que le quedo despues de esperar el semaforo.

  · NO VALIDA NADA. Lo que devuelve son indices de una lista; que sean cinco,
    distintos y de artistas distintos lo impone `reglas.imponer`. Un modelo que
    contesta seis o repite un artista es un caso normal, no un error.

APAGADO POR DEFECTO: sin ninguna clave en el entorno, `disponible()` da False,
nadie llama a nada y el juego se juega entero con las reglas deterministas. Es
la misma decision de focus -- sin clave no hay funcion, no hay interfaz rota.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass

registro = logging.getLogger("tinker.modelo")

# El orden lo decidio una medicion previa de esta casa en ESTE servidor:
# gemini-3.5-flash-lite contesta en 3,2 s, claude-opus-5 en 6,2
# y gpt-5-mini en 24. Para un juego donde la espera se nota, el orden importa
# mas que en cualquier otro uso de la casa.
ORDEN = ("gemini", "claude", "openai")

CLAVES = {"gemini": "GEMINI_API_KEY", "claude": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}

MODELOS = {
    "gemini": os.environ.get("TINKER_IA_GEMINI", "gemini-3.5-flash-lite"),
    "openai": os.environ.get("TINKER_IA_OPENAI", "gpt-5-mini"),
    "claude": os.environ.get("TINKER_IA_CLAUDE", "claude-opus-5"),
}

# Ocho segundos era mas que la paciencia de cualquiera: sumados a los dos que
# puede esperar la cola, daban diez contra una PACIENCIA de cuatro, y la tanda
# del modelo llegaba tarde CASI SIEMPRE. Seis, y descontando lo que se espero.
PRESUPUESTO_TOTAL = 6.0
TOPE_POR_PROVEEDOR = 4.0
MINIMO_UTIL = 1.5          # con menos que esto no vale la pena intentar


class Rechazado(Exception):
    """El modelo no contesto. No es un error del servidor: es una respuesta."""


class SinProveedor(Exception):
    """Ninguno de los que tienen clave pudo contestar."""


def disponible() -> bool:
    """Si HAY algun proveedor con clave. Se lee en cada llamada y no una vez al
    importar: asi agregar una clave al `.env` y reiniciar alcanza."""
    return any(os.environ.get(CLAVES[p]) for p in ORDEN)


def proveedores() -> list[str]:
    """Los que tienen clave, en orden. Vacio si no hay ninguno."""
    return [p for p in ORDEN if os.environ.get(CLAVES[p])]


# ── el pedido ────────────────────────────────────────────────────────────

SISTEMA = """\
Sos el DJ de una fiesta que está pasando ahora, y estás leyendo el salón.

Hay gente con el teléfono en la mano. Cada una escanea un QR y ve 5 canciones;
toca las que le gustan y pide otras cinco. Lo que elige una persona cambia lo
que ven las siguientes: vos no armás la lista de alguien, armás la noche.

Te damos tres cosas: lo que eligió ESTA persona, lo que viene eligiendo EL
SALÓN, y un pozo de candidatas con su número de índice. Elegís 5 del pozo.

Reglas:
- Exactamente 5, por su número de índice del pozo.
- Nunca dos del mismo artista en la misma tanda.
- Al menos 3 géneros distintos.
- NO TE ENCIERRES. Si tres personas eligieron Metallica, la tanda siguiente no
  son cinco de metal: es Metallica, Foo Fighters, Guns N' Roses, Red Hot Chili
  Peppers y una inesperada que pegue en energía. Aprendé el gusto sin volverlo
  una cámara de eco.
- DOS DE LAS CINCO TIENEN QUE ABRIR: géneros distintos de los que esta persona
  viene eligiendo y distintos de los que dominan el salón. Son la única forma
  que tiene alguien de torcer una noche que se cerró.
- El reparto de las otras tres te lo decimos en el pedido: cuántas siguen a la
  persona y cuántas al salón.
- Privilegiá canciones reconocibles: es un evento social, no una sesión de
  descubrimiento.
- Sin saltos bruscos de energía entre una tanda y la siguiente.

Y dos textos, que son distintos:
- `mensaje`: UNA oración de menos de 80 caracteres que SÍ se le muestra a la
  persona, arriba de las tarjetas. Dice qué está pasando en el salón, no qué
  elegiste. "El rock está creciendo entre las elecciones." Nunca nombres una
  canción ni un artista de la tanda: le arruina la sorpresa y dirige el voto.
- `porque`: una oración interna, para entender una tanda rara sin volver a
  llamarte. No se muestra."""

ESQUEMA = {
    "type": "object",
    "properties": {
        # SIN `minItems` ni `maxItems`: la salida estructurada NO los admite y
        # la API contesta 400 --«For 'array' type, property 'maxItems' is not
        # supported»--. La cantidad la pide el prompt y la IMPONE
        # `reglas.imponer`, que es donde de verdad se puede garantizar.
        "elegidas": {"type": "array", "items": {"type": "integer"}},
        # Paralelo a `elegidas`. No se muestra hoy; se guarda para poder
        # reconstruir despues por que el agente eligio lo que eligio.
        "razones": {"type": "array", "items": {"type": "string"}},
        # Lo que el modelo entendio del salon. Se guarda con la tanda: «que
        # entendio el agente a las once y media» es una pregunta que se hace
        # siempre y tarde, y sin esto no se puede contestar.
        "lectura": {
            "type": "object",
            "properties": {
                "generos": {"type": "array", "items": {"type": "string"}},
                "energia": {"type": "number"},
                "decadas": {"type": "array", "items": {"type": "string"}},
                "descubrimiento": {"type": "number"},
            },
            "required": ["generos", "energia", "decadas", "descubrimiento"],
            "additionalProperties": False,
        },
        "mensaje": {"type": "string"},
        "porque": {"type": "string"},
    },
    "required": ["elegidas", "razones", "lectura", "mensaje", "porque"],
    "additionalProperties": False,
}


# ── los tres adaptadores ─────────────────────────────────────────────────
#
# Los tres devuelven lo MISMO: `(datos, uso)`. Lo que cambia entre ellos es
# todo lo demas -- la URL, la cabecera, donde va el esquema y, sobre todo,
# donde viene el texto en la respuesta.

async def _gemini(sistema, texto, esquema, espera):
    import httpx

    async with httpx.AsyncClient(timeout=espera) as cliente:
        respuesta = await cliente.post(
            "https://generativelanguage.googleapis.com/v1beta/interactions",
            headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"]},
            # Sin roles: esta API toma UNA entrada de texto.
            json={
                "model": MODELOS["gemini"],
                "input": f"{sistema}\n\n{texto}",
                "response_format": {
                    "type": "text",
                    "mime_type": "application/json",
                    "schema": esquema,
                },
            },
        )
        respuesta.raise_for_status()
        datos = respuesta.json()
    # El texto vive en `steps[].content[].text`, y el primer paso es una firma
    # del razonamiento sin contenido. Se recorre en vez de indexar: la cantidad
    # de pasos cambia con el modelo.
    crudo = next(
        (b["text"] for paso in datos.get("steps", [])
         for b in (paso.get("content") or []) if b.get("text")),
        None,
    )
    if not crudo:
        raise Rechazado()
    uso = datos.get("usage", {})
    # Los tokens de pensamiento se cobran como salida y vienen aparte: sumarlos
    # es la diferencia entre un costo real y uno que subestima cinco veces.
    return json.loads(crudo), {
        "proveedor": "gemini",
        "modelo": MODELOS["gemini"],
        "entrada": uso.get("total_input_tokens", 0),
        "salida": uso.get("total_output_tokens", 0) + uso.get("total_thought_tokens", 0),
    }


async def _openai(sistema, texto, esquema, espera):
    import httpx

    async with httpx.AsyncClient(timeout=espera) as cliente:
        respuesta = await cliente.post(
            "https://api.openai.com/v1/responses",
            headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"},
            json={
                "model": MODELOS["openai"],
                "input": [
                    {"role": "system", "content": sistema},
                    {"role": "user", "content": texto},
                ],
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": "tanda",
                        "strict": True,
                        "schema": esquema,
                    }
                },
            },
        )
        respuesta.raise_for_status()
        datos = respuesta.json()
    # `output` trae primero un bloque de razonamiento SIN contenido y despues
    # el mensaje. Leer `output[0]` da vacio siempre.
    crudo = next(
        (b["text"] for o in datos.get("output", [])
         for b in (o.get("content") or []) if b.get("text")),
        None,
    )
    if not crudo:
        raise Rechazado()
    uso = datos.get("usage", {})
    return json.loads(crudo), {
        "proveedor": "openai",
        "modelo": datos.get("model", MODELOS["openai"]),
        "entrada": uso.get("input_tokens", 0),
        "salida": uso.get("output_tokens", 0),
    }


async def _claude(sistema, texto, esquema, espera):
    from anthropic import AsyncAnthropic

    respuesta = await AsyncAnthropic(timeout=espera).beta.messages.create(
        model=MODELOS["claude"],
        max_tokens=2000,
        # Un clasificador puede rechazar un pedido --el titulo de una canción
        # alcanza-- y con esto la API lo reintenta sola en otro modelo.
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        system=sistema,
        output_config={
            # Esfuerzo bajo a proposito: elegir cinco de una lista de sesenta no
            # es un problema que mejore pensando mas, y aca cada segundo se nota.
            "effort": "low",
            "format": {"type": "json_schema", "schema": esquema},
        },
        messages=[{"role": "user", "content": texto}],
    )
    # ANTES de leer `content`: un rechazo llega con HTTP 200 y `content` vacio,
    # y `content[0]` reventaria con un IndexError que en el log parece un error
    # nuestro y no una decision del modelo.
    if respuesta.stop_reason == "refusal":
        raise Rechazado()
    crudo = next((b.text for b in respuesta.content if b.type == "text"), None)
    if not crudo:
        raise Rechazado()
    return json.loads(crudo), {
        "proveedor": "claude",
        "modelo": respuesta.model,
        "entrada": respuesta.usage.input_tokens,
        "salida": respuesta.usage.output_tokens,
    }


ADAPTADOR = {"gemini": _gemini, "openai": _openai, "claude": _claude}


async def _pedir(
    sistema: str, texto: str, esquema: dict, presupuesto: float,
    tope_proveedor: float | None = None,
) -> tuple[dict, dict]:
    """La cadena. Se prueban en orden y gana el primero que conteste.

    UN FALLO PASA AL SIGUIENTE; UN RECHAZO NO. Si el modelo se nego por
    politica, preguntarle lo mismo a otro es pagar dos veces por el mismo no.

    EL PRESUPUESTO ES DEL CONJUNTO. Al ultimo se le da todo lo que quede:
    cortarlo como a los anteriores no tiene sentido porque no hay a quien
    saltar despues.
    """
    # CUATRO SEGUNDOS POR PROVEEDOR ES EL TOPE DE UNA TANDA, no de cualquier
    # pedido. Etiquetar cuarenta canciones son cuarenta objetos de salida y no
    # entra en cuatro segundos ni de casualidad: el tagger daba timeout en los
    # tres proveedores y no etiquetaba nada. Quien llama sabe cuanto puede
    # esperar; la constante es solo el valor por defecto.
    tope = tope_proveedor or TOPE_POR_PROVEEDOR
    fin = time.monotonic() + presupuesto
    con_clave = proveedores()
    if not con_clave:
        raise SinProveedor("ninguno tiene clave")

    problemas = []
    for nombre in con_clave:
        queda = fin - time.monotonic()
        if queda < MINIMO_UTIL:
            problemas.append(f"{nombre}: sin tiempo")
            continue
        espera = queda if nombre == con_clave[-1] else min(tope, queda)
        try:
            return await ADAPTADOR[nombre](sistema, texto, esquema, espera)
        except Rechazado:
            raise
        except Exception as error:  # noqa: BLE001
            problemas.append(f"{nombre}: {type(error).__name__}")
            registro.warning("%s fallo: %s: %s", nombre, type(error).__name__, str(error)[:200])
    raise SinProveedor("; ".join(problemas))


@dataclass(frozen=True)
class Sugerencia:
    """Lo que contesto el modelo, sin validar. Validar es cosa de `reglas`."""

    indices: list[int]
    porque: str
    mensaje: str
    lectura: dict
    razones: list[str]
    uso: dict


async def sugerir(texto: str, presupuesto: float = PRESUPUESTO_TOTAL) -> Sugerencia:
    """Lo que contesto el modelo. NO VALIDA NADA.

    Que sean cinco, distintas, de artistas distintos y del pozo lo impone
    `reglas.imponer`; que el mensaje se pueda mostrar lo decide
    `mensajes.del_modelo`. Un modelo que contesta seis o repite un artista es un
    caso normal, no un error.
    """
    datos, uso = await _pedir(SISTEMA, texto, ESQUEMA, presupuesto)
    crudas = datos.get("elegidas") or []
    lectura = datos.get("lectura")
    return Sugerencia(
        indices=[int(i) for i in crudas if isinstance(i, (int, float))],
        porque=(datos.get("porque") or "")[:500],
        mensaje=(datos.get("mensaje") or "")[:300],
        lectura=lectura if isinstance(lectura, dict) else {},
        razones=[r for r in (datos.get("razones") or []) if isinstance(r, str)][:5],
        uso=uso,
    )
