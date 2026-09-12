"""Motor, sesion y Base. Nada de modelos aca: van en app/modelos.py."""
from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.ajustes import ajustes


class Base(DeclarativeBase):
    pass


# pool_pre_ping: la base puede haberse reiniciado debajo nuestro (backup,
# actualizacion de imagen). Sin esto, el primer pedido despues del reinicio
# falla con una conexion muerta que el pool creia buena.
motor = create_engine(ajustes().bd, pool_pre_ping=True, pool_size=5, max_overflow=5)

Sesion = sessionmaker(bind=motor, expire_on_commit=False)


def con_sesion() -> Iterator[Session]:
    """Dependencia de FastAPI. Una sesion por pedido, siempre cerrada."""
    sesion = Sesion()
    try:
        yield sesion
    finally:
        sesion.close()
