# tinker

**An agent that reads a room full of people and picks the music.**

A QR code on the venue screen. People scan it with the phone already in their
hand. Five songs appear; they tap the ones they like and ask for five more.
Five rounds, under a minute, no keyboard, no app to install.

What makes it an agent rather than a jukebox is what happens in between: every
tap updates a live model of the room's taste, and that model changes the cards
the *next* person sees. Person A shifts what B is offered. B shifts C. C shifts
the room, and the room comes back around to A.

> The value here cannot exist inside a chat window. It needs several people in
> one physical space, a shared output — the speakers — and a feedback loop where
> each choice changes what everyone else is shown. A chatbot can recommend you
> music. It cannot read a party.

Live at **[tinker.onda.study](https://tinker.onda.study)**.

---

## Built during the hackathon

This project did not exist before the event. Its first directory was created at
**2026-09-12 14:09:54 UTC** — a timestamp taken from the filesystem, not typed
by hand — and the repository's own initial commit carries that moment as its
author date. [`CRONOGRAMA.md`](CRONOGRAMA.md) is the build log, phase by phase,
including what broke and what it cost to find out.

Fourteen minutes from the first request to a subdomain serving HTTPS with its
own certificate. Ninety minutes to a game playable end to end.

The environment is not a wrapper around this project — it *is* the project. The
same code runs on three surfaces at once (a phone, a television, a speaker), and
the interesting behaviour only appears when more than one person is playing.

---

## How the agent works

### The room, weighted

Every choice lands in a profile of the room. It is not a tally — two corrections
turn a counter into a profile, and both are the point:

- **Recency.** A choice has a half-life of 20 minutes. At 20 minutes it is worth
  half; at 40, a quarter. A party at 11pm is not the same party at 2am, and a
  profile that sums the whole night describes an average that never existed.
- **Agreement.** A song chosen by several people weighs more, but not linearly —
  it is `1 + log₂(n)`. The second person to pick Metallica *confirms*; the sixth
  does not contribute six times as much. Linear counting would let five friends
  with the same taste own the night.

The profile also carries a **confidence**: with three people in the room, "what
everyone likes" is what three people like. The collective term is scaled by that
confidence, and what it gives up goes back to the individual's own taste.

### The shape of a round

Round one is five different worlds — there is nothing to adapt to yet. From
round two on, every round is **three adapted cards and two open ones**:

| | |
|---|---|
| **3 adapted** | follow the detected taste. Inside those three, the room wins slots as the night goes on: `(3,0) → (2,1) → (2,1) → (1,2)` |
| **2 open** | never from the two genres dominating the room, and never from what this person has been choosing. They are the only way somebody can turn a night that closed in on itself. |

### Not an echo chamber

This is the failure mode that matters. If three people pick Metallica, the
obvious system answers with five metal tracks, everyone else stops playing, and
the party becomes one person's playlist. Two mechanisms prevent it, and both
reuse machinery that already knew how to yield:

1. A genre that passes 40% of the weighted profile has its per-round cap dropped
   to two cards, however dominant it gets.
2. The two open cards are structural. Even when the taste is unmistakable, two
   of five cards offer something else.

### Scoring

Three terms, blended per slot, with the penalties deliberately **outside** the
blend:

```
final = w_individual · individual(song)
      + w_collective · collective(song) · confidence
      + w_contrast   · contrast(song)
      − penalties(song)
```

The blend is taste; the penalties are the contract. If penalties were weighted,
a dominant crowd could override "this person already rejected five songs of this
genre" — which is exactly what the diversity reset exists to prevent.

The collective term is the weighted formula (genre .30, artist .20, decade .15,
energy .15, popularity .10, discovery .10), and every weight is an environment
variable. Each term asks for its own tag; when a tag is missing, its weight is
redistributed rather than counted as zero.

### The model proposes, Python decides

An LLM cascade (`gemini → claude → openai`, each entering only if its key is
present) receives the room profile, the person's history, the slot allocation for
this round and a pool of candidates. It answers with **local indices, never song
names** — which makes it physically incapable of naming a song that is not in the
pool.

Everything it can get wrong is then filtered: songs outside the pool, duplicates,
two tracks by the same artist, too many of one genre. Whatever is missing is
filled by the deterministic scorer. The function that does this is called
`imponer` — "impose" — and its docstring carries the rule that orders the whole
module:

```
exactly 5  >  no repeats  >  one artist per round  >  distinct genres
           >  era / language / intensity
```

When something cannot fit, the right-hand side yields. **"Exactly 5" never
yields**: four cards on a phone is a broken screen in the middle of a party.

**With zero API keys configured, the whole game still plays.** Every round, every
message, the full room profile. The model makes it better; it is never load
bearing. That is not a fallback bolted on — it is how the thing was built.

---

## Honesty about the data

Two things a reader should be able to check:

- **Energy is estimated, not measured.** Spotify retired `audio-features`
  (energy, danceability, valence, tempo) in November 2024 for every new app; the
  endpoints return 403 regardless of quota. Energy here comes from an
  `intensidad` tag (1 = slow, 5 = full throttle) assigned by the model in a cold
  tagging pass before the party. It is a declared estimate.
- **Danceability is not implemented at all.** There is nothing to measure it
  with, and an invented variable would contaminate the ones that are real.

What *is* measured: genre, decade (from the real release date), language,
per-song exposure and conversion, rejection, fatigue over a sliding window, and
how many distinct people chose each track.

---

## Architecture

```
phone (QR)  ─┐
             ├─►  FastAPI  ─►  PostgreSQL 18  ─►  weighted room profile
venue screen ┘       │                                    │
      ▲              │  WebSocket  ◄─────────────────────┘
      └──────────────┘       every choice, in the moment
                     │
                     └─►  Spotify playback queue
```

| | |
|---|---|
| **Backend** | FastAPI + SQLAlchemy 2.0 + PostgreSQL 18, Alembic migrations |
| **Realtime** | WebSocket hub in process (single worker, on purpose) |
| **Frontend** | Hand-written HTML/CSS/JS. No framework, no build step |
| **AI** | Provider cascade with structured output and a per-call time budget |
| **Deploy** | Docker Compose behind nginx, private network, no published ports |

Three design decisions worth naming:

- **The round is a database row** with `UNIQUE (participant, round)`. That single
  constraint solves four problems at once: reloading loses nothing, the prefetch
  cannot fire twice, a phone cannot burn unlimited API calls, and the row *is*
  the cost ledger for the night.
- **The model is never called inside a request someone is waiting on.** The next
  round starts cooking the moment a card is tapped, while the person is still
  looking at the current one.
- **The room profile is recomputed in full on every choice**, not incremented. A
  few hundred indexed rows is milliseconds, and a stored counter that drifts lies
  without telling you — the worst failure mode for something that decides what
  plays.

## Running it

Docker and Docker Compose. Nothing else — no Python, no Node, no database on
your machine.

```bash
cp .env.example .env          # works with no API keys at all
openssl rand -base64 32       # paste into POSTGRES_PASSWORD
docker compose -f compose.dev.yaml up -d --build
```

Then <http://localhost:8000/san-lorenzo-2026> is the phone,
<http://localhost:8000/san-lorenzo-2026/pantalla> is the TV, and
<http://localhost:8000/docs> is the API. An empty database seeds a playable
demo event on first start.

**Use `compose.dev.yaml`, not `compose.yaml`.** The two are different on
purpose. `compose.yaml` is the deployment that runs tinker.onda.study: it joins
an external proxy network, binds host paths, reads the design tokens from
outside the repo and publishes no port at all, because in production everything
enters through the reverse proxy. It cannot build or start from a clean clone,
and that is intended. `compose.dev.yaml` is the self-contained one.

There is no separate migration step: `alembic upgrade head` runs before uvicorn
in the same command, so the container refuses to start against a stale schema.

Tests — **100 of them, and not one touches an external API**. The fixtures strip
the AI keys, the Spotify credentials and the background jobs, so a test run is
fast, repeatable and paid for by nobody:

```bash
docker compose -f compose.dev.yaml run --rm -v "$PWD/pruebas:/app/pruebas" web \
  sh -c "pip install -q pytest pytest-asyncio && python -m pytest -q"
```

The tests that matter most are the ones that assert the *collective* behaviour:
that three people choosing rock change what the fourth person sees, and that they
do not turn that fourth screen into five rock songs.

## The code is in Spanish

Names, comments and documentation are in Spanish, because that is the language of
the people who build and run it. The comments explain *why*, not *what* — most of
them exist because something broke first.

---

*Built in Asunción, Paraguay.*
