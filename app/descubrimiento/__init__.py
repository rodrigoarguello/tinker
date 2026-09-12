"""Descubrir canciones que tinker todavia no conoce.

    resolver (app/catalogo)  -> sé qué canción busco y quiero su identidad
    descubrir (acá)          -> quiero encontrar canciones que no conozco

Nunca se mezclan. `importar.py` es lo unico que escribe en la base.
"""
from app.descubrimiento.apple import AppleMusic
from app.descubrimiento.base import CancionDescubierta, ProveedorDescubrimiento
from app.descubrimiento.deezer import Deezer

# El registro de proveedores. Agregar una fuente es un archivo y una linea acá.
PROVEEDORES = {
    "apple": AppleMusic,
    "deezer": Deezer,
}

__all__ = ["AppleMusic", "CancionDescubierta", "Deezer", "PROVEEDORES", "ProveedorDescubrimiento"]
