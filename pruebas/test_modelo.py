"""La cascada de modelos, SIN GASTAR UNA SOLA LLAMADA.

La tecnica es la de las pruebas del mismo adaptador en otro proyecto de esta
casa: se sustituyen los adaptadores por funciones que declaran quien habria
contestado, y se ponen claves de mentira en el entorno. Lo que se prueba es la
DECISION --a quien se llama, en que orden, que pasa cuando uno falla-- que es
donde estan los errores caros; la forma de cada API ya la fijo focus
llamandolas de verdad.

Las expectativas salen de `modelo.ORDEN` y no de nombres escritos a mano: el
dia que el orden cambie, estas pruebas siguen diciendo la verdad.
"""
from __future__ import annotations

import os

import pytest

from app.recomendador import modelo


@pytest.fixture()
def entorno(monkeypatch):
    """Claves de mentira para los tres y adaptadores sustituibles."""
    for clave in modelo.CLAVES.values():
        monkeypatch.setenv(clave, "de-mentira")
    llamados: list[str] = []
    monkeypatch.setattr(modelo, "ADAPTADOR", dict(modelo.ADAPTADOR))
    return llamados


def responde(nombre, llamados, indices=(0, 1, 2, 3, 4)):
    async def adaptador(sistema, texto, esquema, espera):
        llamados.append(nombre)
        return (
            {"elegidas": list(indices), "porque": f"contesto {nombre}"},
            {"proveedor": nombre, "modelo": f"{nombre}-de-prueba", "entrada": 10, "salida": 5},
        )
    return adaptador


def falla(nombre, llamados, error=RuntimeError):
    async def adaptador(sistema, texto, esquema, espera):
        llamados.append(nombre)
        raise error("se cayo")
    return adaptador


def rechaza(nombre, llamados):
    async def adaptador(sistema, texto, esquema, espera):
        llamados.append(nombre)
        raise modelo.Rechazado()
    return adaptador


# ── el orden ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_contesta_el_primero_del_orden(entorno):
    for nombre in modelo.ORDEN:
        modelo.ADAPTADOR[nombre] = responde(nombre, entorno)
    s = await modelo.sugerir("hola")
    assert entorno == [modelo.ORDEN[0]]
    assert s.uso["proveedor"] == modelo.ORDEN[0]
    assert s.indices == [0, 1, 2, 3, 4]


@pytest.mark.asyncio
async def test_si_el_primero_falla_salta_al_segundo(entorno):
    primero, segundo, tercero = modelo.ORDEN
    modelo.ADAPTADOR[primero] = falla(primero, entorno)
    modelo.ADAPTADOR[segundo] = responde(segundo, entorno)
    modelo.ADAPTADOR[tercero] = responde(tercero, entorno)
    s = await modelo.sugerir("hola")
    assert entorno == [primero, segundo]
    assert s.uso["proveedor"] == segundo


@pytest.mark.asyncio
async def test_con_los_tres_caidos_sinproveedor(entorno):
    for nombre in modelo.ORDEN:
        modelo.ADAPTADOR[nombre] = falla(nombre, entorno)
    with pytest.raises(modelo.SinProveedor):
        await modelo.sugerir("hola")
    assert entorno == list(modelo.ORDEN)


@pytest.mark.asyncio
async def test_un_rechazo_corta_la_cadena(entorno):
    """Preguntarle lo mismo a otro proveedor es pagar dos veces por el mismo no."""
    primero, segundo, _ = modelo.ORDEN
    modelo.ADAPTADOR[primero] = rechaza(primero, entorno)
    modelo.ADAPTADOR[segundo] = responde(segundo, entorno)
    with pytest.raises(modelo.Rechazado):
        await modelo.sugerir("hola")
    assert entorno == [primero]


@pytest.mark.asyncio
async def test_sin_clave_ni_se_intenta(entorno, monkeypatch):
    primero = modelo.ORDEN[0]
    monkeypatch.delenv(modelo.CLAVES[primero])
    for nombre in modelo.ORDEN:
        modelo.ADAPTADOR[nombre] = responde(nombre, entorno)
    await modelo.sugerir("hola")
    assert primero not in entorno


@pytest.mark.asyncio
async def test_sin_ninguna_clave_no_hay_funcion(monkeypatch):
    for clave in modelo.CLAVES.values():
        monkeypatch.delenv(clave, raising=False)
    assert modelo.disponible() is False
    assert modelo.proveedores() == []
    with pytest.raises(modelo.SinProveedor):
        await modelo.sugerir("hola")


@pytest.mark.asyncio
async def test_sin_presupuesto_no_se_llama_a_nadie(entorno):
    for nombre in modelo.ORDEN:
        modelo.ADAPTADOR[nombre] = responde(nombre, entorno)
    with pytest.raises(modelo.SinProveedor):
        await modelo.sugerir("hola", presupuesto=0.0)
    assert entorno == []


# ── el contrato de lo que devuelve ────────────────────────────────────

@pytest.mark.asyncio
async def test_los_indices_no_enteros_se_descartan(entorno):
    async def raro(sistema, texto, esquema, espera):
        return (
            {"elegidas": [0, "dos", None, 3.0, 4], "porque": "x"},
            {"proveedor": "x", "modelo": "x", "entrada": 1, "salida": 1},
        )
    modelo.ADAPTADOR[modelo.ORDEN[0]] = raro
    s = await modelo.sugerir("hola")
    assert s.indices == [0, 3, 4]


@pytest.mark.asyncio
async def test_una_respuesta_sin_elegidas_no_revienta(entorno):
    async def vacia(sistema, texto, esquema, espera):
        return ({"porque": "no se me ocurrio nada"},
                {"proveedor": "x", "modelo": "x", "entrada": 1, "salida": 1})
    modelo.ADAPTADOR[modelo.ORDEN[0]] = vacia
    s = await modelo.sugerir("hola")
    assert s.indices == []
    assert s.porque


def test_el_esquema_no_lleva_minitems():
    """`minItems`/`maxItems` hacen que la API conteste 400. La cantidad la pide
    el prompt y la impone `reglas.imponer`."""
    texto = str(modelo.ESQUEMA)
    assert "minItems" not in texto and "maxItems" not in texto


# ── el mensaje que SI se muestra ──────────────────────────────────────
#
# Es la unica salida del modelo que llega a los ojos de una persona, asi que es
# la unica donde una frase mala se ve. Python decide si se muestra.

def test_el_mensaje_del_modelo_se_usa_si_es_corto_y_no_spoilea():
    from app.recomendador import mensajes
    from app.recomendador.pozo import Candidata

    tanda = [Candidata(1, "cur-1", "Enter Sandman", "Metallica", "metallica")]
    assert mensajes.del_modelo("El rock está creciendo.", tanda) == "El rock está creciendo."


def test_un_mensaje_largo_no_se_muestra():
    from app.recomendador import mensajes
    assert mensajes.del_modelo("x" * 200, []) is None


def test_un_mensaje_con_saltos_de_linea_se_aplana():
    from app.recomendador import mensajes
    assert mensajes.del_modelo("Rock\n  creciendo", []) == "Rock creciendo"


def test_un_mensaje_que_nombra_una_canción_de_la_tanda_se_descarta():
    """Nombrarla le adelanta la respuesta a la persona y dirige el voto, que es
    justo lo que el barajado de posiciones existe para evitar."""
    from app.recomendador import mensajes
    from app.recomendador.pozo import Candidata

    tanda = [Candidata(1, "cur-1", "Enter Sandman", "Metallica", "metallica")]
    assert mensajes.del_modelo("Va a salir Enter Sandman, preparate.", tanda) is None
    assert mensajes.del_modelo("Metallica manda esta noche.", tanda) is None


def test_sin_mensaje_del_modelo_siempre_hay_uno_propio():
    """Con cero claves configuradas el agente igual habla. Es la misma decision
    que atraviesa el proyecto entero: sin modelo hay menos, no hay roto."""
    from app.recomendador import mensajes
    from app.recomendador.publico import PerfilPublico

    assert mensajes.del_modelo(None, []) is None
    for ronda in (1, 2, 3, 4, 5):
        assert mensajes.para(ronda, PerfilPublico(), semilla=ronda)


# ── el etiquetador: tampoco inventa ───────────────────────────────────

def test_el_etiquetador_descarta_lo_que_no_esta_en_el_vocabulario():
    from app.recomendador import etiquetador

    assert etiquetador.limpiar(
        {"genero": "heavy metal", "epoca": "1970", "idioma": "ingles", "intensidad": 9}
    ) == {}


def test_el_etiquetador_salva_lo_que_si_vale_de_una_respuesta_mixta():
    """Que el genero venga mal no tiene por que tirar el idioma de la misma
    canción: cada etiqueta se valida por separado."""
    from app.recomendador import etiquetador

    assert etiquetador.limpiar(
        {"genero": "trap", "epoca": "90", "idioma": "es", "intensidad": 3}
    ) == {"epoca": "90", "idioma": "es", "intensidad": 3}


def test_el_etiquetador_normaliza_mayusculas():
    from app.recomendador import etiquetador

    assert etiquetador.limpiar(
        {"genero": " ROCK ", "epoca": "80", "idioma": "EN", "intensidad": 4}
    ) == {"genero": "rock", "epoca": "80", "idioma": "en", "intensidad": 4}


def test_el_esquema_del_etiquetador_tampoco_lleva_minitems():
    from app.recomendador import etiquetador
    texto = str(etiquetador.ESQUEMA)
    assert "minItems" not in texto and "maxItems" not in texto
