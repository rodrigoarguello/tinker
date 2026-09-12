# Tinker — the party builds its own soundtrack

**An agent that lives on the wall of a room, not in a chat window.**

A QR code on the venue screen. People scan it and play: **five rounds of five
songs, tap one per round**, and every tap feeds the next round. Under a minute.
Meanwhile the TV shows what's coming in, live, and the ranking assembles itself.
When the night ends, the **same QR** leads to the playlist.

No app, no signup, no password and **no keyboard**: at a party, every step before
"pick a song" is a person who doesn't participate.

## The loop

```text
  CREATE EVENT ──► load the repertoire ──► QR ──► pantalla.html (the TV)
                                                     │  shows the QR
                                                     ▼
                                              people scan it
                                                     │
                                                     ▼
                                        participante.html (the phone)
                                        5 cards → tap one → 5 more
                                                     │
                                       ┌─────────────┴─────────────┐
                                       ▼                           ▼
                                  PostgreSQL                  WebSocket push
                                       │                           │
                                 ranking (GROUP BY)          pantalla.html
                                                                   │
                                                            [ event closes ]
                                                                   ▼
                                                    the SAME QR → the playlist
```

An event has three states, and the QR **never changes** — it always points at
`/<event-slug>`; what changes is what that page returns:

| State | The phone | The TV |
|---|---|---|
| `previo` | Already playable | QR + what's coming in |
| `en_vivo` | The game | QR + live cards + ranking |
| `cerrado` | The playlist | QR + the result |

A printed QR still works after the event is over.

## Quickstart

Requires Docker and Docker Compose. Nothing else — no Python, no Node, no
database on your machine.

```bash
cp .env.example .env
# generate a DB password
openssl rand -base64 32          # paste into POSTGRES_PASSWORD
docker compose -f compose.dev.yaml up -d --build
docker compose -f compose.dev.yaml logs -f web
```

**Use `compose.dev.yaml`, not `compose.yaml`.** The two are different on
purpose. `compose.yaml` is the deployment that runs `tinker.onda.study`: it
joins an external proxy network, binds host paths, reads the design tokens
from outside the repo and publishes **no port at all**, because in production
everything enters through the reverse proxy. It cannot build or start from a
clean clone, and that is intended. `compose.dev.yaml` is the self-contained
one: it publishes 8000, uses a named volume, and reads the design token from
the copy in `vendor/`.

Migrations run before uvicorn in the same command, so the container refuses to
start against a stale schema. With `TINKER_SEMILLA=si` and an empty database a
**playable demo event** is seeded (slug `san-lorenzo-2026`) with 149 songs:

- <http://localhost:8000/san-lorenzo-2026> — the phone (the game)
- <http://localhost:8000/san-lorenzo-2026/pantalla> — the TV
- <http://localhost:8000/admin> — create events, paste repertoires, open/close
- <http://localhost:8000/docs> — Swagger UI, 36 routes
- <http://localhost:8000/health> — process **and** database

Swagger is off unless `TINKER_DOCS` asks for it; `compose.dev.yaml` turns it on
by itself. Production leaves it off — the app is public, its API surface has no
reason to be.

Tear it down with `docker compose -f compose.dev.yaml down` (add `-v` to drop
the database too).

**No AI keys are required to run it.** With zero keys the five-round game plays
end to end from the deterministic rules and nobody sees an error.

### Without Docker

Needs Python 3.12 and a reachable PostgreSQL.

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
export TINKER_BD="postgresql+psycopg://user:pass@localhost:5432/tinker"
alembic upgrade head
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
```

`--workers 1` is deliberate — see *Decisions* below.

## Tests

100 tests. The rules and the cascade run **without spending a single model call**;
the end-to-end rules run against a real PostgreSQL, because the schema leans on
Postgres constraints and testing it on another engine would test something else.

```bash
docker compose -f compose.dev.yaml run --rm -v "$PWD/pruebas:/app/pruebas" web \
  sh -c "pip install -q pytest pytest-asyncio && python -m pytest -q -p no:cacheprovider"
```

## The engine

Three providers, in order, each entering only if its key is present:
`gemini → claude → openai`. The order was decided by measurement on this same
server, not by preference: 3.2 s vs 6.2 s vs 24 s on the equivalent task. In a
game where the wait between rounds is felt, that ordering outranks everything.

Measured here, on tinker's actual task (pick five out of a pool of sixty):

| | |
|---|---|
| `gemini-3.5-flash-lite` | **1.6–1.9 s** per round · ~1,300 input tokens, ~55 output |
| Wait the phone actually sees | ~2.1 s, with a skeleton |

**The model contributes judgment; Python enforces the contract.** The hard rules
— exactly five, no repeats, one song per artist per round, genre diversity — are
applied in `reglas.imponer()` **after** the model answers. A model that returns
six songs, two by the same artist, or invented indices is a normal case, not an
error. With an empty proposal — which is what happens with no keys — a valid
round still comes out.

Precedence, when the repertoire can't satisfy everything:

```
exactly 5  >  no repeats  >  one artist per round  >  distinct genres  >  era/language/intensity
```

"Exactly 5" never yields: a round of four cards is a broken screen in the middle
of a party.

**Round 1 never calls the model.** There is no signal yet — maximum diversity is
a rule, not a judgment. It comes out instantly, shuffled per device, which is
exactly where you win or lose people.

**Spend ceiling per event**: `eventos.tope_ia` (1500 by default). Once reached,
everything falls back to deterministic with a WARNING and a counter visible in
`/admin`. And by the *shape of the data*, a phone cannot cause more than **four
model calls in its entire lifetime**.

## The repertoire

**The event's curated list is the only source.** It's pasted in `/admin`, one
song per line, `Title — Artist`. Nothing outside that list can ever appear on a
card.

- Minimum **40 songs**, validated on paste with a readable 422. Five rounds of
  five are 25 per person, and the one-artist-per-round rule needs room. A
  30-song repertoire doesn't fail on load: it fails on round five, in the venue.
- An event without a repertoire **cannot go live**.
- Songs absent from Spotify and from the local catalog enter as `curado`, with an
  identity derived from `title|artist`: the same song in two events is the same
  row, and therefore the same row in the ranking.

## Background intelligence

A second engine — the one that decides which songs *should* be in a repertoire —
runs as scheduled in-process jobs. **None of it runs inside a request from the
game**, and all of it can fail without the party noticing:

| Job | Every | What it does |
|---|---|---|
| `spotify:cola` | 60 s | Pushes picked songs into the live playlist |
| `music:perfil` | 2 min | Recomputes the room's taste profile |
| `music:score` | hours | Popularity + conversion + fatigue → a single score |
| `music:discover:*` | hours | Deezer / Apple Music: Paraguay, global, by genre |
| `music:refill` | 30 min | Tops the active catalog back up |
| `music:rotation` | 6 h | Moves songs into low-rotation / quarantine |

Every job catches its own errors, never raises, records how it went in
`fuentes_estado` (visible in `/admin`), and is idempotent.

## Decisions worth reading before undoing

**The served round is persisted.** `tandas` + `tanda_canciones` with
`UNIQUE (participante, ronda)`. One constraint gives four properties: a reload
loses nothing, the prefetch can't double-fire, a phone can't spend unbounded
model calls, and the round row *is* the night's ledger.

**You can only pick from the round you were served.** The phone sends an id and
the server checks it's among the five it served itself. It's the strong version
of "data comes from the catalog, never from the request".

**Identity is a browser UUID.** There is no login and there won't be one. It
lives in `localStorage` and does exactly two things: remind a person what they
picked, and let the database reject the same vote twice. It identifies nobody —
no name, no phone number, no email, no IP is stored.

**One uvicorn worker.** The WebSocket hub lives in memory. With two, the screen
connected to A wouldn't see what B publishes.

**The prefetch is scheduled with `call_soon_threadsafe`.** Routes that touch the
DB run in the threadpool, where there is no event loop: a `create_task` from
there is never born and **nothing visibly fails**.

## File map

```text
app/
  main.py               the pages, /health, the WebSocket, the seed
  modelos.py            the 15 tables, with the why of each constraint
  panorama.py           everything the TV shows
  api/publico.py        enter · round · pick · picked · panorama · qr
  api/admin.py          events, repertoire, Spotify, result
  recomendador/
    pozo.py             where the candidates come from
    reglas.py           THE CONTRACT: imponer() and the precedence
    modelo.py           the gemini → claude → openai cascade
    tandas.py           the only writer of rounds
    prefetch.py         the background worker, bound to the loop at startup
  descubrimiento/       Deezer, Apple Music, dedup, normalize, import
  catalogo/             local (seed), spotify, resolve()
  jobs/                 the scheduler and the background work
  tiempo_real/hub.py    the in-memory WebSocket hub
  estaticos/            the pages, tinker.css, self-hosted fonts
pruebas/                rules, pure contract, cascade — without spending
docs/                   screen-by-screen design
```

## License

MIT
