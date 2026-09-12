# Pantalla por pantalla

Lo que contiene cada interfaz y por qué. Escrito después de construirlas, con
lo que se aprendió construyéndolas: donde el diseño original y lo que quedó no
coinciden, se dice cuál ganó.

La piel viene de `radio.onda.study`: la paleta cobalto, el relieve de tres
capas tintado de azul, el filo de luz cenital, la curva `--ease` y el patrón
táctil —sube al pasar, **se hunde** al tocar—. Lo que cambia es el contenido:
allá el héroe de cada tarjeta es la frecuencia; acá es el título de la canción.
El tema oscuro es el defecto, al revés que en radio: una fiesta es de noche.

---

## 1 · `participante.html` — el teléfono

Ruta: `tinker.onda.study/<evento>` · es lo que contiene el QR.

### La cabecera, siempre visible

```text
 ● Tinker            IA, ¿Tín que querés? — San Lorenzo
 [♪ Flowers ×] [♪ Cactus ×]
 ● ● ● ○ ○   RONDA 3 DE 5      TUS ELEGIDAS 2/5
```

Las elegidas van en **carrusel horizontal** y no en lista: en un teléfono, la
altura es lo que no sobra. Cada chip se puede quitar, y quitar **devuelve el
turno** — vuelve la misma tanda de esa ronda y se elige de nuevo entre las
mismas cinco. Nunca arma una tanda nueva: eso cierra de raíz el ciclo
"elegir → quitar → elegir", que es el vector de gasto más obvio del diseño.

### El juego

```text
   ¿Cuál te gusta más?
   Tocá la que quieras escuchar hoy.

   ┌──────────────────────────────┐
   │ (D)  Despertar        ELEGIR │   ← la tarjeta entera es el botón
   │      Purahéi Soul            │
   └──────────────────────────────┘
   …otras cuatro
```

- **El disco de color** a la izquierda: un orbe con el tono derivado del
  artista y su inicial. Existe porque el repertorio curado todavía no trae
  carátulas, y cinco tarjetas de puro texto no son una aplicación de música.
  El mismo artista es siempre del mismo color, así que la pantalla tiene
  memoria visual. Con Spotify, la carátula se pone encima y esto queda de
  respaldo.
- Los tonos salen de una **paleta de catorce** y no de los 360 grados sueltos:
  con el matiz libre salían verdes ácidos que sobre el fondo oscuro chillan.
- Al tocar: la elegida se marca, las otras cuatro se van, y entra la tanda
  siguiente **en cascada** (40 ms entre tarjeta y tarjeta). Si todavía no está
  lista, **esqueleto** con "Buscando las próximas cinco…".
- La animación y el pedido al servidor corren **juntos**: si se esperara la
  respuesta para animar, el juego se sentiría lento aunque el servidor conteste
  en 30 ms.

### El cierre

```text
        ●
     ¡Listo!
  Elegiste tus 5 canciones.
  Tus gustos ya forman parte de IA, ¿Tín que querés? — San Lorenzo

  [las cinco elegidas]

  CÓMO VA QUEDANDO LA DE TODOS
  1  Seminare · Serú Girán        1
  …

  [ Abrir la playlist ]
```

El top del evento al final no es decoración: es lo que hace que alguien se
quede mirando la pantalla en vez de guardar el teléfono.

### Cuando el evento cierra

El teléfono llega solo. El WebSocket avisa del cambio de estado y la página
pasa a la playlist sin que nadie recargue nada. Si todavía no hay enlace
cargado, dice que en un rato se publica ahí mismo — y cuando se carga, cambia
sola otra vez.

---

## 2 · `pantalla.html` — el televisor

Ruta: `tinker.onda.study/<evento>/pantalla`. Se abre en el monitor y se la
deja: no tiene un solo control.

```text
 ┌───────────────┬──────────────────────────────────────┐
 │   ● Tinker    │  IA, ¿Tín que querés? — San Lorenzo   │
 │               │  San Lorenzo                          │
 │  ┌─────────┐  │  ┌────────┐ ┌────────┐ ┌────────┐     │
 │  │   QR    │  │  │  247   │ │  816   │ │  193   │     │
 │  └─────────┘  │  │PERSONAS│ │ELECCION│ │CANCION.│     │
 │  Escaneá y    │  └────────┘ └────────┘ └────────┘     │
 │  elegí tus    │                                       │
 │  canciones    │  ▮▮▮ LAS MÁS ELEGIDAS                 │
 │               │  1 (S) Seminare · Serú Girán      38  │
 │ tinker.onda   │  2 (C) Cactus · Gustavo Cerati    32  │
 │ .study/slug   │  …                                    │
 └───────────────┴──────────────────────────────────────┘

              ┌────────────────────────────┐
              │      NUEVA ELECCIÓN        │   ← entra desde abajo
              │   Un poco de amor francés  │     cada vez que alguien elige
              │      Babasónicos           │
              │   La eligió una persona    │
              └────────────────────────────┘
```

**Izquierda: lo inmóvil.** El QR y la dirección escrita debajo, para quien mira
desde un ángulo donde el QR no enfoca o prefiere tipear.

**Derecha: lo vivo.** Las tres cifras se actualizan con cada elección. Debajo
alternan cada catorce segundos las más elegidas (ocho) y los artistas de la
noche (cinco). **Ocho y no diez**: con la tarjeta que irrumpe abajo, las dos
últimas filas quedaban tapadas justo cuando el salón las mira.

**La tarjeta que irrumpe** es el corazón de la pantalla. Dice dos cosas
distintas según el caso:

| Caso | Rótulo | Línea de abajo |
|---|---|---|
| Nadie la había elegido | Nueva elección | La eligió una persona |
| Ya estaba | ¡Otro voto! | Ya la eligieron 24 personas |

Con mucha gente llegan ráfagas: se encolan y se muestran de a una; si la cola
pasa de seis, se descartan las viejas. Y **la fila que sube de votos
destella**: es cómo se entera el salón de que su elección llegó, sin tener que
mirar el número.

Abajo a la derecha, un punto que late y "en vivo". Si el socket se cae, el
punto se pone rojo y dice "reconectando…". Sin eso, una pantalla congelada y
una pantalla tranquila se ven exactamente igual.

**Sin un solo emoji**, y es deliberado: en un televisor cualquiera, un emoji
sin fuente instalada es un cuadrito vacío. Se comprobó en la primera captura.
El lenguaje visual es de trazo —las barritas del espectro, el disco, el punto
que late—, como en radio.

---

## 3 · `/admin` — la tercera pantalla que no estaba en el plan

El plan decía dos interfaces y un modo administrador dentro de la pantalla.
Quedó aparte por una razón concreta: **la pantalla se abre en un televisor del
salón**, y un modo administrador ahí es un menú a la vista de todos y a un
toque de cerrar el evento por accidente.

Tiene tres bloques: crear un evento, pegar el enlace de la playlist, y cargar
el repertorio (`Tema — Artista` por línea). La tabla muestra estado, tamaño del
repertorio, personas, elecciones y **cuántas llamadas al modelo lleva el evento
contra su tope** — ese número tiene que verse acá y no sólo en el log, porque
es lo que hace que alguien se entere a tiempo.

Sobrio a propósito: es interno, se usa dos minutos por evento y vestirlo no le
agrega nada.

---

## Lo que se aprendió construyéndolo

**La CSP descarta los estilos inline en silencio.** Los cinco discos salieron
del mismo azul porque `style="--tono:214"` no sobrevive a `style-src 'self'`.
No hay error: simplemente no se aplica. Se arregló pasando el tono por el
CSSOM (`setProperty`), que CSP no gobierna, y sacando todos los `style=` del
HTML.

**Un SVG servido por `<img>` necesita `xmlns`.** `segno.svg_inline()` lo omite
—está pensado para incrustar en el HTML— y el QR del televisor era un recuadro
roto. Ningún navegador avisa.

**El juego se encerraba.** Jugando una partida entera eligiendo siempre la
primera tarjeta salieron cuatro románticas de cinco, y dos del mismo artista.
La regla de "un artista por tanda" no lo cubría: son tandas distintas. Se
agregó penalización por artista ya elegido y **saturación de género** —a la
tercera cumbia elegida, proponer una cuarta ya no es acompañar el gusto—.
Medido después: 0 de 10 partidas con artista repetido y nueve géneros
representados.

---

## Lo que falta, y es a propósito

**Las carátulas.** Llegan con Spotify. El disco de color está diseñado para
que su ausencia no se note como una falta.

**Publicar la playlist.** El enlace se pega a mano. Escribir en una playlist
de Spotify desde el servidor necesita una app propia y autorizar la cuenta una
vez: buscar es anónimo, publicar se hace en nombre de una persona.

**Etiquetar el repertorio con el modelo.** Hoy las canciones curadas entran sin
género y la diversidad de la ronda 1 se apoya sólo en el artista.
