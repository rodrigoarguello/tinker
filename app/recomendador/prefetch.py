"""El obrero de fondo: prepara la tanda siguiente mientras la persona mira la
actual.

DOS REGLAS QUE NO SE NEGOCIAN:

1. La llamada al modelo NUNCA ocurre dentro de un pedido que un telefono esta
   esperando. No es una optimizacion: evita el modo de falla donde doscientos
   telefonos esperan ocho segundos cada uno.

2. Se agenda con `call_soon_threadsafe`, NO con `create_task`. Las rutas que
   tocan la base corren en el threadpool, donde no hay bucle de eventos, y un
   `create_task` desde ahi levanta «no running event loop»: la tarea no nace y
   NADA falla a la vista. Ya paso en jau (`app/demo/motor.py`), donde la sesion
   de demostracion se creaba y el motor nunca arrancaba.

Y una tercera que se descubre tarde si no esta escrita: dentro de la tarea,
toda consulta va por `asyncio.to_thread` y con su PROPIA sesion. Una consulta
bloqueante en el bucle congela el latido del WebSocket de todas las pantallas
del salon; y la sesion del pedido ya esta cerrada cuando la tarea corre.
"""
from __future__ import annotations

import asyncio
import logging
import time

registro = logging.getLogger("tinker.prefetch")

# Cuantas tandas se preparan a la vez. Cuando doscientas personas eligen en la
# misma canción, no se abren doscientas conexiones al proveedor.
TOPE_SIMULTANEO = 8

# Lo que un encargo espera un lugar en la cola. Si no lo consigue, se retira y
# su tanda la arma el respaldo determinista: preferir una tanda determinista a
# una buena que llega tarde es la decision que atraviesa todo el diseño.
TOPE_ESPERA_COLA = 2.0


class Prefetch:
    """Se maneja desde CUALQUIER hilo, y esa es la parte delicada."""

    def __init__(self) -> None:
        self._bucle: asyncio.AbstractEventLoop | None = None
        self._tareas: dict[tuple[int, int], asyncio.Task] = {}
        self._semaforo: asyncio.Semaphore | None = None

    def enlazar(self, bucle: asyncio.AbstractEventLoop) -> None:
        """Lo llama el lifespan de main.py, una sola vez."""
        self._bucle = bucle
        self._semaforo = asyncio.Semaphore(TOPE_SIMULTANEO)

    @property
    def activo(self) -> bool:
        return self._bucle is not None

    def encargar(self, evento_id: int, participante_id: int, ronda: int) -> None:
        """Seguro desde el bucle Y desde el threadpool. No espera ni levanta."""
        if self._bucle is None:
            return
        self._bucle.call_soon_threadsafe(self._nacer, evento_id, participante_id, ronda)

    def _nacer(self, evento_id: int, participante_id: int, ronda: int) -> None:
        # Corre DENTRO del bucle: aca `create_task` si tiene donde nacer.
        clave = (participante_id, ronda)
        vieja = self._tareas.get(clave)
        if vieja is not None and not vieja.done():
            return       # dos toques, dos pestañas o dos recargas: una sola llamada
        self._tareas[clave] = asyncio.create_task(
            self._preparar(clave, evento_id, participante_id, ronda)
        )

    async def _preparar(self, clave, evento_id: int, participante_id: int, ronda: int) -> None:
        from app import recomendador

        try:
            arranque = time.monotonic()
            try:
                await asyncio.wait_for(self._semaforo.acquire(), TOPE_ESPERA_COLA)
            except asyncio.TimeoutError:
                registro.info("cola llena: la ronda %s la arma el respaldo", ronda)
                return
            # Lo que costo la cola sale del presupuesto del modelo, no se suma
            # encima. El docstring de modelo.py lo prometia desde el principio;
            # hasta hoy no se cumplia, y por eso el techo real eran diez
            # segundos contra una paciencia de seis.
            try:
                await recomendador.preparar_con_modelo(
                    evento_id, participante_id, ronda,
                    espera_cola=time.monotonic() - arranque,
                )
            finally:
                self._semaforo.release()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            # Que el prefetch se caiga no puede dejar a nadie sin tanda: el poll
            # con fecha limite arma la determinista.
            registro.exception("prefetch ronda %s de participante %s", ronda, participante_id)
        finally:
            self._tareas.pop(clave, None)

    async def apagar(self) -> None:
        for tarea in list(self._tareas.values()):
            tarea.cancel()
        if self._tareas:
            await asyncio.gather(*self._tareas.values(), return_exceptions=True)
        self._tareas.clear()


prefetch = Prefetch()
