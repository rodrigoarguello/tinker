"""El gusto del SALON, ponderado. No toca la red; una consulta.

La diferencia entre esto y `EventoPerfil.generos` --el perfil que ya existia--
son dos correcciones, y son las que convierten un contador en un perfil:

  · LO RECIENTE PESA MAS. Vida media de veinte minutos: a los 20' una eleccion
    vale la mitad, a los 40' un cuarto. Una fiesta a las once no es la misma a
    las dos, y un perfil que suma toda la noche por igual describe un promedio
    que no existio en ningun momento. No hay nada que resetear: la ventana se
    corre con el reloj, igual que la fatiga.

  · LO QUE ELIGIERON VARIOS PESA MAS, pero no linealmente. Es `1 + log2(n)`: la
    segunda persona que elige Metallica CONFIRMA; la sexta no aporta seis veces
    mas. Si contara lineal, cinco amigos del mismo gusto se llevarian la noche
    entera y el resto del salon dejaria de existir para el motor.

Y una tercera cosa que no es una correccion sino una honestidad: `confianza`.
Con tres personas, "el gusto del salon" es el gusto de tres. El perfil se
calcula igual, pero dice cuanto vale, y quien lo usa escala con eso.

LO QUE NO ESTA, Y POR QUE. `danceability` no se implementa: no tenemos con que
medirla --el endpoint de audio-features de Spotify esta retirado desde 2024
para toda app nueva-- y una variable inventada ensucia las que si se miden.
`energia` sale de `intensidad`, que es una estimacion declarada del modelo en
el etiquetado en frio, no una medicion. Esta escrito asi en el README.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import config
from app.modelos import Cancion, EventoPerfil, Repertorio, Solicitud

TOPE_RECIENTES = 8
TOPE_ARTISTAS = 30


@dataclass(frozen=True)
class PerfilPublico:
    """Lo que el salon viene mostrando. Todo normalizado 0..1, techo = 1.0."""

    generos: dict[str, float] = field(default_factory=dict)
    decadas: dict[str, float] = field(default_factory=dict)
    idiomas: dict[str, float] = field(default_factory=dict)
    artistas: dict[str, float] = field(default_factory=dict)
    energia: float | None = None        # 0..1, de intensidad 1-5
    mainstream: float | None = None     # 0..1, de la popularidad de Spotify
    recientes: list[dict] = field(default_factory=list)
    frecuencia_artista: dict[str, int] = field(default_factory=dict)
    frecuencia_tema: dict[int, int] = field(default_factory=dict)
    personas: int = 0
    elecciones: int = 0
    franja: str = "APERTURA"
    confianza: float = 0.0

    @property
    def dominantes(self) -> list[str]:
        """Los dos generos que mandan. Las cartas abiertas no salen de aca."""
        return [g for g, _ in sorted(self.generos.items(), key=lambda x: -x[1])[:2]]

    @property
    def secundarios(self) -> list[str]:
        return [g for g, _ in sorted(self.generos.items(), key=lambda x: -x[1])[2:]]

    @property
    def descubrimiento(self) -> float:
        """Cuanta apertura a lo menos conocido viene mostrando el salon.

        Es el complemento del mainstream y no una medicion aparte: sin
        `popularidad` no se sabe, y medio punto es la respuesta honesta.
        """
        return 1.0 - self.mainstream if self.mainstream is not None else 0.5

    def domina(self, genero: str | None) -> bool:
        return bool(genero) and self.generos.get(genero, 0.0) >= config.DOMINANCIA

    def como_json(self) -> dict:
        """Lo que ve la pantalla del salon. Sin ids ni frecuencias internas."""
        return {
            "generos": [
                {"genero": g, "peso": round(p, 3)}
                for g, p in sorted(self.generos.items(), key=lambda x: -x[1])[:6]
            ],
            "decadas": [
                {"decada": d, "peso": round(p, 3)}
                for d, p in sorted(self.decadas.items(), key=lambda x: -x[1])[:4]
            ],
            "artistas": [
                {"artista": a, "peso": round(p, 3)}
                for a, p in sorted(self.artistas.items(), key=lambda x: -x[1])[:5]
            ],
            "energia": round(self.energia, 3) if self.energia is not None else None,
            "mainstream": round(self.mainstream, 3) if self.mainstream is not None else None,
            "descubrimiento": round(self.descubrimiento, 3),
            "personas": self.personas,
            "elecciones": self.elecciones,
            "franja": self.franja,
            "confianza": round(self.confianza, 3),
            "recientes": self.recientes,
        }


VACIO = PerfilPublico()


# ── las dos ponderaciones ────────────────────────────────────────────────

def decaimiento(minutos: float) -> float:
    """Cuanto vale hoy una eleccion de hace `minutos`. 0 min -> 1.0."""
    vida = config.PERFIL_VIDA_MEDIA_MIN or 20.0
    return 0.5 ** (max(0.0, minutos) / vida)


def acuerdo(personas: int) -> float:
    """Cuanto suma que una canción la hayan elegido `personas`. 1 -> 1.0."""
    return 1.0 + math.log2(max(1, personas))


def _sumar(destino: dict, clave, cuanto: float) -> None:
    if clave is None or clave == "":
        return
    destino[clave] = destino.get(clave, 0.0) + cuanto


def _normalizar(crudo: dict) -> dict:
    techo = max(crudo.values()) if crudo else 0.0
    if techo <= 0:
        return {}
    return {k: round(v / techo, 4) for k, v in crudo.items()}


# ── el calculo ───────────────────────────────────────────────────────────

def calcular(sesion: Session, evento_id: int, ahora: datetime | None = None) -> PerfilPublico:
    """El perfil del salon en este instante. Una consulta.

    Se recalcula entero en cada eleccion en vez de mantener incrementos. Son
    cientos de filas agrupadas por indice --milisegundos--, y un contador
    guardado que se desincroniza miente sin avisar, que es el peor modo de
    falla posible para algo que decide lo que suena.
    """
    ahora = ahora or datetime.now(timezone.utc)
    filas = sesion.execute(
        select(
            Solicitud.cancion_id,
            Solicitud.participante_id,
            Solicitud.creado,
            Repertorio.genero,
            Repertorio.epoca,
            Repertorio.idioma,
            Repertorio.intensidad,
            Repertorio.artista_clave,
            Cancion.titulo,
            Cancion.artista,
            Cancion.popularidad,
        )
        .join(Repertorio, Repertorio.cancion_id == Solicitud.cancion_id)
        .join(Cancion, Cancion.id == Solicitud.cancion_id)
        .where(
            Solicitud.evento_id == evento_id,
            Repertorio.evento_id == evento_id,
            Solicitud.ronda > 0,
        )
        .order_by(Solicitud.creado)
    ).all()

    if not filas:
        return PerfilPublico(franja=config.franja(ahora.hour))

    # Cuanta gente eligio cada canción. Va en una pasada previa porque el bono
    # de acuerdo de UNA fila depende de todas las demas.
    por_tema: dict[int, set] = {}
    for f in filas:
        por_tema.setdefault(f.cancion_id, set()).add(f.participante_id)

    generos: dict[str, float] = {}
    decadas: dict[str, float] = {}
    idiomas: dict[str, float] = {}
    artistas: dict[str, float] = {}
    peso_total = 0.0
    energia_suma, energia_peso = 0.0, 0.0
    pop_suma, pop_peso = 0.0, 0.0
    frecuencia_artista: dict[str, int] = {}
    frecuencia_tema: dict[int, int] = {}

    for f in filas:
        creado = f.creado if f.creado.tzinfo else f.creado.replace(tzinfo=timezone.utc)
        minutos = (ahora - creado).total_seconds() / 60.0
        peso = decaimiento(minutos) * acuerdo(len(por_tema[f.cancion_id]))
        peso_total += peso

        _sumar(generos, f.genero, peso)
        _sumar(decadas, f.epoca, peso)
        _sumar(idiomas, f.idioma, peso)
        _sumar(artistas, f.artista_clave, peso)

        if f.intensidad:
            # 1..5 -> 0..1. La escala esta escrita en el etiquetador.
            energia_suma += ((f.intensidad - 1) / 4.0) * peso
            energia_peso += peso
        if f.popularidad is not None:
            pop_suma += float(f.popularidad) * peso
            pop_peso += peso

        frecuencia_artista[f.artista_clave] = frecuencia_artista.get(f.artista_clave, 0) + 1
        frecuencia_tema[f.cancion_id] = frecuencia_tema.get(f.cancion_id, 0) + 1

    personas = len({f.participante_id for f in filas})
    recientes = [
        {"titulo": f.titulo, "artista": f.artista}
        for f in reversed(filas[-TOPE_RECIENTES:])
    ]

    return PerfilPublico(
        generos=_normalizar(generos),
        decadas=_normalizar(decadas),
        idiomas=_normalizar(idiomas),
        artistas=dict(
            sorted(_normalizar(artistas).items(), key=lambda x: -x[1])[:TOPE_ARTISTAS]
        ),
        energia=(energia_suma / energia_peso) if energia_peso else None,
        mainstream=(pop_suma / pop_peso) if pop_peso else None,
        recientes=recientes,
        frecuencia_artista=frecuencia_artista,
        frecuencia_tema=frecuencia_tema,
        personas=personas,
        elecciones=len(filas),
        franja=config.franja(ahora.hour),
        confianza=min(1.0, personas / max(1, config.PERSONAS_PARA_CONFIANZA)),
    )


def guardar(sesion: Session, evento_id: int, perfil: PerfilPublico) -> None:
    """Deja el perfil en `evento_perfil`, sin pisar el plano que mira el panel.

    Los conteos crudos --`generos`, `decadas`, `artistas`-- los sigue escribiendo
    el job cada dos minutos y son los totales de la noche. Esto escribe el
    ponderado, que es otra cosa y se usa para otra cosa.
    """
    fila = sesion.get(EventoPerfil, evento_id)
    if fila is None:
        fila = EventoPerfil(evento_id=evento_id)
        sesion.add(fila)
    fila.ponderado = perfil.como_json()
    fila.energia = perfil.energia
    fila.mainstream = perfil.mainstream
    fila.confianza = perfil.confianza
    fila.calculado = datetime.now(timezone.utc)
    sesion.commit()


def leer(sesion: Session, evento_id: int) -> dict:
    """El ponderado guardado, para la pantalla. Vacio si todavia no hay."""
    fila = sesion.get(EventoPerfil, evento_id)
    return dict(fila.ponderado or {}) if fila is not None else {}


def refrescar(sesion: Session, evento_id: int) -> PerfilPublico:
    """Calcular y guardar, que es lo que hace falta en cada eleccion."""
    perfil = calcular(sesion, evento_id)
    guardar(sesion, evento_id, perfil)
    return perfil
