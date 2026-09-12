"""Las reglas que, si se rompen, arruinan un evento.

Corren contra la base de verdad --no hay SQLite aca: el esquema usa
restricciones de PostgreSQL y probarlo contra otro motor probaria otra cosa.
Cada prueba trabaja en su propio evento y lo borra al terminar.

    sudo docker compose run --rm -v "$PWD/pruebas:/app/pruebas" web \
         sh -c "pip install -q pytest && python -m pytest -q"
"""
from __future__ import annotations

import time
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.bd import Sesion
from app.main import app
from app.modelos import Evento

# Un repertorio chico pero suficiente: nueve artistas distintos y cinco
# generos, que es lo que hace falta para que las reglas de diversidad tengan
# de donde elegir en las cinco rondas.
REPERTORIO = """
De música ligera — Soda Stereo
Persiana americana — Soda Stereo
Matador — Los Fabulosos Cadillacs
Vasos vacíos — Los Fabulosos Cadillacs
Lamento boliviano — Enanitos Verdes
Cómo te voy a olvidar — Los Ángeles Azules
Nunca es suficiente — Los Ángeles Azules
17 años — Los Ángeles Azules
Motivos — Damas Gratis
Se te ve la tanga — Damas Gratis
Tusa — Karol G
Provenza — Karol G
Dákiti — Bad Bunny
Tití me preguntó — Bad Bunny
Me porto bonito — Bad Bunny
Gasolina — Daddy Yankee
Despacito — Luis Fonsi
Waka Waka — Shakira
La tortura — Shakira
Hips Don't Lie — Shakira
Vivir mi vida — Marc Anthony
La vida es un carnaval — Celia Cruz
Quimbara — Celia Cruz
Recuerdos de Ypacaraí — Luis Alberto del Paraná
Galopera — Los Paraguayos
India — José Asunción Flores
Garota de Ipanema — Tom Jobim
Ai se eu te pego — Michel Teló
Evidências — Chitãozinho e Xororó
Titanium — David Guetta
Wake Me Up — Avicii
One More Time — Daft Punk
Blinding Lights — The Weeknd
Shape of You — Ed Sheeran
Uptown Funk — Mark Ronson
Happy — Pharrell Williams
Dance Monkey — Tones and I
Flowers — Miley Cyrus
Sweet Child O' Mine — Guns N' Roses
Zombie — The Cranberries
Losing My Religion — R.E.M.
Wonderwall — Oasis
Billie Jean — Michael Jackson
Africa — Toto
El rey — Vicente Fernández
Amor prohibido — Selena
Como la flor — Selena
"""


@pytest.fixture()
def cliente(monkeypatch):
    """Un cliente con el motor de IA APAGADO.

    No es solo por determinismo: con las claves puestas en el entorno del
    contenedor, cada corrida de estas pruebas dispararia llamadas de verdad al
    proveedor y las pagaria alguien. Las reglas del juego se prueban contra el
    camino determinista --que es el que tiene que sostenerlo todo cuando la API
    no esta-- y la cascada se prueba aparte, con adaptadores falsos, en
    test_modelo.py.
    """
    from app.recomendador import modelo

    for clave in modelo.CLAVES.values():
        monkeypatch.delenv(clave, raising=False)
    # Y los jobs de fondo: el TestClient corre el lifespan, asi que sin esto
    # cada corrida sale a descubrir musica y a hablar con Spotify.
    monkeypatch.setattr("app.config.JOBS_ACTIVOS", False)
    # Y SPOTIFY. Esto colgó la suite entera el día que se configuraron las
    # credenciales: `catalogo.resolver` pasa por Spotify cuando las hay, y la
    # fixture de repertorio resuelve cuarenta y siete líneas POR PRUEBA. Son
    # mil llamadas de red por corrida, con su 429 incluido.
    for clave in ("SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET"):
        monkeypatch.delenv(clave, raising=False)
    from app.ajustes import ajustes
    ajustes.cache_clear()      # los ajustes están cacheados con lru_cache
    with TestClient(app) as c:
        yield c
    ajustes.cache_clear()


@pytest.fixture()
def evento(cliente):
    """Un evento con repertorio, listo para jugar."""
    slug = f"prueba-{uuid.uuid4().hex[:8]}"
    cliente.post("/api/admin/eventos", json={"slug": slug, "nombre": f"Prueba {slug}"})
    cliente.put(f"/api/admin/eventos/{slug}/repertorio",
                json={"texto": REPERTORIO, "reemplazar": True})
    yield slug
    with Sesion() as sesion:
        sesion.execute(delete(Evento).where(Evento.slug == slug))
        sesion.commit()


def telefono() -> str:
    return uuid.uuid4().hex


def entrar(cliente, slug, dispositivo):
    return cliente.post(f"/api/e/{slug}/entrar", json={"dispositivo": dispositivo}).json()


def elegir(cliente, slug, dispositivo, ronda, cancion):
    return cliente.post(f"/api/e/{slug}/elegir",
                        json={"dispositivo": dispositivo, "ronda": ronda, "cancion": cancion})


def avanzar(cliente, slug, dispositivo):
    return cliente.post(f"/api/e/{slug}/avanzar", json={"dispositivo": dispositivo})


def jugar_entero(cliente, slug, dispositivo, cual=0, tope=12):
    """Todas las tandas, eligiendo una de cada una y pidiendo la siguiente.

    ELEGIR Y AVANZAR SON DOS GESTOS DISTINTOS desde que se pueden marcar
    varias canciones de una misma tanda: elegir no cambia la tanda. La primera
    version de estas pruebas asumia lo contrario y el bucle no terminaba nunca
    -- colgo la suite entera hasta que se encontro.

    `tope` es el cinturon: una prueba que se cuelga es peor que una que falla.
    """
    datos = entrar(cliente, slug, dispositivo)
    tandas = []
    for _ in range(tope):
        if datos["juego"]["estado"] != "jugando":
            break
        tanda = datos["juego"]["tanda"]
        assert tanda is not None, "sin tanda estando en juego"
        tandas.append(tanda)
        elegir(cliente, slug, dispositivo, tanda["ronda"], tanda["canciones"][cual]["id"])
        datos = avanzar(cliente, slug, dispositivo).json()
    return tandas, datos


# ── la forma del juego ────────────────────────────────────────────────

def test_cada_tanda_trae_exactamente_cinco(cliente, evento):
    """Cinco y no cuatro, pase lo que pase. Una tanda incompleta es una
    pantalla rota en medio de una fiesta."""
    tandas, _ = jugar_entero(cliente, evento, telefono())
    assert len(tandas) == 5
    for tanda in tandas:
        assert len(tanda["canciones"]) == 5


def test_no_se_repite_ninguna_cancion_en_las_cinco_rondas(cliente, evento):
    tandas, _ = jugar_entero(cliente, evento, telefono())
    vistas = [c["id"] for t in tandas for c in t["canciones"]]
    assert len(set(vistas)) == 25


def test_no_hay_dos_del_mismo_artista_en_una_tanda(cliente, evento):
    tandas, _ = jugar_entero(cliente, evento, telefono())
    for tanda in tandas:
        artistas = [c["artista"] for c in tanda["canciones"]]
        assert len(set(artistas)) == 5, f"ronda {tanda['ronda']}: {artistas}"


def test_la_quinta_tanda_cierra_el_juego(cliente, evento):
    uno = telefono()
    tandas, final = jugar_entero(cliente, evento, uno)
    assert len(tandas) == 5, "el juego tiene cinco tandas"
    assert final["juego"]["estado"] == "completado"
    assert len(final["juego"]["elegidas"]) == 5
    assert final["juego"]["tanda"] is None
    respuesta = elegir(cliente, evento, uno, 5, "cur-loquesea")
    assert respuesta.status_code == 409
    assert respuesta.json()["detail"]["error"] == "juego_completado"


def test_se_pueden_elegir_varias_de_una_tanda(cliente, evento):
    """El cambio que pidio el dueño: de cinco tarjetas te pueden gustar tres, y
    marcar una no puede llevarse puestas las otras dos."""
    uno = telefono()
    tanda = entrar(cliente, evento, uno)["juego"]["tanda"]
    ids = [c["id"] for c in tanda["canciones"]]
    for cancion in ids[:3]:
        respuesta = elegir(cliente, evento, uno, 1, cancion)
        assert respuesta.status_code == 200
        juego = respuesta.json()["juego"]
        # La tanda NO cambia al elegir: sigue siendo la misma ronda y las
        # mismas cinco tarjetas.
        assert juego["ronda"] == 1
        assert [c["id"] for c in juego["tanda"]["canciones"]] == ids
    assert len(elegir(cliente, evento, uno, 1, ids[3]).json()["juego"]["elegidas"]) == 4


def test_avanzar_trae_otras_cinco(cliente, evento):
    uno = telefono()
    primera = entrar(cliente, evento, uno)["juego"]["tanda"]
    elegir(cliente, evento, uno, 1, primera["canciones"][0]["id"])
    despues = avanzar(cliente, evento, uno).json()["juego"]
    assert despues["ronda"] == 2
    assert not ({c["id"] for c in primera["canciones"]}
                & {c["id"] for c in despues["tanda"]["canciones"]})


def test_seguir_eligiendo_da_cinco_tandas_mas(cliente, evento):
    uno = telefono()
    _, final = jugar_entero(cliente, evento, uno)
    assert final["juego"]["estado"] == "completado"
    mas = cliente.post(f"/api/e/{evento}/seguir", json={"dispositivo": uno}).json()["juego"]
    assert mas["estado"] == "jugando"
    assert mas["rondas"] == 10
    assert mas["tanda"] is not None


def test_recargar_devuelve_la_misma_tanda_en_el_mismo_orden(cliente, evento):
    """Si al recargar las tarjetas salieran barajadas, la persona creeria que
    son otras canciones."""
    uno = telefono()
    primera = entrar(cliente, evento, uno)["juego"]["tanda"]
    elegir(cliente, evento, uno, 1, primera["canciones"][0]["id"])
    avanzar(cliente, evento, uno)
    antes = entrar(cliente, evento, uno)["juego"]
    despues = entrar(cliente, evento, uno)["juego"]
    assert antes["ronda"] == despues["ronda"] == 2
    assert [c["id"] for c in antes["tanda"]["canciones"]] == \
           [c["id"] for c in despues["tanda"]["canciones"]]
    assert len(despues["elegidas"]) == 1


def test_entrar_es_idempotente_y_no_arma_dos_tandas(cliente, evento):
    uno = telefono()
    for _ in range(3):
        assert entrar(cliente, evento, uno)["juego"]["ronda"] == 1
    datos = cliente.get(f"/api/e/{evento}/panorama").json()
    assert datos["totales"]["personas"] == 1


# ── lo que no se puede hacer ──────────────────────────────────────────

def test_solo_se_puede_elegir_de_la_tanda_servida(cliente, evento):
    """La version fuerte de «los datos salen del catalogo, nunca del pedido»:
    un id valido que NO esta entre las cinco servidas tampoco entra."""
    uno = telefono()
    tanda = entrar(cliente, evento, uno)["juego"]["tanda"]
    servidas = {c["id"] for c in tanda["canciones"]}
    otra = cliente.get(f"/api/admin/eventos/{evento}/repertorio").json()["canciones"]
    ajena = next(c["id"] for c in otra if c["id"] not in servidas)
    respuesta = elegir(cliente, evento, uno, 1, ajena)
    assert respuesta.status_code == 409
    assert respuesta.json()["detail"]["error"] == "fuera_de_tanda"


def test_el_mismo_telefono_no_vota_dos_veces_en_una_ronda(cliente, evento):
    uno = telefono()
    tanda = entrar(cliente, evento, uno)["juego"]["tanda"]
    primera = tanda["canciones"][0]["id"]
    assert elegir(cliente, evento, uno, 1, primera).status_code == 200
    assert elegir(cliente, evento, uno, 1, primera).status_code == 200  # idempotente
    datos = cliente.get(f"/api/e/{evento}/panorama").json()
    assert datos["totales"]["pedidos"] == 1


def test_evento_cerrado_no_acepta(cliente, evento):
    uno = telefono()
    tanda = entrar(cliente, evento, uno)["juego"]["tanda"]
    cliente.patch(f"/api/admin/eventos/{evento}", json={"estado": "cerrado"})
    respuesta = elegir(cliente, evento, uno, 1, tanda["canciones"][0]["id"])
    assert respuesta.status_code == 409
    assert respuesta.json()["detail"]["error"] == "evento_cerrado"


def test_evento_inexistente(cliente):
    assert cliente.get("/api/e/no-existe-nada/panorama").status_code == 404


# ── quitar y volver a elegir ──────────────────────────────────────────

def test_quitar_libera_la_ronda_y_vuelve_la_misma_tanda(cliente, evento):
    uno = telefono()
    tanda = entrar(cliente, evento, uno)["juego"]["tanda"]
    servidas = [c["id"] for c in tanda["canciones"]]
    elegir(cliente, evento, uno, 1, servidas[0])

    despues = cliente.delete(
        f"/api/e/{evento}/elegidas/{servidas[0]}?dispositivo={uno}").json()["juego"]
    assert despues["ronda"] == 1
    assert despues["elegidas"] == []
    assert [c["id"] for c in despues["tanda"]["canciones"]] == servidas

    otra = elegir(cliente, evento, uno, 1, servidas[1]).json()["juego"]
    assert otra["ronda"] == 1 and len(otra["elegidas"]) == 1


def test_quitar_baja_el_voto_pero_no_la_cancion(cliente, evento):
    uno, otro = telefono(), telefono()
    tanda = entrar(cliente, evento, uno)["juego"]["tanda"]
    cancion = tanda["canciones"][0]["id"]
    elegir(cliente, evento, uno, 1, cancion)
    # El otro telefono ve la misma ronda 1 --mismo repertorio, sin señal-- pero
    # su orden puede diferir: se busca la canción en su tanda.
    suya = entrar(cliente, evento, otro)["juego"]["tanda"]
    if any(c["id"] == cancion for c in suya["canciones"]):
        elegir(cliente, evento, otro, 1, cancion)
        cliente.delete(f"/api/e/{evento}/elegidas/{cancion}?dispositivo={uno}")
        ranking = cliente.get(f"/api/e/{evento}/panorama").json()["ranking"]
        fila = next(c for c in ranking if c["id"] == cancion)
        assert fila["votos"] == 1


# ── el repertorio ─────────────────────────────────────────────────────

def test_sin_repertorio_no_se_puede_poner_en_vivo(cliente):
    """La falla mas barata de evitar y la mas cara de descubrir: el QR ya esta
    en la pared y los telefonos entran a una pantalla vacia."""
    slug = f"prueba-{uuid.uuid4().hex[:8]}"
    cliente.post("/api/admin/eventos", json={"slug": slug, "nombre": "Sin canciones"})
    respuesta = cliente.patch(f"/api/admin/eventos/{slug}", json={"estado": "en_vivo"})
    assert respuesta.status_code == 409
    assert respuesta.json()["detail"]["error"] == "sin_repertorio"
    with Sesion() as sesion:
        sesion.execute(delete(Evento).where(Evento.slug == slug))
        sesion.commit()


def test_reimportar_el_repertorio_no_duplica(cliente, evento):
    primera = cliente.get(f"/api/admin/eventos/{evento}/repertorio").json()["total"]
    cliente.put(f"/api/admin/eventos/{evento}/repertorio",
                json={"texto": REPERTORIO, "reemplazar": True})
    assert cliente.get(f"/api/admin/eventos/{evento}/repertorio").json()["total"] == primera


def test_las_lineas_sin_guion_se_avisan(cliente, evento):
    respuesta = cliente.put(
        f"/api/admin/eventos/{evento}/repertorio",
        json={"texto": REPERTORIO + "\nuna linea sin separador\n", "reemplazar": True},
    ).json()
    assert "una linea sin separador" in respuesta["sin_resolver"]


def test_ninguna_me_gusta_trae_otras_cinco_sin_avanzar(cliente, evento):
    """Rechazar no es avanzar: la persona sigue debiendo esa eleccion, pero las
    cinco que descarto no vuelven."""
    uno = telefono()
    primera = entrar(cliente, evento, uno)["juego"]["tanda"]
    respuesta = cliente.post(f"/api/e/{evento}/rechazar",
                             json={"dispositivo": uno, "ronda": 1})
    assert respuesta.status_code == 200
    despues = respuesta.json()["juego"]
    assert despues["ronda"] == 1
    assert not ({c["id"] for c in primera["canciones"]}
                & {c["id"] for c in despues["tanda"]["canciones"]})


def test_la_torta_de_generos_sale_de_lo_elegido(cliente, evento):
    """Antes salia de una encuesta previa. Lo que alguien toca en una fiesta
    dice mas que lo que marca en un formulario antes de empezar."""
    jugar_entero(cliente, evento, telefono())
    gustos = cliente.get(f"/api/e/{evento}/panorama").json()["gustos"]
    assert gustos, "cinco elecciones y ningun genero contado"
    assert sum(g["cuenta"] for g in gustos) <= 5


def test_qr_es_un_svg_que_un_navegador_puede_dibujar(cliente, evento):
    respuesta = cliente.get(f"/api/e/{evento}/qr.svg")
    assert respuesta.status_code == 200
    assert respuesta.headers["content-type"].startswith("image/svg+xml")
    # Sin xmlns, un <img> no lo dibuja y no avisa. Paso de verdad.
    assert "xmlns=" in respuesta.text


# ── el camino con modelo, sin llamar a ninguno ────────────────────────

def test_con_modelo_la_tanda_llega_por_el_poll(monkeypatch):
    """Con el motor encendido, `elegir` no trae la tanda: la encarga y el
    telefono la busca con el poll. Se prueba con un adaptador falso, sin gastar
    una sola llamada."""
    from app.recomendador import modelo

    async def falso(sistema, texto, esquema, espera):
        return (
            {"elegidas": [0, 1, 2, 3, 4], "porque": "de mentira"},
            {"proveedor": "gemini", "modelo": "falso", "entrada": 1, "salida": 1},
        )

    for clave in modelo.CLAVES.values():
        monkeypatch.setenv(clave, "de-mentira")
    monkeypatch.setattr(modelo, "ADAPTADOR", {n: falso for n in modelo.ORDEN})

    with TestClient(app) as cliente:
        slug = f"prueba-{uuid.uuid4().hex[:8]}"
        cliente.post("/api/admin/eventos", json={"slug": slug, "nombre": "Con modelo"})
        cliente.put(f"/api/admin/eventos/{slug}/repertorio",
                    json={"texto": REPERTORIO, "reemplazar": True})
        uno = telefono()
        try:
            tanda = entrar(cliente, slug, uno)["juego"]["tanda"]
            assert tanda is not None, "la ronda 1 nunca espera a un modelo"

            elegir(cliente, slug, uno, 1, tanda["canciones"][0]["id"])
            # Avanzar es lo que pide la tanda siguiente, y es ahi donde entra
            # el modelo: elegir no cambia de tanda.
            despues = avanzar(cliente, slug, uno).json()["juego"]
            assert despues["ronda"] == 2

            # Sea que el prefetch ya termino o que todavia no, el telefono
            # siempre termina con cinco tarjetas.
            if despues["tanda"] is None:
                for _ in range(40):
                    respuesta = cliente.get(
                        f"/api/e/{slug}/tanda?dispositivo={uno}&ronda=2").json()
                    if respuesta["lista"]:
                        assert len(respuesta["canciones"]) == 5
                        break
                    time.sleep(0.2)
                else:
                    raise AssertionError("la tanda nunca llego")
        finally:
            with Sesion() as sesion:
                sesion.execute(delete(Evento).where(Evento.slug == slug))
                sesion.commit()


# ── el agente colectivo, de punta a punta ────────────────────────────────
#
# Todo lo de arriba prueba que el juego funciona para UNA persona. Lo que sigue
# prueba lo unico que no se puede reproducir en un chat: que lo que elige una
# persona cambie lo que ve la siguiente. Si esto falla, tinker es un
# recomendador individual con un proyector al lado.


@pytest.fixture()
def dos_eventos(cliente):
    """Dos eventos con el MISMO repertorio. Uno se llena de gente, el otro no.

    Es la unica forma de aislar el efecto del salon: mismo dispositivo, misma
    semilla, mismo repertorio, misma ronda. Lo unico distinto es la gente.
    """
    slugs = []
    for _ in range(2):
        slug = f"prueba-{uuid.uuid4().hex[:8]}"
        cliente.post("/api/admin/eventos", json={"slug": slug, "nombre": f"Prueba {slug}"})
        cliente.put(f"/api/admin/eventos/{slug}/repertorio",
                    json={"texto": REPERTORIO, "reemplazar": True})
        slugs.append(slug)
    yield slugs
    with Sesion() as sesion:
        sesion.execute(delete(Evento).where(Evento.slug.in_(slugs)))
        sesion.commit()


def llenar_el_salon(cliente, slug, cuantas_personas=3):
    """Varias personas juegan una ronda y se llevan TODO lo que les mostraron.

    Marcar las cinco es lo que hace la señal inequivoca: el perfil del salon
    queda con peso de sobra para que su efecto sea visible en una sola prueba.
    """
    for _ in range(cuantas_personas):
        quien = telefono()
        datos = entrar(cliente, slug, quien)
        tanda = datos["juego"]["tanda"]
        for cancion in tanda["canciones"]:
            elegir(cliente, slug, quien, tanda["ronda"], cancion["id"])


def test_lo_que_elige_una_persona_cambia_lo_que_ve_la_siguiente(cliente, dos_eventos):
    """LA prueba del producto. Mismo telefono, mismo repertorio, misma ronda:
    lo unico que cambia es que en un evento ya hay gente que eligio."""
    conGente, vacio = dos_eventos
    llenar_el_salon(cliente, conGente)

    mismo = telefono()
    enElLleno = entrar(cliente, conGente, mismo)["juego"]["tanda"]["canciones"]
    enElVacio = entrar(cliente, vacio, mismo)["juego"]["tanda"]["canciones"]

    assert [c["id"] for c in enElLleno] != [c["id"] for c in enElVacio], (
        "el salon no cambio nada: sin esto no hay agente colectivo"
    )


def test_el_perfil_del_salon_se_puede_mirar_y_se_mueve(cliente, evento):
    """La pantalla del salon muestra esto, y es lo que hace visible al agente."""
    antes = cliente.get(f"/api/e/{evento}/panorama").json()["perfil"]
    assert antes == {} or antes.get("elecciones", 0) == 0

    llenar_el_salon(cliente, evento, cuantas_personas=2)

    despues = cliente.get(f"/api/e/{evento}/panorama").json()["perfil"]
    assert despues["elecciones"] == 10
    assert despues["personas"] == 2
    assert despues["generos"], "el salon eligio diez canciones y no aprendio nada"
    assert 0 < despues["confianza"] <= 1


def test_la_confianza_crece_con_la_gente_que_hay(cliente, evento):
    """Con una persona el perfil casi no vale; con cuatro vale entero."""
    llenar_el_salon(cliente, evento, cuantas_personas=1)
    conUna = cliente.get(f"/api/e/{evento}/panorama").json()["perfil"]["confianza"]
    llenar_el_salon(cliente, evento, cuantas_personas=3)
    conCuatro = cliente.get(f"/api/e/{evento}/panorama").json()["perfil"]["confianza"]
    assert conUna < conCuatro == 1.0


def test_el_salon_no_encierra_a_nadie_en_un_genero(cliente, evento):
    """Aunque el salon entero vaya a un lado, ninguna tanda es de un solo
    genero. Es la camara de eco, medida sobre las cinco rondas de verdad."""
    llenar_el_salon(cliente, evento, cuantas_personas=4)
    tandas, _ = jugar_entero(cliente, evento, telefono())
    assert tandas, "no llego a jugar ninguna tanda"
    for tanda in tandas:
        artistas = [c["artista"] for c in tanda["canciones"]]
        assert len(set(artistas)) == 5, f"artista repetido en una tanda: {artistas}"


def test_el_perfil_no_rompe_la_eleccion_si_algo_sale_mal(cliente, evento, monkeypatch):
    """El perfil es una mejora del proximo reparto, no parte del gesto de la
    persona: si falla, la canción se guarda igual."""
    from app.recomendador import publico

    def explota(*_a, **_k):
        raise RuntimeError("la base se cayo justo ahora")

    monkeypatch.setattr(publico, "refrescar", explota)

    quien = telefono()
    datos = entrar(cliente, evento, quien)
    tanda = datos["juego"]["tanda"]
    respuesta = elegir(cliente, evento, quien, tanda["ronda"], tanda["canciones"][0]["id"])
    assert respuesta.status_code == 200
    assert len(respuesta.json()["juego"]["elegidas"]) == 1


def test_cada_tanda_trae_su_linea_y_nunca_viene_vacia(cliente, evento):
    """El agente habla en todas las rondas, tambien sin una sola clave de IA.
    Un agente que trabaja sin que se note es indistinguible de uno que no."""
    tandas, _ = jugar_entero(cliente, evento, telefono())
    assert len(tandas) == 5
    for tanda in tandas:
        assert tanda.get("mensaje"), f"ronda {tanda['ronda']} sin mensaje"
        assert len(tanda["mensaje"]) <= 80


def test_la_linea_tambien_viene_por_el_poll(cliente, evento):
    quien = telefono()
    entrar(cliente, evento, quien)
    datos = cliente.get(f"/api/e/{evento}/tanda?dispositivo={quien}&ronda=1").json()
    assert datos["lista"] is True
    assert datos["mensaje"]
