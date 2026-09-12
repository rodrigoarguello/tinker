"""El planificador: corre los jobs cada tanto, dentro del proceso.

Adentro y no en un cron del anfitrion, por tres razones concretas:

  · un cron necesitaria `docker exec` y una copia del entorno -- dos cosas mas
    que mantener;
  · el proceso ya tiene el bucle de eventos y la conexion a la base;
  · y lo mas importante: si el contenedor no esta, los jobs no tienen que
    correr. Con un cron externo, corren y fallan.

Todos los jobs son de fondo. NINGUNO toca el camino caliente del juego, y si
todos fallan, las cinco rondas siguen funcionando con el repertorio que ya
esta en la base.
"""
from __future__ import annotations

import asyncio
import logging
import time

from app import config
from app.jobs import tareas

registro = logging.getLogger("tinker.planificador")


class Job:
    def __init__(self, nombre: str, cada_segundos: float, correr, al_arrancar: bool = False):
        self.nombre = nombre
        self.cada = cada_segundos
        self.correr = correr
        # `al_arrancar` en falso para todo lo que sale a internet: levantar el
        # contenedor no tiene por que disparar cuarenta pedidos a APIs ajenas.
        self.proximo = 0.0 if al_arrancar else time.monotonic() + cada_segundos


def catalogo() -> list[Job]:
    hora = 3600
    return [
        # Cada minuto, y es el unico que importa que sea frecuente: es lo que
        # hace que una canción elegida aparezca en la playlist mientras la
        # fiesta sigue.
        Job("spotify:cola", 60, tareas.spotify, al_arrancar=True),
        # Lo que suena, al televisor. Quince segundos es el piso real del
        # planificador --su vuelta duerme ese tanto-- y no gasta una sola
        # llamada mientras no haya ninguna pantalla conectada.
        Job("spotify:sonando", 15, tareas.sonando, al_arrancar=True),
        Job("music:score", config.HORAS_SCORING * hora, tareas.puntuar, al_arrancar=True),
        Job("music:perfil", 120, tareas.perfilar, al_arrancar=True),
        Job("music:rotation", 6 * hora, tareas.rotar),
        Job("music:discover:py", config.HORAS_PARAGUAY * hora, tareas.descubrir_paraguay),
        Job("music:discover:global", config.HORAS_GLOBAL * hora, tareas.descubrir_global),
        Job("music:discover:genres", config.HORAS_GENEROS * hora, tareas.descubrir_generos),
        Job("music:refill", 30 * 60, tareas.rellenar),
    ]


class Planificador:
    def __init__(self) -> None:
        self._tarea: asyncio.Task | None = None
        self._jobs: list[Job] = []

    def arrancar(self) -> None:
        if self._tarea is not None:
            return
        if not config.JOBS_ACTIVOS:
            registro.info("jobs de fondo apagados (TINKER_JOBS=no)")
            return
        self._jobs = catalogo()
        if not config.DESCUBRIMIENTO_ACTIVO:
            # Apagar el descubrimiento NO apaga Spotify ni el scoring: son
            # cosas distintas y se apagan por separado.
            self._jobs = [j for j in self._jobs if not j.nombre.startswith("music:discover")]
            registro.info("descubrimiento apagado por configuracion")
        self._tarea = asyncio.create_task(self._girar())
        registro.info("planificador con %d jobs", len(self._jobs))

    async def _girar(self) -> None:
        # Un respiro antes de empezar: el arranque tiene mejores cosas que
        # hacer que salir a internet, como atender el primer QR.
        await asyncio.sleep(10)
        while True:
            try:
                ahora = time.monotonic()
                for job in self._jobs:
                    if ahora < job.proximo:
                        continue
                    job.proximo = ahora + job.cada
                    await self._uno(job)
                await asyncio.sleep(15)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                # El planificador no se muere por un job roto. Si se muriera,
                # dejaria de procesarse la cola de Spotify y nadie se enteraria
                # hasta que alguien mirara la playlist vacia.
                registro.exception("el planificador tropezo; sigue")
                await asyncio.sleep(30)

    async def _uno(self, job: Job) -> None:
        arranque = time.monotonic()
        try:
            # Se decide POR LA FUNCION y no llamandola: `job.correr()` para ver
            # que devuelve ya lo ejecuto --en el bucle, que es justo donde no
            # tiene que correr-- y despues lo ejecutaba otra vez en el hilo.
            # Todo job sincronico corria dos veces y congelaba el latido del
            # WebSocket de todas las pantallas mientras tanto.
            if asyncio.iscoroutinefunction(job.correr):
                resultado = await job.correr()
            else:
                resultado = await asyncio.to_thread(job.correr)
            ms = int((time.monotonic() - arranque) * 1000)
            if resultado and resultado != {"agregadas": 0, "pendientes": 0}:
                registro.info("%s (%d ms): %s", job.nombre, ms, resultado)
        except Exception as error:  # noqa: BLE001
            registro.warning("%s fallo: %s: %s", job.nombre, type(error).__name__, error)

    async def apagar(self) -> None:
        if self._tarea is not None:
            self._tarea.cancel()
            try:
                await self._tarea
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._tarea = None


planificador = Planificador()
