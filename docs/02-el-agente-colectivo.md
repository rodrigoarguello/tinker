# El agente colectivo

Cómo tinker aprende el gusto de un salón, ronda a ronda, y por qué está hecho
así. Este documento es el mapa del motor; el recorrido por pantallas está en
`01-pantalla-por-pantalla.md`.

---

## La idea, en una frase

**Lo que elige una persona cambia las cartas que ve la siguiente.** A influye en
B, B en C, y el perfil del salón vuelve a entrar en la tanda de A. Nadie está
armando su propia playlist: entre todos están armando la noche, sin saber que lo
están haciendo.

Todo lo demás de este documento existe para sostener esa frase.

---

## El perfil del salón · `app/recomendador/publico.py`

Una consulta agrupada sobre las solicitudes del evento. No es un contador: son
dos correcciones sobre un contador, y las dos son el punto.

### Lo reciente pesa más

```python
def decaimiento(minutos: float) -> float:
    return 0.5 ** (minutos / config.PERFIL_VIDA_MEDIA_MIN)   # 20 minutos
```

A los 20 minutos una elección vale la mitad; a los 40, un cuarto. Una fiesta a
las once no es la misma a las dos, y un perfil que suma toda la noche por igual
describe un promedio que no existió en ningún momento.

No hay nada que reiniciar: la ventana se corre con el reloj, igual que la
fatiga. Es la misma decisión de diseño en dos lugares distintos.

### Lo que eligieron varios pesa más

```python
def acuerdo(personas: int) -> float:
    return 1.0 + math.log2(max(1, personas))
```

La segunda persona que elige Metallica **confirma**; la sexta no aporta seis
veces más. Si contara lineal, cinco amigos del mismo gusto se llevarían la noche
entera y el resto del salón dejaría de existir para el motor.

### Y cuánto vale todo eso

```python
confianza = min(1.0, personas / PERSONAS_PARA_CONFIANZA)   # 4 personas
```

Con tres personas, «el gusto del salón» es el gusto de tres. El término
colectivo se escala con la confianza y **lo que cede se lo lleva el gusto
individual**. Es la única forma honesta de usar un promedio de cuatro datos.

### Cuándo se recalcula

En cada elección, dentro de `_empujar_eleccion` — el mismo lugar que ya publica
por WebSocket. Entero, no por incrementos: son cientos de filas agrupadas por
índice y un contador guardado que se desincroniza **miente sin avisar**, que es
el peor modo de falla posible para algo que decide lo que suena.

Si el cálculo falla, la elección se guarda igual. El perfil es una mejora del
próximo reparto, no parte del gesto de la persona.

---

## La forma de una tanda · `reglas.ranuras()`

```
ronda 1   →  5 exploración          cinco mundos distintos; no sabemos nada
ronda 2+  →  3 adaptadas + 2 abiertas
```

Las **tres adaptadas** siguen el gusto detectado, y dentro de ellas el salón va
ganando lugar:

| ronda | individual | salón | abiertas |
|---|---|---|---|
| 2 | 3 | 0 | 2 |
| 3 | 2 | 1 | 2 |
| 4 | 2 | 1 | 2 |
| 5 | 1 | 2 | 2 |

Se mueve de a una carta entera porque son tres ranuras. **La gradación fina no
vive ahí sino en el puntaje**, donde el término colectivo pesa sobre *todas* las
candidatas y no sólo sobre su ranura: las ranuras garantizan una presencia
mínima, los pesos hacen el matiz.

Las **dos abiertas** nunca salen de los dos géneros dominantes del salón ni de
lo que esta persona viene eligiendo. No son azar uniforme —eso daría basura—
sino «no dictadas por tu gusto», que es lo que hace falta para que alguien pueda
torcer una noche que se cerró.

Si la persona rechazó dos tandas enteras seguidas, las adaptadas bajan de tres a
una: quien descartó dos tandas al hilo no está pidiendo más de lo mismo con otro
nombre.

---

## El puntaje · `reglas._puntaje()`

```python
final = ( w_individual · individual(c)
        + w_colectivo  · colectivo(c) · confianza
        + w_contraste  · contraste(c) )
        − castigos(c)
```

Cada ranura es una **mezcla** de los tres términos, no un término solo:

| ranura | individual | colectivo | contraste |
|---|---|---|---|
| `explorar` (ronda 1) | 0.00 | 0.35 | 0.65 |
| `individual` | 0.70 | 0.20 | 0.10 |
| `colectivo` | 0.25 | 0.65 | 0.10 |
| `contraste` | 0.10 | 0.10 | 0.80 |

Por eso el salón influye también en las cartas individuales, y por eso su
presencia crece de a poco en vez de aparecer de golpe cuando le toca una ranura.

### Por qué los castigos quedan afuera de la mezcla

**La mezcla es gusto; los castigos son el contrato.** Si los castigos entraran
ponderados, un colectivo muy dominante podría pasarle por encima a «esta persona
ya rechazó cinco de este género», que es exactamente lo que el reinicio de
diversidad existe para impedir.

Los castigos están en escala absoluta y no se ponderan nunca: fatiga (hasta −8),
rechazo propio del género (hasta −6), rechazo del salón (−1.5), artista que la
persona ya se llevó (−4), y repetir género, época o idioma dentro de la misma
tanda.

### El término colectivo

Es la fórmula del pedido, con los pesos en `config.PESOS_COLECTIVO` y todos
ajustables por variable de entorno:

```
0.30 género  +  0.20 artista  +  0.15 década
0.15 energía +  0.10 popularidad  +  0.10 descubrimiento
```

**Cada término pide su etiqueta y, si falta, se reparte su peso entre los
demás** en vez de contar como cero. Un repertorio sin década no puntúa peor que
uno con: puntúa con lo que tiene.

---

## No encerrarse

Es el modo de falla que importa. Si tres personas eligen Metallica, el sistema
obvio contesta con cinco de metal, el resto del salón deja de jugar y la fiesta
se convierte en la playlist de una persona.

Dos mecanismos, y los dos **reusan maquinaria que ya sabía ceder**:

1. **`tope_de()`**: un género que pasa el 40% del perfil ponderado ve su tope
   por tanda bajar a dos cartas, por dominante que se ponga.
2. **Las dos abiertas son estructurales**: aunque el gusto esté clarísimo, dos
   de cinco ofrecen otra cosa.

Ninguno de los dos es una regla dura nueva dentro de `imponer()`. Viven en la
composición y en el tope, que ya tienen su forma de ceder, y así **«exactamente
5» sigue sin ceder nunca**.

---

## Las 36 bandas: de cuota a empuje

Hubo una regla dura: tres de cada cinco tarjetas *tenían* que salir de la lista
de grupos del dueño del evento. Funcionaba mientras el motor no sabía nada, y
chocaba de frente con el motor adaptativo — si el salón converge a cumbia, la
cumbia sólo puede mover dos cartas de cinco.

Ahora la lista entra por puntaje, con un bono que **se desvanece solo**:

```python
BONO_PRIORITARIA = {1: 3.0, 2: 2.0, 3: 1.2, 4: 0.6, 5: 0.3}
```

Fuerte mientras no sabemos nada —ahí su lista es la mejor apuesta que hay—, casi
nulo en la ronda 5, donde lo que el salón mostró vale más que una lista escrita
antes de que llegara nadie. **Las bandas dejan de aparecer porque están
obligadas y empiezan a aparecer porque ganan.**

La cuota dura no se desmontó: `prioritarias_por_tanda` en uno o más la devuelve.
Lo que cambió es el valor por defecto.

---

## El modelo, y dónde manda Python

La cascada (`gemini → claude → openai`) recibe el perfil del salón con
porcentajes, la energía, las décadas, las últimas cinco elecciones del salón, la
franja horaria, cuánta gente hay, el historial de esta persona y el reparto de
ranuras de la ronda.

**Contesta índices locales, nunca nombres de canciones.** Es lo que lo hace
físicamente incapaz de nombrar algo que no esté en el pozo: sólo puede emitir
enteros que nosotros mapeamos de vuelta.

Después `reglas.imponer()` filtra todo lo que puede hacer mal —fuera del pozo,
repetidas, dos del mismo artista, demasiadas de un género— y completa lo que
falte con el puntaje determinista. Una propuesta vacía entra con la tanda en
cero y sale con cinco, sin una sola rama distinta.

### Sin claves, el juego se juega entero

Cinco rondas, los mensajes, el perfil del salón completo. El modelo lo hace
mejor; no lo sostiene. No es un respaldo agregado después: es cómo está
construido, y hay pruebas que lo afirman.

---

## Lo que el agente dice en voz alta · `mensajes.py`

Una oración por tanda, arriba de las tarjetas:

> «Estamos entendiendo el gusto del grupo.» · «Rock está creciendo entre las
> elecciones.» · «La energía del salón está subiendo.» · «Esta tanda mezcla lo
> tuyo con lo que está eligiendo el resto.»

Es determinista —sale del perfil medido— así que existe con cero claves. El
modelo puede proponer la suya y **se usa sólo si pasa un chequeo de forma**:
menos de 80 caracteres, una sola oración, y que no nombre ninguna canción ni
artista de la tanda. Nombrarla le adelantaría la respuesta a la persona y
dirigiría el voto, que es justo lo que el barajado de posiciones evita.

El razonamiento interno se guarda en `tandas.porque` y no se muestra nunca.

---

## Que el modelo llegue a tiempo

Un agente que contesta tarde es un agente que no contesta: el respaldo
determinista ya armó la tanda y la buena se descarta.

- **El prefetch arranca al elegir**, no al pedir la ronda siguiente. En el
  momento en que alguien toca una carta, la tanda siguiente empieza a cocinarse;
  la persona todavía va a marcar otras y recién después va a tocar «otras
  cinco». Son diez o veinte segundos de ventaja, gratis.
- **El presupuesto descuenta lo que costó la cola.** Antes eran hasta 2 s de
  semáforo + 8 s de modelo contra una paciencia de 4: llegaba tarde casi
  siempre, y `tandas.guardar` descartaba la tanda **sin un solo log**.
- **`PACIENCIA` son 6 segundos** y perder la carrera ahora se registra. Sin ese
  log no hay forma de saber qué fracción de la noche salió del modelo.

Medido en el evento real: **~2 segundos de media** por tanda del modelo, y 5 de
cada 6 rondas servidas desde el modelo.

---

## El costo no puede crecer

nginx no sirve acá: en un salón todos comparten IP. El freno vive en la **forma
de los datos**: `UNIQUE (participante, ronda)` más una ronda 1 que nunca llama
al modelo significa **máximo cuatro llamadas por teléfono, para siempre**. No es
un contador que haya que reiniciar: es geometría. Recargar, tocar dos veces,
reintentar tras un corte y abrir dos pestañas son el mismo caso y cuestan cero.

Contra el rotado de identidad, `eventos.tope_ia` corta el bucle: alcanzado el
tope, todo pasa a determinista **sin romperse ni mostrar un error**.
