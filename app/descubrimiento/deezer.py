"""Deezer: charts, generos y el top de un artista. SIN credenciales.

Es la fuente que permite que el descubrimiento funcione hoy, sin que nadie
tenga que dar de alta una app en ningun lado. Su API publica responde a
`api.deezer.com` sin token, con un limite de uso razonable para lo que
hacemos: unas pocas decenas de pedidos por hora.

No reemplaza a Spotify --Deezer no da `spotify_id` y la playlist final es de
Spotify-- pero para DESCUBRIR que canciones existen y cuales suenan, alcanza y
sobra.
"""
from __future__ import annotations

import asyncio
import logging

import httpx

from app.descubrimiento.base import CancionDescubierta, ProveedorDescubrimiento

registro = logging.getLogger("tinker.deezer")

API = "https://api.deezer.com"

# Los generos de Deezer que nos interesan, con el nombre que usa tinker.
# El id es de ellos; el nombre, nuestro.
GENEROS = {
    132: "pop",
    152: "rock",
    116: "urbano",       # Rap/Hip Hop
    122: "reggaeton",
    113: "electronica",
    85: "alternativo",
    80: "brasilera",     # Sertanejo
    79: "brasilera",     # Samba/Pagode
    76: "tropical",      # Axé/Forró
    144: "tropical",     # Reggae
    165: "romantica",    # R&B
}

# Una consulta por termino para los generos que Deezer no tiene como tal.
BUSQUEDAS = {
    "cumbia": ["cumbia", "cumbia paraguaya", "cumbia villera"],
    "paraguaya": ["polka paraguaya", "guarania", "musica paraguaya"],
    "rock": ["rock latino", "rock nacional"],
    "ranchera": ["ranchera", "mariachi"],
    "tropical": ["salsa", "merengue", "bachata"],
    "clasicos": ["rock clasico", "80s hits"],
}


class Deezer(ProveedorDescubrimiento):
    nombre = "deezer"

    def __init__(self, espera: float = 8.0) -> None:
        self.espera = espera

    async def _pedir(self, ruta: str, **params) -> dict:
        async with httpx.AsyncClient(timeout=self.espera) as cliente:
            respuesta = await cliente.get(f"{API}{ruta}", params=params)
            respuesta.raise_for_status()
            datos = respuesta.json()
        # Deezer contesta 200 con un objeto `error` adentro. Tratarlo como
        # exito es la forma de terminar con listas vacias sin saber por que.
        if isinstance(datos, dict) and datos.get("error"):
            raise RuntimeError(str(datos["error"])[:200])
        return datos

    def _pista(self, item: dict, genero: str | None, ranking: int | None,
               chart: str | None = None) -> CancionDescubierta:
        artista = (item.get("artist") or {}).get("name") or "—"
        album = item.get("album") or {}
        return CancionDescubierta(
            titulo=item.get("title_short") or item.get("title") or "",
            artista=artista,
            fuente=self.nombre,
            ranking=ranking,
            genero_estimado=genero,
            url_origen=item.get("link"),
            imagen=album.get("cover_medium") or album.get("cover"),
            popularidad=min(1.0, (item.get("rank") or 0) / 1_000_000),
            extra={"deezer_id": item.get("id"), **({"chart": chart} if chart else {})},
        )

    # ── el top de un grupo: lo que pide «pasame los temas de estos grupos» ──

    async def top_del_artista(self, artista: str, limite: int = 10) -> list[CancionDescubierta]:
        try:
            encontrados = await self._pedir("/search/artist", q=artista, limit=5)
        except Exception as error:  # noqa: BLE001
            registro.warning("deezer: no pude buscar %s: %s", artista, error)
            return []

        from app.catalogo import clave_artista

        buscado = clave_artista(artista)
        candidatos = encontrados.get("data", []) or []

        # POR SEGUIDORES Y NO POR ORDEN DE RELEVANCIA, y esto costó tres grupos:
        # con nombres comunes --"Muse", "Ghost", "Royal Blood"-- Deezer devuelve
        # PRIMERO un homonimo con cero temas, y el primero que coincidia por
        # nombre se quedaba con el lugar. El de mas fans es el que la gente
        # quiso nombrar.
        exactos = [c for c in candidatos if clave_artista(c.get("name", "")) == buscado]
        orden = sorted(
            exactos or candidatos, key=lambda c: c.get("nb_fan", 0) or 0, reverse=True
        )
        if not orden:
            registro.info("deezer: sin artista para %r", artista)
            return []

        # Y se prueba con el siguiente si el elegido no tiene temas: un
        # homonimo con muchos fans y ningun tema tambien existe.
        for candidato in orden[:3]:
            try:
                top = await self._pedir(f"/artist/{candidato['id']}/top", limit=limite)
            except Exception as error:  # noqa: BLE001
                registro.warning("deezer: sin top de %s: %s", artista, error)
                continue
            temas = top.get("data", []) or []
            if temas:
                return [
                    self._pista(item, None, posicion)
                    for posicion, item in enumerate(temas, start=1)
                ]
        registro.info("deezer: %r no tiene temas en ningun candidato", artista)
        return []

    # ── charts y generos ──────────────────────────────────────────────────

    async def buscar_nuevas(self, genero_id: int | None = None, limite: int = 50, **_) -> list[CancionDescubierta]:
        ruta = f"/chart/{genero_id or 0}/tracks"
        try:
            datos = await self._pedir(ruta, limit=limite)
        except Exception as error:  # noqa: BLE001
            registro.warning("deezer: chart %s fallo: %s", genero_id, error)
            return []
        genero = GENEROS.get(genero_id or 0)
        return [
            self._pista(item, genero, posicion, chart=f"deezer-{genero_id or 0}")
            for posicion, item in enumerate(datos.get("data", []), start=1)
        ]

    async def por_busqueda(self, termino: str, genero: str, limite: int = 25) -> list[CancionDescubierta]:
        """Para los generos que Deezer no tiene como categoria: cumbia
        paraguaya, guarania, polka. Son justo los que mas importan acá."""
        try:
            datos = await self._pedir("/search", q=termino, limit=limite, order="RANKING")
        except Exception as error:  # noqa: BLE001
            registro.warning("deezer: busqueda %r fallo: %s", termino, error)
            return []
        return [
            self._pista(item, genero, posicion)
            for posicion, item in enumerate(datos.get("data", []), start=1)
        ]

    async def por_generos(self, limite_por_genero: int = 30) -> list[CancionDescubierta]:
        """Un barrido por todos los generos, en serie y con pausa.

        En serie a proposito: son treinta pedidos a una API gratuita y sin
        clave. Hacerlos todos juntos es la forma mas rapida de que nos corten.
        """
        salida: list[CancionDescubierta] = []
        for genero_id in GENEROS:
            salida += await self.buscar_nuevas(genero_id=genero_id, limite=limite_por_genero)
            await asyncio.sleep(0.3)
        for genero, terminos in BUSQUEDAS.items():
            for termino in terminos:
                salida += await self.por_busqueda(termino, genero, limite=20)
                await asyncio.sleep(0.3)
        return salida
