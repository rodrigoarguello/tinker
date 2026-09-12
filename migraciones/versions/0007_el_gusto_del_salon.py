"""el gusto del salon: perfil ponderado, señales que llegan a la tanda

Tres cosas, y las tres existen por el mismo motivo: el motor razonaba sobre
etiquetas que en produccion estaban vacias o no llegaban hasta la tanda.

  · `canciones` gana popularidad, año y explicito. Van ACA y no en
    `catalogo_musical` a proposito: el pozo de una tanda se arma de
    `repertorio ⨝ canciones` y no toca el catalogo global, asi que una señal
    puesta alla se queda en el panel y no cambia una sola tarjeta. Los tres
    datos ya llegaban en cada respuesta de Spotify y se tiraban.

  · `evento_perfil` gana el perfil PONDERADO --lo reciente pesa mas, lo que
    varios eligieron pesa mas-- junto a energia, mainstream y la confianza que
    merece con la gente que hay. El perfil plano de antes se conserva: sigue
    siendo lo que mira el panel.

  · `tandas` gana el mensaje que se le mostro a la persona y la lectura que el
    modelo hizo del salon en ese momento. Sin esto, «que entendio el agente a
    las once y media» no se puede reconstruir despues.

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-12
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── las señales que ya llegaban y se descartaban ──────────────────────
    op.add_column("canciones", sa.Column("popularidad", sa.Float, nullable=True))
    op.add_column("canciones", sa.Column("anio", sa.SmallInteger, nullable=True))
    op.add_column("canciones", sa.Column("explicito", sa.Boolean, nullable=True))
    op.create_check_constraint(
        "ck_cancion_popularidad", "canciones", "popularidad is null or popularidad between 0 and 1"
    )

    # ── el perfil del salon, ponderado ────────────────────────────────────
    op.add_column(
        "evento_perfil",
        sa.Column("ponderado", postgresql.JSONB, nullable=False, server_default="{}"),
    )
    op.add_column("evento_perfil", sa.Column("energia", sa.Float, nullable=True))
    op.add_column("evento_perfil", sa.Column("mainstream", sa.Float, nullable=True))
    op.add_column(
        "evento_perfil",
        sa.Column("confianza", sa.Float, nullable=False, server_default="0"),
    )
    op.add_column(
        "evento_perfil", sa.Column("calculado", sa.DateTime(timezone=True), nullable=True)
    )

    # ── que dijo el agente, y que leyo ────────────────────────────────────
    op.add_column("tandas", sa.Column("mensaje", sa.String(160), nullable=True))
    op.add_column("tandas", sa.Column("lectura", postgresql.JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("tandas", "lectura")
    op.drop_column("tandas", "mensaje")
    op.drop_column("evento_perfil", "calculado")
    op.drop_column("evento_perfil", "confianza")
    op.drop_column("evento_perfil", "mainstream")
    op.drop_column("evento_perfil", "energia")
    op.drop_column("evento_perfil", "ponderado")
    op.drop_constraint("ck_cancion_popularidad", "canciones", type_="check")
    op.drop_column("canciones", "explicito")
    op.drop_column("canciones", "anio")
    op.drop_column("canciones", "popularidad")
