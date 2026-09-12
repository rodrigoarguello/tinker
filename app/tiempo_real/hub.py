"""El canal en vivo entre los telefonos y el televisor.

Una sala por evento, en la memoria de ESTE proceso. Es lo que permite que la
pantalla no consulte cada diez segundos: cuando alguien pide una canción, el
servidor la empuja y la tarjeta aparece.

El dia que haga falta mas de un worker, esto es lo unico que cambia -- pasa a
Redis pub/sub y el resto de la aplicacion ni se entera. Por eso vive aislado.
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict

from fastapi import WebSocket

registro = logging.getLogger("tinker.vivo")


class Hub:
    def __init__(self) -> None:
        self._salas: dict[str, set[WebSocket]] = defaultdict(set)
        self._candado = asyncio.Lock()

    async def entrar(self, slug: str, socket: WebSocket) -> None:
        async with self._candado:
            self._salas[slug].add(socket)

    async def salir(self, slug: str, socket: WebSocket) -> None:
        async with self._candado:
            self._salas[slug].discard(socket)
            if not self._salas[slug]:
                del self._salas[slug]

    def conectados(self, slug: str) -> int:
        return len(self._salas.get(slug, ()))

    async def publicar(self, slug: str, mensaje: dict) -> None:
        """Manda a todas las pantallas del evento. Nunca levanta.

        Un socket que murio sin avisar no puede tumbar el pedido HTTP que
        acaba de guardar una solicitud: la canción YA esta en la base, y que
        un televisor se haya ido no es un error de quien la pidio.
        """
        async with self._candado:
            destinos = list(self._salas.get(slug, ()))
        muertos = []
        for socket in destinos:
            try:
                await socket.send_json(mensaje)
            except Exception:  # noqa: BLE001 — cualquier fallo es "se fue"
                muertos.append(socket)
        for socket in muertos:
            await self.salir(slug, socket)
        if muertos:
            registro.info("se soltaron %d pantallas de %s", len(muertos), slug)


hub = Hub()
