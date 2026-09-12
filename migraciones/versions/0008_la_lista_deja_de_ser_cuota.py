"""las bandas del dueño del evento dejan de tener cuota

Habia una regla dura: tres de cada cinco tarjetas TENIAN que salir de la lista
de grupos del evento. Funcionaba mientras el motor no sabia nada, y chocaba de
frente con el motor adaptativo -- si el salon converge a cumbia, la cumbia solo
puede mover dos cartas de cinco y el agente no tiene con que adaptarse.

Ahora la lista entra por puntaje, con un bono que se desvanece ronda a ronda
(`reglas.bono_prioritaria`): fuerte mientras no sabemos nada, casi nulo en la
ronda 5. Las bandas dejan de aparecer porque estan obligadas y empiezan a
aparecer porque ganan.

La columna NO se borra ni la regla se desmonta: un evento puede volver a pedir
cuota dura poniendola en uno o mas, y `asegurar_prioritarias` sigue entera y
probada. Lo que cambia es el valor por defecto.

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-12
"""
from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("eventos", "prioritarias_por_tanda", server_default="0")
    op.execute("UPDATE eventos SET prioritarias_por_tanda = 0")


def downgrade() -> None:
    op.alter_column("eventos", "prioritarias_por_tanda", server_default="3")
    op.execute("UPDATE eventos SET prioritarias_por_tanda = 3")
