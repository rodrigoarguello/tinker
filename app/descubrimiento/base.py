"""Descubrir NO es resolver, y el dia que se mezclen empieza el problema.

    resolver    → sé qué canción busco y quiero su identidad  (app/catalogo)
    descubrir   → quiero encontrar canciones que todavía no conozco  (acá)

Son dos responsabilidades distintas: una convierte texto en identidad, la otra
sale a buscar repertorio. Un proveedor de descubrimiento puede devolver
cuarenta canciones que nadie pidio; uno de resolucion devuelve exactamente la
que se le nombro, o nada.

Todos los proveedores devuelven lo mismo --`CancionDescubierta`-- y ninguno
escribe en la base. Eso lo hace `importar.py`, que es el unico que sabe de
tablas. Asi, agregar una fuente nueva es un archivo y una linea en el registro.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

registro = logging.getLogger("tinker.descubrimiento")


@dataclass(frozen=True)
class CancionDescubierta:
    """Lo que cualquier fuente sabe decir de una canción.

    Todo es opcional menos el titulo y el artista: una fuente que solo sabe
    nombres sigue siendo util, y obligarla a inventar el resto seria peor.
    """

    titulo: str
    artista: str
    fuente: str
    ranking: int | None = None          # posicion en el chart de origen, si lo hay
    pais: str | None = None
    genero_estimado: str | None = None
    url_origen: str | None = None
    fecha: date | None = None
    anio: int | None = None
    imagen: str | None = None
    popularidad: float | None = None     # 0..1, normalizada por el proveedor
    extra: dict = field(default_factory=dict)


class ProveedorDescubrimiento:
    """La interfaz. Un proveedor nuevo implementa `buscar_nuevas` y se registra.

    NINGUN metodo puede levantar hacia arriba: una fuente caida no puede
    romper tinker ni frenar a las demas. Lo que devuelve una fuente que fallo
    es una lista vacia, y el estado queda anotado en `fuentes_estado`.
    """

    nombre: str = "?"

    async def buscar_nuevas(self, **opciones) -> list[CancionDescubierta]:
        raise NotImplementedError

    async def top_del_artista(self, artista: str, limite: int = 10) -> list[CancionDescubierta]:
        """Los temas mas conocidos de un grupo. No todos los proveedores
        pueden: el que no, devuelve vacio y se pasa al siguiente."""
        return []
