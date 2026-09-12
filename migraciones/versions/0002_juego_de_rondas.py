"""el juego de las cinco rondas

El telefono deja de ser un buscador y pasa a ser un juego: cinco tandas de
cinco canciones, una eleccion por tanda. Esta migracion trae lo que ese cambio
necesita y NO borra nada del mundo viejo -- las solicitudes cargadas con el
buscador quedan con `ronda = 0` y siguen contando en el ranking.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-12
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── eventos ──────────────────────────────────────────────────────────
    #
    # El nombre viejo pasaria a mentir: ya no es un techo de canciones sueltas,
    # es cuantas veces juega cada persona. Y las filas existentes valen 10, que
    # como rondas seria un juego larguisimo: se bajan a 5.
    op.alter_column("eventos", "max_por_participante", new_column_name="rondas")
    op.alter_column("eventos", "rondas", server_default="5")
    op.execute("UPDATE eventos SET rondas = 5")
    op.drop_constraint("ck_eventos_max", "eventos", type_="check")
    op.create_check_constraint("ck_eventos_rondas", "eventos", "rondas between 1 and 10")

    op.add_column("eventos", sa.Column("tope_ia", sa.Integer, nullable=False, server_default="1500"))
    op.add_column("eventos", sa.Column("notas_repertorio", sa.Text))
    op.create_check_constraint("ck_eventos_tope_ia", "eventos", "tope_ia >= 0")

    # ── canciones ────────────────────────────────────────────────────────
    op.add_column("canciones", sa.Column("spotify_id", sa.String(40)))

    # ── solicitudes ──────────────────────────────────────────────────────
    op.add_column(
        "solicitudes",
        sa.Column("ronda", sa.SmallInteger, nullable=False, server_default="0"),
    )
    op.create_check_constraint("ck_solicitudes_ronda", "solicitudes", "ronda between 0 and 10")
    # Una eleccion por ronda. Parcial: las filas del buscador viejo tienen
    # ronda = 0 y de esas puede haber muchas por participante.
    op.create_index(
        "uq_solicitud_ronda",
        "solicitudes",
        ["evento_id", "participante_id", "ronda"],
        unique=True,
        postgresql_where=sa.text("ronda > 0"),
    )
    op.create_index("ix_solicitudes_mias", "solicitudes", ["evento_id", "participante_id", "ronda"])

    # ── repertorio ───────────────────────────────────────────────────────
    op.create_table(
        "repertorio",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("evento_id", sa.Integer, sa.ForeignKey("eventos.id", ondelete="CASCADE"), nullable=False),
        sa.Column("cancion_id", sa.Integer, sa.ForeignKey("canciones.id", ondelete="CASCADE"), nullable=False),
        sa.Column("orden", sa.Integer, nullable=False),
        sa.Column("linea", sa.Text, nullable=False),
        sa.Column("artista_clave", sa.String(120), nullable=False),
        sa.Column("genero", sa.String(20)),
        sa.Column("epoca", sa.String(4)),
        sa.Column("idioma", sa.String(4)),
        sa.Column("intensidad", sa.SmallInteger),
        sa.Column("creado", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("evento_id", "cancion_id", name="uq_repertorio"),
        sa.CheckConstraint(
            "intensidad is null or intensidad between 1 and 5",
            name="ck_repertorio_intensidad",
        ),
    )
    op.create_index("ix_repertorio_pozo", "repertorio", ["evento_id", "genero"])
    op.create_index("ix_repertorio_artista", "repertorio", ["evento_id", "artista_clave"])

    # ── tandas ───────────────────────────────────────────────────────────
    op.create_table(
        "tandas",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("evento_id", sa.Integer, sa.ForeignKey("eventos.id", ondelete="CASCADE"), nullable=False),
        sa.Column("participante_id", sa.Integer, sa.ForeignKey("participantes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ronda", sa.SmallInteger, nullable=False),
        sa.Column("origen", sa.String(10), nullable=False),
        sa.Column("proveedor", sa.String(10)),
        sa.Column("modelo", sa.String(60)),
        sa.Column("tokens_entrada", sa.Integer, nullable=False, server_default="0"),
        sa.Column("tokens_salida", sa.Integer, nullable=False, server_default="0"),
        sa.Column("ms", sa.Integer),
        sa.Column("porque", sa.Text),
        sa.Column("creada", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("ronda between 1 and 10", name="ck_tandas_ronda"),
        sa.CheckConstraint("origen in ('semilla', 'modelo', 'respaldo')", name="ck_tandas_origen"),
        sa.UniqueConstraint("participante_id", "ronda", name="uq_tanda_ronda"),
    )
    # El contador de gasto del evento, sin recorrer las tandas que no costaron.
    op.create_index(
        "ix_tandas_costo",
        "tandas",
        ["evento_id"],
        postgresql_where=sa.text("origen = 'modelo'"),
    )

    op.create_table(
        "tanda_canciones",
        sa.Column("tanda_id", sa.BigInteger, sa.ForeignKey("tandas.id", ondelete="CASCADE"), nullable=False),
        sa.Column("posicion", sa.SmallInteger, nullable=False),
        sa.Column("cancion_id", sa.Integer, sa.ForeignKey("canciones.id", ondelete="CASCADE"), nullable=False),
        sa.PrimaryKeyConstraint("tanda_id", "posicion"),
        sa.UniqueConstraint("tanda_id", "cancion_id", name="uq_tanda_sin_repetidas"),
        sa.CheckConstraint("posicion between 1 and 5", name="ck_tanda_posicion"),
    )


def downgrade() -> None:
    op.drop_table("tanda_canciones")
    op.drop_index("ix_tandas_costo", table_name="tandas")
    op.drop_table("tandas")
    op.drop_index("ix_repertorio_artista", table_name="repertorio")
    op.drop_index("ix_repertorio_pozo", table_name="repertorio")
    op.drop_table("repertorio")

    op.drop_index("ix_solicitudes_mias", table_name="solicitudes")
    op.drop_index("uq_solicitud_ronda", table_name="solicitudes")
    op.drop_constraint("ck_solicitudes_ronda", "solicitudes", type_="check")
    op.drop_column("solicitudes", "ronda")

    op.drop_column("canciones", "spotify_id")

    op.drop_constraint("ck_eventos_tope_ia", "eventos", type_="check")
    op.drop_column("eventos", "notas_repertorio")
    op.drop_column("eventos", "tope_ia")
    op.drop_constraint("ck_eventos_rondas", "eventos", type_="check")
    op.create_check_constraint(
        "ck_eventos_max", "eventos", "max_por_participante between 1 and 50"
    )
    op.alter_column("eventos", "rondas", server_default="10")
    op.alter_column("eventos", "rondas", new_column_name="max_por_participante")
