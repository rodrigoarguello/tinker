"""Apple Music RSS: lo mas escuchado POR PAIS. Sin credenciales.

Es la unica fuente gratuita que da tendencias de **Paraguay** de verdad, y por
eso es la que sostiene el pool paraguayo y el «que suena hoy acá».

    https://rss.marketingtools.apple.com/api/v2/py/music/most-played/50/songs.json

Ojo con el dominio: `rss.applemarketingtools.com` --el que aparece en medio
internet-- contesta 301 y muere. El bueno es `rss.marketingtools.apple.com`.
"""
from __future__ import annotations

import logging
from datetime import date

import httpx

from app.descubrimiento.base import CancionDescubierta, ProveedorDescubrimiento

registro = logging.getLogger("tinker.apple")

RSS = "https://rss.marketingtools.apple.com/api/v2/{pais}/music/most-played/{cuantas}/songs.json"

# Lo que dice Apple -> lo que entiende tinker. Lo que no esta, queda sin
# genero y lo etiqueta despues el job de scoring.
GENEROS = {
    "Música latina": "reggaeton",
    "Latin": "reggaeton",
    "Pop latino": "pop",
    "Rock": "rock",
    "Rock latino": "rock",
    "Pop": "pop",
    "Hip-Hop/Rap": "urbano",
    "Urbano latino": "urbano",
    "Electrónica": "electronica",
    "Dance": "electronica",
    "Regional mexicano": "ranchera",
    "Tropical": "tropical",
    "Brasileña": "brasilera",
    "Sertanejo": "brasilera",
    "Alternativa": "alternativo",
}


def _fecha_de(crudo: str | None) -> date | None:
    """`"2026-08-07"` -> `date`. None si Apple no la trae o viene rara.

    Apple SI devuelve `releaseDate` en cada fila del feed; el codigo la
    descartaba y guardaba la fecha de descarga en su lugar.
    """
    if not crudo:
        return None
    try:
        return date.fromisoformat(crudo[:10])
    except ValueError:
        return None


class AppleMusic(ProveedorDescubrimiento):
    nombre = "apple"

    def __init__(self, espera: float = 10.0) -> None:
        self.espera = espera

    async def buscar_nuevas(self, pais: str = "py", limite: int = 50, **_) -> list[CancionDescubierta]:
        url = RSS.format(pais=pais, cuantas=limite)
        try:
            async with httpx.AsyncClient(timeout=self.espera, follow_redirects=True) as cliente:
                respuesta = await cliente.get(url)
                respuesta.raise_for_status()
                feed = respuesta.json().get("feed", {})
        except Exception as error:  # noqa: BLE001
            registro.warning("apple %s fallo: %s", pais, error)
            return []

        salida = []
        for posicion, fila in enumerate(feed.get("results", []), start=1):
            generos = [g.get("name") for g in fila.get("genres", []) if g.get("name")]
            traducido = next((GENEROS[g] for g in generos if g in GENEROS), None)
            salida.append(
                CancionDescubierta(
                    titulo=fila.get("name", ""),
                    artista=fila.get("artistName", ""),
                    fuente=f"{self.nombre}:{pais}",
                    ranking=posicion,
                    pais=pais,
                    genero_estimado=traducido,
                    url_origen=fila.get("url"),
                    imagen=fila.get("artworkUrl100"),
                    # `releaseDate` DEL FEED, no `date.today()`. Con la fecha de
                    # hoy toda canción de Apple quedaba con año 2026: la epoca
                    # salia mal y la transicion a CLASSIC --que pide veinte años
                    # de antigüedad-- era imposible por construccion.
                    fecha=_fecha_de(fila.get("releaseDate")),
                    # El primero del chart vale 1.0 y el ultimo casi nada: es
                    # una posicion, no un puntaje, y asi se vuelve comparable
                    # con la popularidad de otras fuentes.
                    popularidad=round(max(0.0, 1 - (posicion - 1) / max(limite, 1)), 3),
                    extra={"chart": f"most-played-{pais}"},
                )
            )
        return salida
