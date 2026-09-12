# Una sola etapa y ningun Node: las dos paginas son HTML, CSS y JavaScript
# servidos tal cual. No es pereza, es la decision de la primera version --
# mientras la estetica se este buscando, editar un archivo y recargar vale
# mas que cualquier cadena de compilacion. El dia que haga falta empaquetar,
# el Dockerfile de jau muestra como se agrega la etapa de Node sin que la
# imagen final la herede.

FROM python:3.12-slim

RUN useradd --create-home --uid 10001 app
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY alembic.ini .
COPY migraciones/ ./migraciones/
COPY app/ ./app/

# Los tokens del sistema de diseno llegan por el CONTEXTO ADICIONAL que
# declara el compose. En tiempo de build, no como copia versionada: la fuente
# de verdad sigue siendo /srv/01-infra/diseno/tokens/onda.css y por eso este
# proyecto NO va en consumidores.txt.
COPY --from=diseno tokens/onda.css ./app/estaticos/onda.css

USER app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
EXPOSE 8000
