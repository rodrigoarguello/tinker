"""El gusto del salon: las dos ponderaciones y la confianza.

Puro, sin base y sin HTTP. Es aritmetica --lo reciente pesa mas, lo que varios
eligieron pesa mas-- y la aritmetica se prueba sola.

Importa que estas tres funciones esten probadas por separado: son las que
convierten un contador en un perfil, y si se equivocan nadie lo ve. Una tanda
rara se nota; un perfil que pondera mal produce tandas perfectamente
plausibles y sistematicamente equivocadas.
"""
from __future__ import annotations

import pytest

from app import config
from app.recomendador import publico as P
from app.recomendador.publico import PerfilPublico


# ── lo reciente pesa mas ─────────────────────────────────────────────────

def test_una_eleccion_de_recien_vale_entera():
    assert P.decaimiento(0) == 1.0


def test_a_la_vida_media_vale_la_mitad():
    """20 minutos es la vida media: no es un numero cualquiera, es LA perilla."""
    assert P.decaimiento(config.PERFIL_VIDA_MEDIA_MIN) == 0.5


def test_el_decaimiento_es_monotono():
    anteriores = [P.decaimiento(m) for m in (0, 5, 10, 20, 40, 90)]
    assert anteriores == sorted(anteriores, reverse=True)


def test_lo_viejo_no_llega_a_cero_ni_se_da_vuelta():
    """Una fiesta de ocho horas no puede producir pesos negativos ni ceros
    exactos: el perfil se inclina, no se borra."""
    assert 0 < P.decaimiento(480) < 0.01


# ── lo que eligieron varios pesa mas ─────────────────────────────────────

def test_una_sola_persona_no_recibe_bono():
    assert P.acuerdo(1) == 1.0


def test_la_segunda_persona_confirma():
    assert P.acuerdo(2) == 2.0


def test_el_acuerdo_no_es_lineal():
    """La sexta persona no aporta seis veces mas que la primera. Si contara
    lineal, cinco amigos del mismo gusto se llevarian la noche entera."""
    assert P.acuerdo(8) == 4.0
    assert P.acuerdo(8) < 8 * P.acuerdo(1)


def test_el_acuerdo_aguanta_un_cero():
    assert P.acuerdo(0) == 1.0


# ── la confianza ─────────────────────────────────────────────────────────

def test_sin_gente_el_perfil_no_vale_nada():
    assert PerfilPublico().confianza == 0.0


def test_la_confianza_crece_con_la_gente_y_se_planta_en_uno():
    def perfil(personas):
        return min(1.0, personas / config.PERSONAS_PARA_CONFIANZA)

    assert perfil(1) < perfil(2) < perfil(4)
    assert perfil(4) == 1.0
    assert perfil(40) == 1.0


# ── el perfil como objeto ────────────────────────────────────────────────

def test_los_dominantes_son_los_dos_de_arriba():
    p = PerfilPublico(generos={"rock": 1.0, "cumbia": 0.6, "pop": 0.3, "otros": 0.1})
    assert p.dominantes == ["rock", "cumbia"]
    assert p.secundarios == ["pop", "otros"]


def test_domina_solo_por_encima_del_umbral():
    p = PerfilPublico(generos={"rock": 1.0, "pop": 0.1})
    assert p.domina("rock")
    assert not p.domina("pop")
    assert not p.domina(None)


def test_sin_popularidad_el_descubrimiento_es_medio_punto():
    """No saber no es lo mismo que saber que no. Media es la respuesta honesta."""
    assert PerfilPublico().descubrimiento == 0.5
    assert PerfilPublico(mainstream=0.8).descubrimiento == pytest.approx(0.2)


def test_el_json_de_la_pantalla_no_lleva_ids_internos():
    """Lo que sale por el WebSocket lo ve cualquiera que abra la pantalla."""
    p = PerfilPublico(
        generos={"rock": 1.0}, frecuencia_tema={7: 3}, frecuencia_artista={"x": 2},
    )
    datos = p.como_json()
    crudo = repr(datos)
    assert "frecuencia_tema" not in crudo and "frecuencia_artista" not in crudo
    assert datos["generos"] == [{"genero": "rock", "peso": 1.0}]


def test_el_perfil_vacio_no_rompe_nada():
    """El primer teléfono de la noche encuentra esto, y tiene que jugar igual."""
    p = PerfilPublico()
    assert p.dominantes == [] and p.elecciones == 0
    assert p.como_json()["generos"] == []
