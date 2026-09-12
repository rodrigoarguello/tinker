# Tinker — la fiesta construye su propia banda sonora

**Un agente que vive en la pared de un lugar, no en una ventana de chat.**

Un código QR en la pantalla del lugar. La gente lo escanea y juega: **cinco rondas de cinco canciones, toca una por ronda**, y cada toque alimenta la siguiente. Menos de un minuto. Mientras tanto, la TV muestra lo que viene, en vivo, y el ranking se arma solo. Cuando termina la noche, el **mismo QR** lleva a la playlist.

Sin app, sin registro, sin contraseña y **sin teclado**: en una fiesta, cada paso antes de "elige una canción" es una persona que no participa.

## El flujo

Una noche tiene tres estados, y el QR **nunca cambia** — siempre apunta a `/<evento-slug>`; lo que cambia es qué retorna esa página:

| Estado | El teléfono | La TV |
|---|---|---|
| `previo` | Ya jugable | QR + lo que viene |
| `en_vivo` | El juego | QR + las tarjetas + ranking en vivo |
| `cerrado` | La playlist | QR + el resultado |

Un QR impreso sigue funcionando después de que termina el evento.

## Inicio rápido

Requiere Docker y Docker Compose.

```bash
cp .env.example .env
# generar una contraseña para la BD
openssl rand -base64 32          # pegar en POSTGRES_PASSWORD
docker compose up -d --build
docker compose logs -f web
```

Luego abrí la app. Con `TINKER_SEMILLA=si` y una base de datos vacía, se siembra un **evento de demostración** jugable (slug `san-lorenzo-2026`) con su repertorio completo:

- `/<slug>` — el teléfono (el juego)
- `/<slug>/pantalla` — la TV
- `/admin` — crear eventos, pegar repertorios, abrir/cerrar, ver el gasto
- `/health` — proceso **y** base de datos

**No se requieren claves de IA.** Sin claves, el juego de cinco rondas funciona de punta a punta desde las reglas deterministas y nadie ve un error.

### Sin Docker

Necesita Python 3.12 y PostgreSQL accesible.

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
export TINKER_BD="postgresql+psycopg://usuario:contraseña@localhost:5432/tinker"
alembic upgrade head
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
```

`--workers 1` es deliberado — lee el README en inglés para saber por qué.

## Pruebas

55 pruebas. Las reglas y la cascada funcionan **sin gastar una sola llamada al modelo**. Las pruebas end-to-end corren contra PostgreSQL real porque el esquema depende de restricciones de Postgres.

```bash
docker compose run --rm -v "$PWD/pruebas:/app/pruebas" web \
  sh -c "pip install -q pytest pytest-asyncio && python -m pytest -q -p no:cacheprovider"
```

## El motor

Tres proveedores en orden, cada uno entra solo si su clave existe:
`gemini → claude → openai`. El orden se decidió midiendo latencia en este mismo servidor, no por preferencia: 3.2 s vs 6.2 s vs 24 s en la tarea equivalente.

**El modelo propone; Python aplica.** Las reglas duras — exactamente cinco, sin repeats, un artista por ronda, diversidad de géneros — se aplican **después** de que el modelo responde. Un modelo que devuelve seis canciones o índices inventados es un caso normal, no un error. Con ninguna propuesta — lo que pasa sin claves — una ronda válida igual sale.

**Ronda 1 nunca llama al modelo.** No hay señal todavía — máxima diversidad es una regla, no un juicio.

**Tope de gasto por evento**: `eventos.tope_ia` (1500 por defecto). Una vez alcanzado, todo vuelve a determinista. Y por la *forma de los datos* — `UNIQUE (participante, ronda)` — un teléfono **no puede** causar más de **cuatro llamadas al modelo en toda su vida**.

## El repertorio

**La lista curada del evento es la única fuente.** Se pega en `/admin`, una canción por línea, `Título — Artista`. Nada fuera de esa lista puede aparecer nunca en una tarjeta.

- Mínimo **40 canciones**, validadas al pegar con un 422 legible. 
- Un evento sin repertorio **no puede ir en vivo**.
- Las canciones ausentes de Spotify y del catálogo local entran como `curado`.

## Inteligencia de fondo

Un segundo motor — que decide cuáles canciones *deberían* estar en un repertorio — corre como trabajos en proceso. **Nada de esto corre adentro de una solicitud del juego**, y todo puede fallar sin que la fiesta lo note.

## Decisiones que vale leer antes de cambiar

**La ronda servida se persiste.** `tandas` + `tanda_canciones` con `UNIQUE (participante, ronda)`. Una restricción da cuatro propiedades: un reload no pierde nada, el prefetch no puede dispararse dos veces, un teléfono no gasta llamadas ilimitadas, y la fila de ronda *es* el ledger de la noche.

**Solo se puede elegir de la ronda que te sirvieron.** El teléfono envía un id y el servidor verifica que esté entre los cinco que él mismo sirvió.

**Identidad es un UUID de navegador.** No hay login y no lo habrá. Vive en `localStorage` y hace exactamente dos cosas: recordar qué eligió una persona, y dejar que la base de datos rechace el mismo voto dos veces. No identifica a nadie — sin nombre, sin teléfono, sin email, sin IP.

**Un worker de uvicorn.** El hub de WebSocket vive en memoria.

## Licencia

MIT

---

📝 **Lee el [README.md](README.md) completo en inglés para todos los detalles técnicos.**
