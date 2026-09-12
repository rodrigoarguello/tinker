"""tinker — la gente arma la musica del evento desde su telefono.

Dos paginas y un circuito: el QR del televisor lleva a participante.html,
cada telefono carga hasta diez canciones, y pantalla.html las muestra en vivo.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app import panorama
from app.ajustes import ajustes
from app.api import admin, publico
from app.bd import Sesion, con_sesion, motor
from app.catalogo import clave_artista
from app.catalogo import local as catalogo_local
from app.modelos import Cancion, Evento, Repertorio
from app.jobs.planificador import planificador
from app.recomendador.prefetch import prefetch
from app.tiempo_real.hub import hub

logging.basicConfig(level=logging.INFO, format="%(levelname)s [%(name)s] %(message)s")
registro = logging.getLogger("tinker")

ESTATICOS = Path(__file__).parent / "estaticos"

# Las rutas que NO son slugs de eventos. Sin esto, /health lo atenderia el
# comodin de abajo y responderia "no existe ese evento".
RESERVADAS = {"api", "health", "ws", "estaticos", "admin", "favicon.ico", "robots.txt",
              "docs", "openapi.json"}
SLUG = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


@asynccontextmanager
async def ciclo(app: FastAPI):
    # El prefetch necesita el bucle para poder agendarse desde el threadpool.
    # Sin esto no falla nada: simplemente no se prepara ninguna tanda por
    # adelantado y todas salen del respaldo determinista.
    prefetch.enlazar(asyncio.get_running_loop())
    if ajustes().semilla:
        await asyncio.to_thread(sembrar)
    # Los jobs de fondo: descubrimiento, scoring, rotacion, refill y la cola de
    # Spotify. Nada de esto toca el camino caliente del juego.
    planificador.arrancar()
    yield
    await planificador.apagar()
    await prefetch.apagar()


# Swagger solo si TINKER_DOCS lo pide. En produccion la app es publica y la
# superficie de la API no tiene por que estarlo; en un clon limpio,
# compose.dev.yaml lo enciende y /docs es la forma mas rapida de ver que el
# circuito quedo bien levantado.
app = FastAPI(
    title="tinker",
    docs_url="/docs" if ajustes().docs else None,
    redoc_url=None,
    lifespan=ciclo,
)
app.include_router(publico.ruteador)
app.include_router(admin.ruteador)
app.mount("/estaticos", StaticFiles(directory=ESTATICOS), name="estaticos")


def sembrar() -> None:
    """Un evento de demostracion CON su repertorio, si la base esta vacia.

    Con repertorio y no solo el evento: desde que la lista curada es la unica
    fuente, un evento sin canciones no se puede jugar, y una demostracion que
    no se puede jugar no demuestra nada.
    """
    with Sesion() as sesion:
        if sesion.scalar(select(Evento).limit(1)):
            return
        evento = Evento(
            slug="san-lorenzo-2026",
            nombre="IA, ¿Tín que querés? — San Lorenzo",
            lugar="San Lorenzo",
            estado="en_vivo",
        )
        sesion.add(evento)
        sesion.commit()

        generos = catalogo_local.generos()
        crudo = json.loads((Path(catalogo_local.__file__).with_name("semilla.json")).read_text("utf-8"))
        for orden, fila in enumerate(crudo, start=1):
            cancion = Cancion(
                proveedor="local",
                proveedor_id=fila["id"],
                titulo=fila["titulo"],
                artista=fila["artista"],
            )
            sesion.add(cancion)
            sesion.flush()
            sesion.add(
                Repertorio(
                    evento_id=evento.id,
                    cancion_id=cancion.id,
                    orden=orden,
                    linea=f"{fila['titulo']} — {fila['artista']}",
                    artista_clave=clave_artista(fila["artista"]),
                    genero=generos.get(fila["id"]),
                )
            )
        sesion.commit()
        registro.info(
            "evento de demostracion creado: san-lorenzo-2026 con %d canciones", len(crudo)
        )


@app.get("/health")
def salud() -> JSONResponse:
    """Dice si el proceso Y su base responden. Es lo unico abierto que no
    depende de que exista ningun evento."""
    try:
        with motor.connect() as conexion:
            conexion.execute(text("select 1"))
    except Exception as error:  # noqa: BLE001
        registro.warning("health: la base no responde: %s", error)
        return JSONResponse({"ok": False, "bd": False}, status_code=503)
    return JSONResponse({"ok": True, "bd": True})


# ───────────────────────────── el canal en vivo ─────────────────────────────

LATIDO = 25  # segundos


@app.websocket("/ws/{slug}")
async def vivo(socket: WebSocket, slug: str) -> None:
    """Una pantalla --o un telefono-- mirando un evento.

    El latido cada 25s no es decorativo: sin trafico, cualquier intermediario
    corta un socket ocioso y la pantalla del salon se congela sin ningun error
    a la vista. El proxy tiene ademas un read_timeout de una hora para este
    location; las dos cosas juntas son lo que evita el modo de falla peor de
    todos, que es una fiesta mirando datos viejos.
    """
    if slug in RESERVADAS or not SLUG.match(slug):
        await socket.close(code=4404)
        return
    with Sesion() as sesion:
        evento = sesion.scalar(select(Evento).where(Evento.slug == slug))
        if evento is None:
            await socket.close(code=4404)
            return
        inicial = panorama.completo(sesion, evento)

    await socket.accept()
    await hub.entrar(slug, socket)
    await socket.send_json({"tipo": "panorama"} | inicial)
    try:
        while True:
            # No esperamos nada del cliente: el receive esta para enterarnos
            # de que se fue. El timeout lo convierte en el reloj del latido.
            try:
                await asyncio.wait_for(socket.receive_text(), timeout=LATIDO)
            except asyncio.TimeoutError:
                await socket.send_json({"tipo": "latido"})
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        await hub.salir(slug, socket)


# ───────────────────────────── las paginas ─────────────────────────────

@app.get("/")
def portada() -> FileResponse:
    return FileResponse(ESTATICOS / "portada.html")


@app.get("/admin")
def tablero() -> FileResponse:
    """Detras de Authelia por configuracion del proxy, no por codigo."""
    return FileResponse(ESTATICOS / "admin.html")


@app.get("/{slug}/pantalla")
def pantalla(slug: str, sesion: Session = Depends(con_sesion)) -> FileResponse:
    publico.evento_o_404(sesion, slug)
    return FileResponse(ESTATICOS / "pantalla.html")


@app.get("/{slug}")
def participante(slug: str, sesion: Session = Depends(con_sesion)) -> FileResponse:
    """El destino del QR. Va ULTIMO: es un comodin y se comeria todo lo de
    arriba si estuviera antes."""
    if slug in RESERVADAS:
        raise HTTPException(404, {"error": "no_encontrado"})
    publico.evento_o_404(sesion, slug)
    return FileResponse(ESTATICOS / "participante.html")
