"""inteligencia musical: catalogo global, exposiciones, spotify y perfil

Siete tablas nuevas y ninguna columna tocada de las que ya estaban. Lo que
existe --tandas, tanda_canciones, solicitudes, repertorio-- no cambia de forma:
este bloque AGREGA capacidades alrededor, no rediseña el juego.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-12
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── el catalogo global ───────────────────────────────────────────────
    #
    # Separado del repertorio a proposito: el repertorio dice que se puede
    # elegir EN ESTE EVENTO; el catalogo dice que canciones conoce tinker y
    # cuanto valen. Una canción descubierta entra aca, no al juego.
    op.create_table(
        "catalogo_musical",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("cancion_id", sa.Integer, sa.ForeignKey("canciones.id", ondelete="CASCADE"),
                  nullable=False, unique=True),
        sa.Column("fuente_principal", sa.String(30), nullable=False),
        sa.Column("pais", sa.String(4)),
        sa.Column("idioma", sa.String(4)),
        sa.Column("genero", sa.String(20)),
        sa.Column("subgenero", sa.String(40)),
        sa.Column("epoca", sa.String(4)),
        sa.Column("anio", sa.SmallInteger),
        sa.Column("intensidad", sa.SmallInteger),
        sa.Column("ambiente", sa.String(20)),
        sa.Column("version", sa.String(10), nullable=False, server_default="ORIGINAL"),
        sa.Column("es_paraguaya", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("es_latina", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("es_brasilera", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("es_clasico", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("es_tendencia", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("popularidad_py", sa.Float, nullable=False, server_default="0"),
        sa.Column("popularidad_global", sa.Float, nullable=False, server_default="0"),
        sa.Column("score_actual", sa.Float, nullable=False, server_default="0"),
        sa.Column("estado", sa.String(14), nullable=False, server_default="NEW"),
        sa.Column("primera_deteccion", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("ultima_deteccion", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("ultima_actualizacion", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "estado in ('NEW','RISING','HOT','STABLE','CLASSIC','LOW_ROTATION','QUARANTINE','ARCHIVED')",
            name="ck_catalogo_estado",
        ),
        sa.CheckConstraint(
            "version in ('ORIGINAL','REMIX','LIVE','ACOUSTIC','COVER','REMASTER','OTHER')",
            name="ck_catalogo_version",
        ),
    )
    # El refill pregunta «cuantas activas hay» en cada vuelta; el dashboard
    # ordena por score. Los dos van por aca.
    op.create_index("ix_catalogo_estado", "catalogo_musical", ["estado", "score_actual"])
    op.create_index("ix_catalogo_genero", "catalogo_musical", ["genero", "estado"])
    op.create_index("ix_catalogo_paraguaya", "catalogo_musical", ["es_paraguaya"],
                    postgresql_where=sa.text("es_paraguaya"))

    # ── exposiciones: lo MOSTRADO, no solo lo elegido ────────────────────
    #
    # Sin esto no hay conversion, no hay fatiga y no hay forma de distinguir
    # una canción que nadie quiere de una que nadie vio.
    op.create_table(
        "exposiciones",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("evento_id", sa.Integer, sa.ForeignKey("eventos.id", ondelete="CASCADE"), nullable=False),
        sa.Column("participante_id", sa.Integer, sa.ForeignKey("participantes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tanda_id", sa.BigInteger, sa.ForeignKey("tandas.id", ondelete="CASCADE"), nullable=False),
        sa.Column("cancion_id", sa.Integer, sa.ForeignKey("canciones.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ronda", sa.SmallInteger, nullable=False),
        sa.Column("posicion", sa.SmallInteger, nullable=False),
        sa.Column("mostrada_en", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("franja", sa.String(12)),
        # pendiente -> elegida | no_elegida | rechazada
        sa.Column("resultado", sa.String(12), nullable=False, server_default="pendiente"),
        sa.Column("resuelta_en", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("tanda_id", "cancion_id", name="uq_exposicion"),
        sa.CheckConstraint(
            "resultado in ('pendiente','elegida','no_elegida','rechazada')",
            name="ck_exposicion_resultado",
        ),
    )
    # La fatiga mira las exposiciones de los ultimos minutos de UN evento.
    op.create_index("ix_exposiciones_fatiga", "exposiciones", ["evento_id", "mostrada_en"])
    # Las metricas por canción agrupan por canción y resultado.
    op.create_index("ix_exposiciones_metricas", "exposiciones", ["cancion_id", "resultado"])
    op.create_index("ix_exposiciones_evento", "exposiciones", ["evento_id", "cancion_id"])

    # ── rechazos explicitos de tanda ─────────────────────────────────────
    op.create_table(
        "rechazos",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("evento_id", sa.Integer, sa.ForeignKey("eventos.id", ondelete="CASCADE"), nullable=False),
        sa.Column("participante_id", sa.Integer, sa.ForeignKey("participantes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tanda_id", sa.BigInteger, sa.ForeignKey("tandas.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ronda", sa.SmallInteger, nullable=False),
        sa.Column("creado", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("tanda_id", name="uq_rechazo_tanda"),
    )
    op.create_index("ix_rechazos_seguidos", "rechazos", ["participante_id", "creado"])

    # ── perfil colectivo del evento ──────────────────────────────────────
    op.create_table(
        "evento_perfil",
        sa.Column("evento_id", sa.Integer, sa.ForeignKey("eventos.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("generos", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("decadas", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("idiomas", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("artistas", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("paises", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("rechazados", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("intensidad_promedio", sa.Float),
        sa.Column("elecciones", sa.Integer, nullable=False, server_default="0"),
        sa.Column("actualizado", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # ── la cola de Spotify ───────────────────────────────────────────────
    #
    # Materializa el reintento que hasta ahora era una promesa en un comentario.
    # Una canción elegida se guarda SIEMPRE; que Spotify la acepte es otra
    # historia y pasa despues, sin que nadie espere.
    op.create_table(
        "spotify_cola",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("evento_id", sa.Integer, sa.ForeignKey("eventos.id", ondelete="CASCADE"), nullable=False),
        sa.Column("cancion_id", sa.Integer, sa.ForeignKey("canciones.id", ondelete="CASCADE"), nullable=False),
        sa.Column("playlist_id", sa.String(40), nullable=False),
        sa.Column("spotify_id", sa.String(40)),
        sa.Column("estado", sa.String(12), nullable=False, server_default="PENDING"),
        sa.Column("intentos", sa.SmallInteger, nullable=False, server_default="0"),
        sa.Column("ultimo_error", sa.Text),
        sa.Column("creado", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("ultimo_intento", sa.DateTime(timezone=True)),
        sa.Column("proximo_intento", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("enviado_en", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "estado in ('PENDING','SENDING','ADDED','DUPLICATE','ERROR','ABANDONED')",
            name="ck_spotify_estado",
        ),
        # UNA canción por playlist. La restriccion esta en la base y no en el
        # codigo: dos personas eligiendo la misma canción a la vez son dos
        # pedidos que llegan juntos, y el codigo no puede arbitrar eso.
        sa.UniqueConstraint("playlist_id", "cancion_id", name="uq_spotify_playlist_cancion"),
    )
    op.create_index("ix_spotify_pendientes", "spotify_cola", ["estado", "proximo_intento"])

    # ── la cuenta conectada ──────────────────────────────────────────────
    #
    # Una sola fila. El refresh token vive aca porque no hay otro lugar: es un
    # secreto de larga vida y la base ya lo es. Se revoca desde la cuenta de
    # Spotify, y borrar la fila alcanza para desconectar.
    op.create_table(
        "spotify_cuenta",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("usuario", sa.String(80)),
        sa.Column("refresh_token", sa.Text, nullable=False),
        sa.Column("scopes", sa.Text),
        sa.Column("conectado", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("ultimo_uso", sa.DateTime(timezone=True)),
    )

    # ── estado de las fuentes de descubrimiento ──────────────────────────
    op.create_table(
        "fuentes_estado",
        sa.Column("nombre", sa.String(40), primary_key=True),
        sa.Column("estado", sa.String(10), nullable=False, server_default="OK"),
        sa.Column("ultima_ejecucion", sa.DateTime(timezone=True)),
        sa.Column("encontradas", sa.Integer, nullable=False, server_default="0"),
        sa.Column("nuevas", sa.Integer, nullable=False, server_default="0"),
        sa.Column("duracion_ms", sa.Integer),
        sa.Column("ultimo_error", sa.Text),
        sa.CheckConstraint("estado in ('OK','DEGRADED','ERROR','DISABLED')", name="ck_fuente_estado"),
    )

    # ── el pool prioritario ──────────────────────────────────────────────
    #
    # Los grupos que el dueño del evento quiere que suenen SI O SI. No es una
    # preferencia del recomendador: es una cuota dura por tanda --tres de cada
    # cinco, configurable-- que se impone en `reglas.imponer` despues de que el
    # modelo opina.
    op.add_column("repertorio", sa.Column("prioritario", sa.Boolean, nullable=False,
                                          server_default=sa.false()))
    op.create_index("ix_repertorio_prioritario", "repertorio", ["evento_id", "prioritario"],
                    postgresql_where=sa.text("prioritario"))

    op.create_table(
        "artistas_prioritarios",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("evento_id", sa.Integer, sa.ForeignKey("eventos.id", ondelete="CASCADE"), nullable=False),
        sa.Column("nombre", sa.String(160), nullable=False),
        sa.Column("artista_clave", sa.String(120), nullable=False),
        sa.Column("nota", sa.Text),
        sa.Column("canciones", sa.Integer, nullable=False, server_default="0"),
        sa.Column("ultima_carga", sa.DateTime(timezone=True)),
        sa.Column("creado", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("evento_id", "artista_clave", name="uq_artista_prioritario"),
    )

    # ── contexto del evento ──────────────────────────────────────────────
    op.add_column("eventos", sa.Column("tipo_evento", sa.String(14), nullable=False,
                                       server_default="FIESTA"))
    op.add_column("eventos", sa.Column("explotar_pct", sa.SmallInteger, nullable=False,
                                       server_default="80"))
    op.add_column("eventos", sa.Column("spotify_modo", sa.String(20)))
    op.add_column("eventos", sa.Column("spotify_votos", sa.SmallInteger))
    op.add_column("eventos", sa.Column("prioritarias_por_tanda", sa.SmallInteger,
                                       nullable=False, server_default="3"))
    op.create_check_constraint(
        "ck_eventos_prioritarias", "eventos", "prioritarias_por_tanda between 0 and 5"
    )
    op.create_check_constraint(
        "ck_eventos_explotar", "eventos", "explotar_pct between 0 and 100"
    )


def downgrade() -> None:
    op.drop_constraint("ck_eventos_prioritarias", "eventos", type_="check")
    op.drop_column("eventos", "prioritarias_por_tanda")
    op.drop_table("artistas_prioritarios")
    op.drop_index("ix_repertorio_prioritario", table_name="repertorio")
    op.drop_column("repertorio", "prioritario")
    op.drop_constraint("ck_eventos_explotar", "eventos", type_="check")
    for columna in ("spotify_votos", "spotify_modo", "explotar_pct", "tipo_evento"):
        op.drop_column("eventos", columna)
    op.drop_table("fuentes_estado")
    op.drop_table("spotify_cuenta")
    op.drop_index("ix_spotify_pendientes", table_name="spotify_cola")
    op.drop_table("spotify_cola")
    op.drop_table("evento_perfil")
    op.drop_index("ix_rechazos_seguidos", table_name="rechazos")
    op.drop_table("rechazos")
    for indice in ("ix_exposiciones_evento", "ix_exposiciones_metricas", "ix_exposiciones_fatiga"):
        op.drop_index(indice, table_name="exposiciones")
    op.drop_table("exposiciones")
    for indice in ("ix_catalogo_paraguaya", "ix_catalogo_genero", "ix_catalogo_estado"):
        op.drop_index(indice, table_name="catalogo_musical")
    op.drop_table("catalogo_musical")
