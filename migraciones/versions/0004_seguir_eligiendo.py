"""seguir eligiendo: rondas extra por telefono

Quien termino sus cinco y quiere seguir, sigue. Las rondas extra son POR
PARTICIPANTE y no del evento: subir `eventos.rondas` a mitad de fiesta le
cambiaria el juego a todos, incluida la gente que ya termino.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-12
"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "participantes",
        sa.Column("rondas_extra", sa.SmallInteger, nullable=False, server_default="0"),
    )
    op.create_check_constraint(
        "ck_participantes_extra", "participantes", "rondas_extra between 0 and 45"
    )


def downgrade() -> None:
    op.drop_constraint("ck_participantes_extra", "participantes", type_="check")
    op.drop_column("participantes", "rondas_extra")
