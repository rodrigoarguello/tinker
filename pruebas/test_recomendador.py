"""El contrato de una tanda, sin base y sin HTTP.

Aca se prueba lo que de verdad sostiene el producto: que `imponer` devuelva
cinco candidatas validas PASE LO QUE PASE del otro lado. Cada prueba es un modo
de falla del modelo escrito como caso.
"""
from __future__ import annotations

import pytest

from app import config
from app.recomendador import reglas
from app.recomendador.pozo import Candidata, Pozo
from app.recomendador.publico import PerfilPublico

GENEROS = ["rock", "cumbia", "pop", "reggaeton", "romantica", "electronica"]


def candidata(i: int, artista: str | None = None, genero: str | None = None) -> Candidata:
    return Candidata(
        cancion_id=i,
        proveedor_id=f"cur-{i:04d}",
        titulo=f"Canción {i}",
        artista=artista or f"Artista {i}",
        artista_clave=(artista or f"artista {i}").lower(),
        genero=genero or GENEROS[i % len(GENEROS)],
        epoca=["80", "90", "00", "10", "20"][i % 5],
        idioma=["es", "en", "pt"][i % 3],
        intensidad=(i % 5) + 1,
    )


def pozo(cuantas: int = 40, elegidas: list[Candidata] | None = None, ronda: int = 1) -> Pozo:
    return Pozo(
        disponibles=[candidata(i) for i in range(1, cuantas + 1)],
        elegidas=elegidas or [],
        vistas=frozenset(),
        ronda=ronda,
        semilla=12345,
    )


def validar(tanda, p):
    fallas = reglas.violaciones(tanda, p)
    assert not fallas, f"tanda invalida: {fallas}"


# ── lo que el modelo puede hacer mal ──────────────────────────────────

def test_una_propuesta_perfecta_pasa_entera():
    p = pozo()
    propuesta = p.disponibles[:5]
    tanda = reglas.imponer(propuesta, p)
    assert {c.cancion_id for c in tanda} == {c.cancion_id for c in propuesta}
    validar(tanda, p)


def test_propuesta_vacia_igual_da_cinco():
    """El caso «el modelo no contesto». Es el mas importante de todos: es lo
    que pasa cuando no hay clave configurada, y tiene que ser invisible."""
    p = pozo()
    tanda = reglas.imponer([], p)
    assert len(tanda) == 5
    validar(tanda, p)


def test_propuesta_corta_se_completa():
    p = pozo()
    tanda = reglas.imponer(p.disponibles[:2], p)
    assert len(tanda) == 5
    validar(tanda, p)


def test_propuesta_larga_se_recorta():
    p = pozo()
    tanda = reglas.imponer(p.disponibles[:40], p)
    assert len(tanda) == 5
    validar(tanda, p)


def test_indices_inventados_se_descartan():
    """Lo que llegaria si el modelo devolviera numeros de otra lista."""
    p = pozo()
    inventadas = [candidata(999), candidata(1000), p.disponibles[3]]
    tanda = reglas.imponer(inventadas, p)
    assert len(tanda) == 5
    assert all(c.cancion_id <= 40 for c in tanda)
    validar(tanda, p)


def test_repetidas_en_la_propuesta_se_descartan():
    p = pozo()
    una = p.disponibles[0]
    tanda = reglas.imponer([una, una, una, una, una], p)
    assert len({c.cancion_id for c in tanda}) == 5
    validar(tanda, p)


def test_cinco_del_mismo_artista_no_entran_juntas():
    p = Pozo(
        disponibles=[candidata(i, artista="Los Palmeras") for i in range(1, 6)]
                    + [candidata(i) for i in range(6, 30)],
        elegidas=[], vistas=frozenset(), ronda=1, semilla=7,
    )
    tanda = reglas.imponer(p.disponibles[:5], p)
    artistas = [c.artista_clave for c in tanda]
    assert len(set(artistas)) == 5
    validar(tanda, p)


def test_nunca_sirve_algo_ya_visto():
    vistas = {1, 2, 3, 4, 5}
    p = Pozo(
        disponibles=[c for c in (candidata(i) for i in range(1, 40)) if c.cancion_id not in vistas],
        elegidas=[], vistas=frozenset(vistas), ronda=2, semilla=7,
    )
    # El modelo propone justo las que la persona ya vio.
    tanda = reglas.imponer([candidata(i) for i in vistas], p)
    assert not ({c.cancion_id for c in tanda} & vistas)
    validar(tanda, p)


# ── los bordes del repertorio ─────────────────────────────────────────

def test_pozo_de_exactamente_cinco():
    p = pozo(cuantas=5)
    tanda = reglas.imponer([], p)
    assert len(tanda) == 5


def test_pozo_de_un_solo_genero_igual_da_cinco():
    """La precedencia en accion: «exactamente cinco» gana sobre «generos
    distintos». Una tanda de cuatro tarjetas es peor que una monotona."""
    p = Pozo(
        disponibles=[candidata(i, genero="cumbia") for i in range(1, 20)],
        elegidas=[], vistas=frozenset(), ronda=1, semilla=3,
    )
    tanda = reglas.imponer([], p)
    assert len(tanda) == 5
    assert len({c.cancion_id for c in tanda}) == 5


def test_pozo_de_un_solo_artista_igual_da_cinco():
    p = Pozo(
        disponibles=[candidata(i, artista="Soda Stereo") for i in range(1, 20)],
        elegidas=[], vistas=frozenset(), ronda=1, semilla=3,
    )
    tanda = reglas.imponer([], p)
    assert len(tanda) == 5
    assert len({c.cancion_id for c in tanda}) == 5


# ── diversidad y afinidad ─────────────────────────────────────────────

def test_la_ronda_uno_mezcla_generos():
    p = pozo(cuantas=40)
    tanda = reglas.diversa(p)
    assert len({c.genero for c in tanda}) >= reglas.MIN_GENEROS


def test_la_ronda_uno_admite_como_mucho_dos_del_mismo_genero():
    p = pozo(cuantas=40)
    tanda = reglas.diversa(p)
    for genero in {c.genero for c in tanda}:
        assert sum(1 for c in tanda if c.genero == genero) <= reglas.tope_genero(1)


def test_no_repite_un_artista_que_la_persona_ya_eligio():
    """Cinco canciones con el mismo artista dos veces es una seleccion pobre, y
    la regla de «un artista por tanda» no lo cubre: son tandas distintas."""
    ya = candidata(1, artista="Fito Páez")
    p = Pozo(
        disponibles=[candidata(2, artista="Fito Páez"), candidata(3, artista="Fito Páez")]
                    + [candidata(i) for i in range(4, 30)],
        elegidas=[ya], vistas=frozenset({1}), ronda=3, semilla=11,
    )
    tanda = reglas.afin(p)
    assert not any(c.artista_clave == "fito páez" for c in tanda)


def test_la_afinidad_satura_y_no_encierra():
    """Tres cumbias elegidas no se responden con cinco cumbias."""
    elegidas = [candidata(i, genero="cumbia") for i in range(1, 4)]
    p = Pozo(
        disponibles=[candidata(i, genero="cumbia") for i in range(10, 25)]
                    + [candidata(i, genero="rock") for i in range(25, 40)],
        elegidas=elegidas, vistas=frozenset({1, 2, 3}), ronda=4, semilla=5,
    )
    tanda = reglas.afin(p)
    assert sum(1 for c in tanda if c.genero == "cumbia") <= reglas.tope_genero(4)


def test_la_misma_semilla_da_el_mismo_orden():
    """Recargar la pagina no puede reordenar las tarjetas: la persona creeria
    que son otras canciones."""
    p = pozo()
    assert [c.cancion_id for c in reglas.imponer([], p)] == \
           [c.cancion_id for c in reglas.imponer([], p)]


def test_dos_telefonos_distintos_ven_ordenes_distintos():
    uno = pozo()
    otro = Pozo(disponibles=uno.disponibles, elegidas=[], vistas=frozenset(), ronda=1, semilla=999)
    assert [c.cancion_id for c in reglas.imponer([], uno)] != \
           [c.cancion_id for c in reglas.imponer([], otro)]


# ── violaciones() dice QUE se rompio ──────────────────────────────────

def test_violaciones_nombra_el_problema():
    p = pozo()
    assert "cantidad:3" in reglas.violaciones(p.disponibles[:3], p)
    repetida = [p.disponibles[0]] * 5
    assert "repetidas" in reglas.violaciones(repetida, p)
    assert "fuera_del_pozo" in reglas.violaciones([candidata(999)] * 5, p)


# ── las señales que antes se medían y no se usaban ────────────────────

def test_la_fatiga_baja_una_cancion_aunque_encaje():
    """Una canción que salió seis veces en la última media hora no puede seguir
    ganando el puesto solo porque encaja bien."""
    p = pozo(cuantas=20)
    cansada = p.disponibles[0]
    conFatiga = Pozo(
        disponibles=p.disponibles, elegidas=[], vistas=frozenset(), ronda=1, semilla=4,
        fatiga={cansada.cancion_id: 8.0},
    )
    tanda = reglas.diversa(conFatiga)
    assert cansada.cancion_id not in {c.cancion_id for c in tanda}


def test_el_gusto_del_salon_inclina_sin_mandar():
    """El perfil colectivo mueve el puntaje, no reemplaza la diversidad: la
    tanda sigue teniendo varios géneros."""
    p = Pozo(
        disponibles=[candidata(i, genero="cumbia") for i in range(1, 12)]
                    + [candidata(i, genero="rock") for i in range(12, 24)]
                    + [candidata(i, genero="pop") for i in range(24, 34)],
        elegidas=[], vistas=frozenset(), ronda=1, semilla=8,
        perfil={"cumbia": 40, "rock": 3},
    )
    tanda = reglas.diversa(p)
    assert len({c.genero for c in tanda}) >= 2
    assert sum(1 for c in tanda if c.genero == "cumbia") >= 1


def test_rechazar_un_genero_lo_saca_de_las_tandas():
    """El reinicio de diversidad: quien rechazó urbano no recibe más urbano."""
    p = Pozo(
        disponibles=[candidata(i, genero="urbano") for i in range(1, 12)]
                    + [candidata(i, genero="rock") for i in range(12, 24)]
                    + [candidata(i, genero="cumbia") for i in range(24, 34)],
        elegidas=[], vistas=frozenset(), ronda=2, semilla=2,
        rechazados_propios={"urbano": 5}, rechazos_seguidos=2,
    )
    tanda = reglas.diversa(p)
    assert not any(c.genero == "urbano" for c in tanda), [c.genero for c in tanda]


def test_el_reinicio_apaga_la_afinidad():
    elegidas = [candidata(1, genero="cumbia")]
    normal = Pozo(disponibles=[candidata(i) for i in range(2, 40)], elegidas=elegidas,
                  vistas=frozenset({1}), ronda=4, semilla=3)
    reiniciado = Pozo(disponibles=normal.disponibles, elegidas=elegidas,
                      vistas=frozenset({1}), ronda=4, semilla=3, rechazos_seguidos=3)
    assert reglas.reiniciar_diversidad(reiniciado)
    assert not reglas.reiniciar_diversidad(normal)
    assert reglas.mezcla(4, reiniciado) < reglas.mezcla(4, normal)


def test_el_evento_escala_la_proporcion():
    """`explotar_pct` del evento mueve la mezcla: una discoteca puede pesar más
    a lo conocido que un bar."""
    conservador = Pozo(disponibles=[], elegidas=[], vistas=frozenset(), ronda=4,
                       semilla=1, explotar=1.0)
    explorador = Pozo(disponibles=[], elegidas=[], vistas=frozenset(), ronda=4,
                      semilla=1, explotar=0.3)
    assert reglas.mezcla(4, conservador) > reglas.mezcla(4, explorador)


# ── el agente colectivo: lo que una persona elige cambia lo que ve la que
#    viene despues ─────────────────────────────────────────────────────────
#
# Estas son las pruebas del pedido. Hasta acá se probaba que una tanda fuera
# VALIDA; lo que sigue prueba que sea ADAPTATIVA, que es otra cosa y es la que
# justifica el producto.


def salon(generos: dict, personas: int = 4, **extra) -> PerfilPublico:
    """Un salón con un gusto declarado. `personas` gobierna la confianza."""
    return PerfilPublico(
        generos=generos,
        personas=personas,
        elecciones=personas * 3,
        confianza=min(1.0, personas / config.PERSONAS_PARA_CONFIANZA),
        **extra,
    )


def con_salon(p: Pozo, perfil: PerfilPublico, ronda: int | None = None) -> Pozo:
    return Pozo(
        disponibles=p.disponibles, elegidas=p.elegidas, vistas=p.vistas,
        ronda=ronda if ronda is not None else p.ronda, semilla=p.semilla,
        publico=perfil,
    )


def test_el_salon_cambia_la_tanda_de_quien_recien_llega():
    """EL NUCLEO. Mismo pozo, misma persona, misma semilla: lo unico que cambia
    es lo que eligio el resto de la gente. Si la tanda no cambia, no hay agente
    colectivo -- hay un recomendador individual con una decoracion."""
    base = pozo(cuantas=40)
    rockero = reglas.diversa(con_salon(base, salon({"rock": 1.0, "pop": 0.2})))
    cumbiero = reglas.diversa(con_salon(base, salon({"cumbia": 1.0, "pop": 0.2})))
    assert {c.cancion_id for c in rockero} != {c.cancion_id for c in cumbiero}


def test_el_salon_inclina_hacia_lo_que_viene_funcionando():
    """El primero que entra a las tres de la mañana no empieza de cero."""
    base = pozo(cuantas=40)
    conRock = reglas.diversa(con_salon(base, salon({"rock": 1.0})))
    sinNada = reglas.diversa(base)
    assert sum(1 for c in conRock if c.genero == "rock") >= \
           sum(1 for c in sinNada if c.genero == "rock")


def test_el_salon_no_convierte_la_tanda_en_un_solo_genero():
    """Si tres personas eligen Metallica, la tanda siguiente NO son cinco de
    metal. Es la camara de eco, escrita como caso."""
    base = pozo(cuantas=40)
    p = con_salon(base, salon({"rock": 1.0}), ronda=4)
    tanda = reglas.diversa(p)
    assert sum(1 for c in tanda if c.genero == "rock") <= 2, [c.genero for c in tanda]
    assert len({c.genero for c in tanda}) >= 3


def test_el_genero_dominante_aprieta_su_propio_tope():
    p = con_salon(pozo(cuantas=40), salon({"rock": 1.0, "pop": 0.1}), ronda=4)
    assert reglas.tope_de("rock", 4, p.publico) == 2
    assert reglas.tope_de("pop", 4, p.publico) == 3


def test_las_dos_abiertas_no_salen_del_genero_dominante():
    """La regla que impide encerrarse: aunque el gusto este clarisimo, dos de
    las cinco cartas ofrecen otra cosa."""
    elegidas = [candidata(i, genero="rock") for i in range(1, 3)]
    p = Pozo(
        disponibles=[candidata(i, genero="rock") for i in range(3, 25)]
                    + [candidata(i, genero="cumbia") for i in range(25, 45)],
        elegidas=elegidas, vistas=frozenset({1, 2}), ronda=4, semilla=7,
        publico=salon({"rock": 1.0}),
    )
    tanda = reglas.afin(p)
    assert sum(1 for c in tanda if c.genero != "rock") >= 2, [c.genero for c in tanda]


def test_con_poca_gente_manda_el_gusto_propio():
    """«Si todavia existen pocos participantes, aumentar el peso del perfil
    individual». Con una sola persona, el salon casi no existe."""
    base = pozo(cuantas=40)
    unaPersona = reglas.diversa(con_salon(base, salon({"cumbia": 1.0}, personas=1)))
    muchas = reglas.diversa(con_salon(base, salon({"cumbia": 1.0}, personas=8)))
    assert sum(1 for c in muchas if c.genero == "cumbia") >= \
           sum(1 for c in unaPersona if c.genero == "cumbia")


def test_el_reparto_de_ranuras_es_tres_y_dos():
    elegidas = [candidata(1, genero="rock")]
    for ronda in (2, 3, 4, 5):
        p = Pozo(disponibles=[candidata(i) for i in range(2, 40)], elegidas=elegidas,
                 vistas=frozenset({1}), ronda=ronda, semilla=3, publico=salon({"rock": 1.0}))
        reparto = reglas.ranuras(p)
        assert len(reparto) == 5
        assert reparto.count("contraste") == config.ABIERTAS
        assert reparto.count("individual") + reparto.count("colectivo") == config.ADAPTADAS


def test_el_salon_gana_ranuras_a_medida_que_avanzan_las_rondas():
    """Ronda 2 es toda de la persona; ronda 5 la comparte con el salon."""
    elegidas = [candidata(1, genero="rock")]

    def colectivas(ronda):
        p = Pozo(disponibles=[candidata(i) for i in range(2, 40)], elegidas=elegidas,
                 vistas=frozenset({1}), ronda=ronda, semilla=3, publico=salon({"rock": 1.0}))
        return reglas.ranuras(p).count("colectivo")

    assert colectivas(2) == 0
    assert colectivas(5) > colectivas(2)


def test_la_ronda_uno_no_tiene_ranuras_adaptadas():
    p = con_salon(pozo(), salon({"rock": 1.0}), ronda=1)
    assert reglas.ranuras(p) == ["explorar"] * 5


def test_rechazar_tandas_enteras_abre_la_siguiente():
    """Quien descarto dos tandas al hilo no esta pidiendo mas de lo mismo."""
    elegidas = [candidata(1, genero="rock")]
    normal = Pozo(disponibles=[candidata(i) for i in range(2, 40)], elegidas=elegidas,
                  vistas=frozenset({1}), ronda=4, semilla=3, publico=salon({"rock": 1.0}))
    harto = Pozo(disponibles=normal.disponibles, elegidas=elegidas, vistas=frozenset({1}),
                 ronda=4, semilla=3, publico=normal.publico, rechazos_seguidos=3)
    assert reglas.ranuras(harto).count("contraste") > reglas.ranuras(normal).count("contraste")


# ── las bandas del dueño: ganan, no obligan ──────────────────────────────

def test_la_lista_del_dueño_empuja_fuerte_al_principio_y_casi_nada_al_final():
    prioritaria = Candidata(
        cancion_id=99, proveedor_id="cur-0099", titulo="X", artista="Foo Fighters",
        artista_clave="foo fighters", genero="rock", prioritaria=True,
    )
    assert reglas.bono_prioritaria(prioritaria, 1) > reglas.bono_prioritaria(prioritaria, 5)
    assert reglas.bono_prioritaria(prioritaria, 5) > 0


def test_una_canción_comun_no_recibe_bono():
    assert reglas.bono_prioritaria(candidata(1), 1) == 0.0


def test_sin_cuota_las_prioritarias_aparecen_igual_en_la_ronda_uno():
    """El empuje tiene que alcanzar cuando no sabemos nada: es el unico momento
    en que la lista del dueño es la mejor apuesta que hay."""
    comunes = [candidata(i) for i in range(1, 30)]
    delDueño = [
        Candidata(cancion_id=100 + i, proveedor_id=f"cur-{100 + i}", titulo=f"T{i}",
                  artista=f"Grupo {i}", artista_clave=f"grupo {i}",
                  genero=GENEROS[i % len(GENEROS)], prioritaria=True)
        for i in range(10)
    ]
    p = Pozo(disponibles=comunes + delDueño, elegidas=[], vistas=frozenset(),
             ronda=1, semilla=4, prioritarias_objetivo=0)
    tanda = reglas.diversa(p)
    assert sum(1 for c in tanda if c.prioritaria) >= 3, [c.artista for c in tanda]


def test_la_cuota_dura_sigue_disponible_para_quien_la_quiera():
    """Se quito la OBLIGACION, no el mecanismo: un evento puede volver a pedirla."""
    comunes = [candidata(i) for i in range(1, 30)]
    delDueño = [
        Candidata(cancion_id=100 + i, proveedor_id=f"cur-{100 + i}", titulo=f"T{i}",
                  artista=f"Grupo {i}", artista_clave=f"grupo {i}",
                  genero=GENEROS[i % len(GENEROS)], prioritaria=True)
        for i in range(10)
    ]
    p = Pozo(disponibles=comunes + delDueño, elegidas=[], vistas=frozenset(),
             ronda=1, semilla=4, prioritarias_objetivo=3)
    tanda = reglas.imponer([], p)
    assert sum(1 for c in tanda if c.prioritaria) >= 3
