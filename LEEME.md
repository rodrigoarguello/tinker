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
tocan **todas las que gusten** y la tanda cambia cuando la persona lo pide
(«Otras cinco»), porque de cinco tarjetas te pueden gustar tres. Arriba, fijo:
las elegidas en carrusel (se pueden quitar), el progreso `● ● ● ○ ○` y **la
línea del agente** — una oración por tanda diciendo qué está pasando en el
salón. Abajo, una barra fija con «Otras cinco» y «Ninguna», y un buscador de
grupos plegable. A las cinco, la pantalla de cierre con lo elegido y el top del
evento. Cuando el evento cierra, esta misma página pasa sola a la playlist: el
WebSocket avisa, nadie recarga nada.

**`pantalla.html`** — el televisor. **Alto fijo, sin scroll y sin carrusel**:
lo que no entra no existe, y nada se esconde. A la izquierda, la columna quieta:
el QR, la dirección escrita y —abajo— **lo que está sonando ahora** con los
votos que lo pusieron ahí y las tres que siguen. A la derecha, el tablero: las
cifras en una tira, las más elegidas en la columna ancha, y al lado los artistas
de la noche sobre el perfil del salón. La tarjeta de cada elección **cae desde
arriba**, sobre el título —que es texto fijo— y no sobre el ranking en vivo.

La rotación de tres escenas cada catorce segundos se fue el 2026-09-12: en una
fiesta quien levanta la vista tres segundos veía una de tres cosas al azar, y el
reparto era malo —el ranking desbordaba la pantalla mientras la escena del
perfil dejaba media columna negra—.

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
`gemini → claude → openai`. El orden lo decidió una medición previa en este
mismo servidor: 3,2 s contra 6,2 y 24. En un juego
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

- **La playlist de Spotify.** Lo elegido entra a la **cola de reproducción**,
  que es lo que sí se puede: los endpoints de *playlist* contestan 403 a las
  apps en modo Development. Escribir en la playlist necesita la extensión de
  cuota, que es un formulario y una espera.
- **`popularity` de Spotify**: tampoco viene en modo Development —se verificó
  contra la API, la clave ni aparece en el objeto— así que el término de
  mainstream del puntaje colectivo se reparte entre los otros. La fecha de
  publicación sí viene, y de ahí sale la década de verdad.
- **Moderación**: nadie puede escribir texto libre, pero tampoco hay forma de
  bajar una canción del repertorio en caliente.
- **Medir los pesos en varias fiestas.** Los de `config.py` son un punto de
  partida honesto, no una verdad: la fórmula correcta no se deduce, se mide.

---

## Mapa de archivos

```text
app/
  main.py               las páginas, /health, el WebSocket y la semilla
  modelos.py            las tablas, con el porqué de cada restricción
  config.py             las perillas: pesos, ventanas y cuotas, por entorno
  panorama.py           lo que ve el televisor, incluido el perfil del salón
  api/publico.py        entrar · tanda · elegir · avanzar · rechazar · seguir
                        grupo · elegir-buscada · panorama · qr · callback
  api/admin.py          eventos, repertorio, etiquetar, grupos, spotify, jobs
  recomendador/
    pozo.py             de dónde salen las candidatas
    publico.py          EL GUSTO DEL SALÓN, ponderado por tiempo y por acuerdo
    reglas.py           EL CONTRATO: imponer(), las ranuras y la precedencia
    mensajes.py         la línea que el agente dice en voz alta
    modelo.py           la cascada gemini → claude → openai
    etiquetador.py      género, década, idioma e intensidad, en frío
    tandas.py           lo único que escribe tandas
    prefetch.py         el obrero de fondo, atado al bucle en el arranque
  catalogo/             local (semilla), spotify, enriquecer() y resolver()
  descubrimiento/       Apple RSS y Deezer: el catálogo global se llena solo
  spotify/              cliente, cola de reproducción y reintentos
  jobs/                 métricas, perfil, rotación y descubrimiento
  tiempo_real/          el hub del WebSocket, en memoria
  estaticos/            las páginas, tinker.css y las tipografías propias
pruebas/                100 pruebas, cero llamadas a APIs externas
docs/                   pantalla por pantalla · el agente colectivo
```
