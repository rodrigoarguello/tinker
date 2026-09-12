"""esquema inicial

Revision ID: 0001
Revises:
Create Date: 2026-09-12
"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "eventos",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("slug", sa.String(60), nullable=False, unique=True),
        sa.Column("nombre", sa.String(160), nullable=False),
        sa.Column("lugar", sa.String(160)),
        sa.Column("fecha", sa.Date),
        sa.Column("estado", sa.String(10), nullable=False, server_default="previo"),
        sa.Column("max_por_participante", sa.Integer, nullable=False, server_default="10"),
        sa.Column("playlist_url", sa.Text),
        sa.Column("creado", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("estado in ('previo', 'en_vivo', 'cerrado')", name="ck_eventos_estado"),
        sa.CheckConstraint("max_por_participante between 1 and 50", name="ck_eventos_max"),
    )

    op.create_table(
        "participantes",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("evento_id", sa.Integer, sa.ForeignKey("eventos.id", ondelete="CASCADE"), nullable=False),
        sa.Column("dispositivo", sa.String(64), nullable=False),
        sa.Column("creado", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("evento_id", "dispositivo", name="uq_participante_dispositivo"),
    )

    op.create_table(
        "canciones",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("proveedor", sa.String(20), nullable=False),
        sa.Column("proveedor_id", sa.String(120), nullable=False),
        sa.Column("titulo", sa.String(300), nullable=False),
        sa.Column("artista", sa.String(300), nullable=False),
        sa.Column("album", sa.String(300)),
        sa.Column("imagen", sa.Text),
        sa.Column("duracion_ms", sa.Integer),
        sa.UniqueConstraint("proveedor", "proveedor_id", name="uq_cancion_proveedor"),
    )

    op.create_table(
        "solicitudes",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("evento_id", sa.Integer, sa.ForeignKey("eventos.id", ondelete="CASCADE"), nullable=False),
        sa.Column("participante_id", sa.Integer, sa.ForeignKey("participantes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("cancion_id", sa.Integer, sa.ForeignKey("canciones.id", ondelete="CASCADE"), nullable=False),
        sa.Column("creado", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("evento_id", "participante_id", "cancion_id", name="uq_solicitud"),
    )
    op.create_index("ix_solicitudes_ranking", "solicitudes", ["evento_id", "cancion_id"])
    op.create_index("ix_solicitudes_recientes", "solicitudes", ["evento_id", "creado"])

    op.create_table(
        "gustos",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("participante_id", sa.Integer, sa.ForeignKey("participantes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("genero", sa.String(20), nullable=False),
        sa.UniqueConstraint("participante_id", "genero", name="uq_gusto"),
    )


def downgrade() -> None:
    op.drop_table("gustos")
    op.drop_index("ix_solicitudes_recientes", table_name="solicitudes")
    op.drop_index("ix_solicitudes_ranking", table_name="solicitudes")
    op.drop_table("solicitudes")
    op.drop_table("canciones")
    op.drop_table("participantes")
    op.drop_table("eventos")
