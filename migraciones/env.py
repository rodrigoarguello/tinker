"""Alembic. La URL sale del entorno, nunca del .ini.

Una cadena de conexion con clave adentro de un archivo versionado es la fuga
mas facil de cometer y la mas dificil de sacar despues del historial de git.
"""
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.ajustes import ajustes
from app.bd import Base
import app.modelos  # noqa: F401  — registra TODAS las tablas en Base.metadata

config = context.config
config.set_main_option("sqlalchemy.url", ajustes().bd)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

metadatos = Base.metadata


def sin_conexion() -> None:
    context.configure(
        url=ajustes().bd,
        target_metadata=metadatos,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def con_conexion() -> None:
    motor = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with motor.connect() as conexion:
        context.configure(connection=conexion, target_metadata=metadatos, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    sin_conexion()
else:
    con_conexion()
