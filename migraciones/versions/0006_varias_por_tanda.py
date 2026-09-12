"""varias elecciones por tanda, y la ronda la maneja la persona

Hasta acá el juego era: cinco tarjetas, se toca UNA, aparecen otras cinco. En
una fiesta eso pierde canciones -- de cinco tarjetas te pueden gustar tres, y
elegir una hacia desaparecer las otras dos.

Ahora se puede marcar cuantas se quiera de cada tanda, y la tanda cambia
cuando la persona lo pide. Dos cambios de forma:

  · se cae el unique parcial de «una eleccion por ronda»;
  · la ronda en curso deja de derivarse de las elecciones y pasa a ser un dato
    del participante -- porque ya no hay una eleccion por ronda de la cual
    derivarla.

Lo que NO cambia: `uq_solicitud (evento, participante, cancion)` sigue
impidiendo que el mismo telefono vote dos veces la misma canción.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-12
"""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("uq_solicitud_ronda", table_name="solicitudes")
    # El indice comun se queda: `_mis_elegidas` lo usa en cada pedido.
    op.add_column(
        "participantes",
        sa.Column("ronda_actual", sa.SmallInteger, nullable=False, server_default="1"),
    )
    op.create_check_constraint(
        "ck_participantes_ronda", "participantes", "ronda_actual between 1 and 50"
    )
    # Las sesiones que ya venian jugando arrancan en la ronda que les toca.
    op.execute(
        """
        UPDATE participantes p
           SET ronda_actual = LEAST(50, GREATEST(1, (
                 SELECT COALESCE(MAX(s.ronda), 0) + 1
                   FROM solicitudes s
                  WHERE s.participante_id = p.id AND s.ronda > 0
               )))
        """
    )


def downgrade() -> None:
    op.drop_constraint("ck_participantes_ronda", "participantes", type_="check")
    op.drop_column("participantes", "ronda_actual")
    op.create_index(
        "uq_solicitud_ronda",
        "solicitudes",
        ["evento_id", "participante_id", "ronda"],
        unique=True,
        postgresql_where=sa.text("ronda > 0"),
    )
