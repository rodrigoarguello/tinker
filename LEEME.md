# tinker — la fiesta arma su propia música

`https://tinker.onda.study`

Un QR en la pantalla del salón. La gente lo escanea y juega: **cinco tandas de
cinco canciones, toca una de cada tanda**, y cada elección alimenta la
siguiente. Menos de un minuto. Mientras tanto, el televisor muestra en vivo lo
que va entrando y el ranking se arma solo. Al final, el **mismo QR** lleva a la
playlist.

Sin aplicación, sin registro, sin contraseña y **sin teclado**: en una fiesta,
cualquier paso previo a "elegí una canción" es gente que no participa.

---

## El circuito

```text
   CREAR EVENTO ──► cargar el repertorio ──► QR ──► pantalla.html (el televisor)
                                                      │  muestra el QR
                                                      ▼
                                                 la gente escanea
                                                      │
                                                      ▼
                                            participante.html (el teléfono)
                                             5 tarjetas → toca una → otras 5
                                                      │
                                        ┌─────────────┴──────────────┐
                                        ▼                            ▼
                                  PostgreSQL                  empujón por WS
                                        │                            │
                                  ranking (GROUP BY)          pantalla.html
                                                                     │
                                                              [ se cierra ]
                                                                     ▼
                                                      el MISMO QR → la playlist
```

Los tres estados de un evento:

| Estado | El teléfono | La pantalla |
|---|---|---|
| `previo` | Ya se puede jugar | QR + lo que va entrando |
| `en_vivo` | Se juega | QR + tarjetas en vivo + ranking |
| `cerrado` | Muestra la playlist | QR + el resultado |

El QR **no cambia nunca**: apunta a `tinker.onda.study/<evento>` y lo que
cambia es lo que esa página devuelve. Un QR impreso sirve después de que el
evento terminó.

---

## Las dos páginas

**`participante.html`** — el teléfono. Cinco tarjetas grandes por ronda; se
toca una. Arriba, fijo: las elegidas en carrusel (se pueden quitar) y el
progreso `● ● ● ○ ○`. A las cinco, la pantalla de cierre con lo elegido, el
top del evento y el enlace a la playlist. Cuando el evento cierra, esta misma
página pasa sola a la playlist: el WebSocket avisa, nadie recarga nada.

**`pantalla.html`** — el televisor. A la izquierda, lo único inmóvil: el QR y
la dirección escrita. A la derecha, lo vivo: cifras, las más elegidas, los
artistas de la noche, y la tarjeta que irrumpe desde abajo cada vez que
alguien elige. Alterna entre las dos listas cada catorce segundos.

Hay una tercera página, `/admin`, y es la única detrás de Authelia.

---

## Decisiones que conviene no deshacer sin leer esto

**La tanda servida se guarda.** Las cinco tarjetas que vio un teléfono en una
ronda son filas en `tandas` + `tanda_canciones`, con
`UNIQUE (participante, ronda)`. Esa sola restricción resuelve cuatro cosas:
recargar la página no pierde nada, el prefetch no puede dispararse dos veces,
un teléfono no puede gastar llamadas al modelo sin fin —hay a lo sumo `rondas`
tandas por participante, **por forma de los datos**— y la fila de la tanda *es*
el libro de gastos de la noche.

**El modelo aporta juicio; Python impone el contrato.** Las reglas duras
—exactamente cinco, sin repetir, un artista por tanda, diversidad de géneros—
se aplican en `reglas.imponer()` **después** de que el modelo contesta. Un
modelo que devuelve seis canciones, dos del mismo artista o índices inventados
es un caso normal, no un error. Y con la propuesta vacía —que es lo que pasa
sin claves— sale una tanda válida igual.

**La precedencia, cuando el repertorio no da para todo:**

```
exactamente 5  >  no repetir  >  un artista por tanda  >  géneros distintos  >  época/idioma/intensidad
```

"Exactamente 5" no cede nunca: una tanda de cuatro tarjetas es una pantalla
rota en medio de una fiesta.

**La ronda 1 nunca llama al modelo.** No hay ninguna señal todavía: la máxima
diversidad es una regla, no un juicio. Sale instantánea y barajada por
dispositivo, que es donde se gana o se pierde a la gente.

**Solo se puede elegir de la tanda servida.** El teléfono manda un id y el
servidor comprueba que esté entre los cinco que él mismo mostró. Es la versión
fuerte de "los datos salen del catálogo, nunca del pedido".

**La identidad es un UUID del navegador.** No hay login ni lo va a haber. Vive
en `localStorage` y sirve para recordarle a la persona lo que eligió y para
que la base rechace el mismo voto dos veces. No identifica a nadie.

**Un solo worker de uvicorn.** El hub de WebSocket vive en memoria. Con dos, la
pantalla conectada al A no vería lo que publica el B.

**El prefetch se agenda con `call_soon_threadsafe`.** Las rutas que tocan la
base corren en el threadpool, donde no hay bucle de eventos: un `create_task`
desde ahí no nace y **nada falla a la vista**. Ya pasó en jau.

---

## El repertorio

**La lista curada del evento es la única fuente.** Se pega en `/admin`, una
canción por línea, `Tema — Artista`. Nada fuera de esa lista puede aparecer en
una tarjeta.

- Mínimo **40 canciones**, validado al pegar con un 422 legible. Cinco rondas
  de cinco son 25 por persona y la regla de un artista por tanda necesita
  margen. Un repertorio de 30 no falla al cargarlo: falla en la quinta ronda,
  en el salón.
- Un evento sin repertorio **no puede ponerse en vivo**.
- Las canciones que no estén en Spotify ni en el catálogo local entran como
  `curado`, con una identidad derivada de `titulo|artista`: el mismo tema en
  dos eventos es la misma fila, y por lo tanto la misma en el ranking.
- Las etiquetas (género, época, idioma, intensidad) son del **repertorio de
  cada evento**, no de la canción: el dueño de una fiesta puede corregir "esto
  acá es cumbia" sin cambiárselo a otro.

---

## El motor

Cascada de tres proveedores, cada uno entra solo si tiene su clave:
`gemini → claude → openai`. El orden lo decidió la medición de focus en este
mismo servidor (`focus/app/ia.py:36-64`): 3,2 s contra 6,2 y 24. En un juego
donde la espera se nota, eso manda.

Medido acá, con la tarea de tinker (elegir cinco de un pozo de sesenta):

| | |
|---|---|
| `gemini-3.5-flash-lite` | **1,6–1,9 s** por tanda · ~1.300 tokens de entrada, ~55 de salida |
| Espera que ve el teléfono | ~2,1 s, con esqueleto |

**Sin ninguna clave, el juego se juega entero**: las tandas salen de las
reglas deterministas y nadie ve un error. Esa es la prueba
`test_con_los_tres_caidos` y es el criterio de aceptación del módulo.

**Techo de gasto por evento**: `eventos.tope_ia` (1500 por defecto). Alcanzado,
todo pasa a determinista con un WARNING y el contador visible en `/admin`.
Además, por forma de los datos, un teléfono no puede provocar más de **cuatro
llamadas en toda su vida** (la ronda 1 es gratis).

---

## Operación

```bash
cd /srv/02-onda/proyectos/tinker

sudo docker compose up -d --build     # levantar
sudo docker compose logs -f web       # mirar

# las pruebas, SIN gastar una sola llamada al modelo
sudo docker compose run --rm -v "$PWD/pruebas:/app/pruebas" web \
     sh -c "pip install -q pytest pytest-asyncio && python -m pytest -q -p no:cacheprovider"
```

Crear un evento, cargarle el repertorio, ponerlo en vivo y cerrarlo se hace
desde `https://tinker.onda.study/admin` (Authelia, grupo `admin`).

⚠ **Las claves de modelo son hoy las mismas que usa focus.** Rotarlas rompe los
dos proyectos y el gasto se mezcla sin forma de separarlo. Para medir lo que
cuesta un evento, darle a tinker las suyas.

---

## Lo que falta

- **Publicar en Spotify.** El enlace de la playlist se pega a mano en `/admin`.
  Escribir en ella desde el servidor necesita una app de Spotify
  (`CLIENT_ID` + `SECRET`) y autorizar la cuenta una vez: buscar es anónimo,
  publicar se hace en nombre de una persona.
- **Etiquetar el repertorio con el modelo** (`/repertorio/etiquetar`): hoy las
  canciones curadas entran sin género y la diversidad de la ronda 1 se apoya
  solo en el artista. Es la llamada más barata del sistema —una vez por evento,
  sin nadie esperando— y mejora todas las tandas de la noche.
- **Moderación**: nadie puede escribir texto libre, pero tampoco hay forma de
  bajar una canción del repertorio en caliente.

---

## Mapa de archivos

```text
app/
  main.py               las páginas, /health, el WebSocket y la semilla
  modelos.py            las ocho tablas, con el porqué de cada restricción
  panorama.py           lo que ve el televisor
  api/publico.py        entrar · tanda · elegir · elegidas · panorama · qr
  api/admin.py          eventos, repertorio y resultado
  recomendador/
    pozo.py             de dónde salen las candidatas
    reglas.py           EL CONTRATO: imponer() y la precedencia
    modelo.py           la cascada gemini → claude → openai
    tandas.py           lo único que escribe tandas
    prefetch.py         el obrero de fondo, atado al bucle en el arranque
  catalogo/             local (semilla), spotify y resolver()
  estaticos/            las páginas, tinker.css y las tipografías propias
pruebas/                las reglas, el contrato puro y la cascada sin gastar
docs/                   el diseño pantalla por pantalla
```
