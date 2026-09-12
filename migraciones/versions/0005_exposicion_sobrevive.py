"""la exposicion sobrevive a la tanda que la mostro

«Ninguna me gusta» rehace la tanda, y rehacer borra su fila. Con el borrado en
cascada, las exposiciones se iban con ella y las cinco canciones rechazadas
volvian a aparecer como si nunca se hubieran mostrado -- que es exactamente lo
que la persona pidio que no pasara.

La exposicion es el registro historico de que algo se le mostro a alguien. La
tanda es la unidad de servicio que lo mostro. Que la segunda desaparezca no
puede borrar la primera.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-12
"""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("exposiciones", "tanda_id", nullable=True)
    op.drop_constraint("exposiciones_tanda_id_fkey", "exposiciones", type_="foreignkey")
    op.create_foreign_key(
        "exposiciones_tanda_id_fkey", "exposiciones", "tandas",
        ["tanda_id"], ["id"], ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("exposiciones_tanda_id_fkey", "exposiciones", type_="foreignkey")
    op.execute("DELETE FROM exposiciones WHERE tanda_id IS NULL")
    op.create_foreign_key(
        "exposiciones_tanda_id_fkey", "exposiciones", "tandas",
        ["tanda_id"], ["id"], ondelete="CASCADE",
    )
    op.alter_column("exposiciones", "tanda_id", nullable=False)
