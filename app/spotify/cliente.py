"""El cliente de Spotify. Dos credenciales distintas y no se mezclan.

  TOKEN DE APLICACION (client_credentials) -> BUSCAR. Es anonimo, no actua en
  nombre de nadie y no puede escribir nada.
  TOKEN DE USUARIO (authorization_code + refresh) -> ESCRIBIR en una playlist.
  Actua en nombre de una persona y por eso necesita su consentimiento.

Mezclarlos en un solo cliente termina con un token de usuario viajando en cada
busqueda de cada telefono de la fiesta. Estan separados a proposito.
"""
from __future__ import annotations

import base64
import logging
import time

import httpx

from app.ajustes import ajustes

registro = logging.getLogger("tinker.spotify")

AUTORIZAR = "https://accounts.spotify.com/authorize"
TOKEN = "https://accounts.spotify.com/api/token"
API = "https://api.spotify.com/v1"

# Lo minimo para agregar a una playlist propia. Cada scope de mas es algo que
# la persona nos confia sin que lo necesitemos, asi que hay tres y ninguno
# sobra:
#
#   playlist-modify-public / -private  escribir en la playlist.
#   user-read-private                  saber si la cuenta es Premium o Free.
#   user-modify-playback-state         MANDAR A LA COLA DE REPRODUCCION. Este
#     es el que importa: Spotify bloquea los endpoints de playlist para las
#     apps en modo desarrollo --403 Forbidden-- pero los del reproductor
#     contestan «401 Permissions missing», que es otra cosa: no estan
#     bloqueados, falta el permiso. Y la cola es mejor que la playlist para lo
#     que hace tinker -- la canción elegida va a lo que esta sonando, no a una
#     lista que alguien tiene que poner despues.
#   user-read-playback-state           ver si hay un dispositivo activo.
#     Se agrego el 2026-09-12 por un diagnostico: Spotify devolvia 403 al
#     agregar temas y sin este scope `GET /me` no dice `product`, asi que no
#     habia forma de saber desde el servidor si el problema era la cuenta.
#   streaming + user-read-email        QUE LA PANTALLA SEA EL PARLANTE. Los
#     pide el Web Playback SDK y no hay forma de saltearlos: sin `streaming`
#     el SDK ni siquiera inicializa. Se agregaron el 2026-09-12, cuando quedo
#     claro que Spotify nos dejaba el reproductor y nos cerraba la playlist.
#     ⚠ Agregarlos NO alcanza: una cuenta ya conectada sigue con los scopes
#     viejos hasta que vuelve a autorizar. Por eso `token_reproductor` mira
#     `spotify_cuenta.scopes` y lo dice, en vez de dejar que el SDK falle en
#     el navegador del televisor con un error que nadie va a leer.
SCOPES = (
    "playlist-modify-public playlist-modify-private user-read-private "
    "user-modify-playback-state user-read-playback-state "
    "streaming user-read-email"
)

# Lo que el Web Playback SDK necesita si o si.
SCOPES_REPRODUCTOR = ("streaming", "user-read-email")

# Los articulos que sobran al comparar artistas. No se tocan en la identidad
# --`clave_artista` sigue siendo la de siempre-- porque cambiarla renombraria
# canciones ya guardadas: esto es solo para decidir si dos nombres son el
# mismo artista.
_ARTICULOS = ("los ", "las ", "el ", "la ", "the ")


def _sin_articulo(nombre: str) -> str:
    for articulo in _ARTICULOS:
        if nombre.startswith(articulo):
            return nombre[len(articulo):]
    return nombre


_APP: tuple[str, float] | None = None
_USUARIO: tuple[str, float] | None = None


def configurado() -> bool:
    return ajustes().spotify_disponible


def _basica() -> str:
    cfg = ajustes()
    return base64.b64encode(f"{cfg.spotify_id}:{cfg.spotify_secreto}".encode()).decode()


async def token_app() -> str:
    """Para buscar. Se cachea hasta un minuto antes de vencer."""
    global _APP
    if _APP and time.time() < _APP[1] - 60:
        return _APP[0]
    if not configurado():
        raise RuntimeError("faltan SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET")
    async with httpx.AsyncClient(timeout=10) as cliente:
        respuesta = await cliente.post(
            TOKEN,
            data={"grant_type": "client_credentials"},
            headers={"Authorization": f"Basic {_basica()}"},
        )
        respuesta.raise_for_status()
        datos = respuesta.json()
    _APP = (datos["access_token"], time.time() + int(datos.get("expires_in", 3600)))
    return _APP[0]


async def canjear_codigo(codigo: str, redireccion: str) -> dict:
    """El paso final del consentimiento: codigo -> refresh_token."""
    async with httpx.AsyncClient(timeout=10) as cliente:
        respuesta = await cliente.post(
            TOKEN,
            data={
                "grant_type": "authorization_code",
                "code": codigo,
                "redirect_uri": redireccion,
            },
            headers={"Authorization": f"Basic {_basica()}"},
        )
        respuesta.raise_for_status()
        return respuesta.json()


async def token_usuario(refresh_token: str) -> str:
    """Para escribir. El refresh token no vence; el de acceso, cada hora."""
    global _USUARIO
    if _USUARIO and time.time() < _USUARIO[1] - 60:
        return _USUARIO[0]
    async with httpx.AsyncClient(timeout=10) as cliente:
        respuesta = await cliente.post(
            TOKEN,
            data={"grant_type": "refresh_token", "refresh_token": refresh_token},
            headers={"Authorization": f"Basic {_basica()}"},
        )
        respuesta.raise_for_status()
        datos = respuesta.json()
    _USUARIO = (datos["access_token"], time.time() + int(datos.get("expires_in", 3600)))
    return _USUARIO[0]


def olvidar_token_usuario() -> None:
    """Tras un 401: el proximo pedido renueva. Sin esto, un token vencido en
    memoria hace fallar toda la cola hasta que se reinicie el contenedor."""
    global _USUARIO
    _USUARIO = None


async def quien_soy(acceso: str) -> dict:
    async with httpx.AsyncClient(timeout=10) as cliente:
        respuesta = await cliente.get(f"{API}/me", headers={"Authorization": f"Bearer {acceso}"})
        respuesta.raise_for_status()
        return respuesta.json()


async def buscar_pista(titulo: str, artista: str) -> dict | None:
    """Busca UNA canción concreta y COMPRUEBA que sea esa.

    Spotify siempre devuelve algo; que haya devuelto algo no quiere decir que
    sea esto. Sin la comprobacion, el repertorio se llena de covers de karaoke
    con el mismo titulo.
    """
    from app.catalogo import clave_artista, plano
    from app.descubrimiento.normalizar import limpiar_titulo

    async def _pedir(consulta: str) -> list[dict]:
        async with httpx.AsyncClient(timeout=10) as cliente:
            respuesta = await cliente.get(
                f"{API}/search",
                params={"q": consulta, "type": "track", "limit": 10,
                        "market": ajustes().spotify_mercado},
                headers={"Authorization": f"Bearer {await token_app()}"},
            )
            if respuesta.status_code != 200:
                registro.warning("busqueda %s: %s", respuesta.status_code, respuesta.text[:160])
                return []
            return [i for i in respuesta.json().get("tracks", {}).get("items", []) if i]

    # El titulo se compara LIMPIO de los dos lados, y esto recupero 54
    # canciones de 406: en Spotify el rock argentino esta casi todo como
    # «Matador - Remasterizado 2008», y exigir el titulo exacto lo descartaba
    # entero. `limpiar_titulo` saca justo ese ruido de edicion.
    objetivo_titulo = plano(limpiar_titulo(titulo))
    objetivo_artista = _sin_articulo(clave_artista(artista))

    def calza(item: dict) -> bool:
        artistas = ", ".join(a["name"] for a in item.get("artists", []))
        suyo = _sin_articulo(clave_artista(artistas))
        if plano(limpiar_titulo(item["name"])) != objetivo_titulo:
            return False
        # Igual, o uno empieza con el otro: en Spotify es «Los Enanitos
        # Verdes» y en el repertorio «Enanitos Verdes». El articulo de mas
        # descartaba a media Argentina.
        return (
            suyo == objetivo_artista
            or suyo.startswith(objetivo_artista)
            or objetivo_artista.startswith(suyo)
        )

    consulta = f'track:"{titulo}"'
    if artista.strip():
        consulta += f' artist:"{artista}"'
    for item in await _pedir(consulta):
        if calza(item):
            return item

    # Segundo intento SIN los campos: la busqueda por campos es exacta y falla
    # con un acento de mas o un parentesis. La libre es mas tolerante, y la
    # comprobacion de arriba sigue siendo la que decide -- no se acepta nada
    # que no sea la canción buscada.
    for item in await _pedir(f"{titulo} {artista}".strip()):
        if calza(item):
            return item
    return None


async def playlist_contiene(acceso: str, playlist_id: str) -> set[str]:
    """Los ids que YA estan en la playlist.

    Se consulta antes de agregar porque la playlist es de una persona y puede
    haberla tocado a mano: nuestra tabla sabe lo que mandamos nosotros, no lo
    que hay.
    """
    ids: set[str] = set()
    url = f"{API}/playlists/{playlist_id}/tracks"
    params = {"fields": "items(track(id)),next", "limit": 100}
    async with httpx.AsyncClient(timeout=15) as cliente:
        while url:
            respuesta = await cliente.get(
                url, params=params, headers={"Authorization": f"Bearer {acceso}"}
            )
            respuesta.raise_for_status()
            datos = respuesta.json()
            for fila in datos.get("items", []):
                pista = (fila or {}).get("track") or {}
                if pista.get("id"):
                    ids.add(pista["id"])
            url, params = datos.get("next"), None
    return ids


async def agregar(acceso: str, playlist_id: str, spotify_id: str) -> None:
    """Agrega UNA canción al final de la playlist.

    El error lleva el CUERPO de la respuesta y no solo el codigo: un 403 de
    Spotify sin el mensaje de adentro no se puede diagnosticar, y el 2026-09-12
    se perdieron horas por eso -- «403 Forbidden» a secas puede ser el scope,
    la cuenta o la app, y son tres arreglos distintos.
    """
    async with httpx.AsyncClient(timeout=15) as cliente:
        respuesta = await cliente.post(
            f"{API}/playlists/{playlist_id}/tracks",
            json={"uris": [f"spotify:track:{spotify_id}"]},
            headers={"Authorization": f"Bearer {acceso}"},
        )
        if respuesta.status_code >= 400:
            raise RuntimeError(f"HTTP {respuesta.status_code}: {respuesta.text[:300]}")


async def puede_escribir(acceso: str) -> dict:
    """Que puede y que no puede hacer esta cuenta. Para el panel.

    Prueba lo minimo y no escribe nada: lee la cuenta y su tipo. Si `product`
    viene vacio, falta el scope de lectura y hay que volver a autorizar.
    """
    async with httpx.AsyncClient(timeout=10) as cliente:
        yo = await cliente.get(f"{API}/me", headers={"Authorization": f"Bearer {acceso}"})
        datos = yo.json() if yo.status_code == 200 else {}
    return {
        "cuenta": datos.get("display_name") or datos.get("id"),
        "tipo": datos.get("product"),
        "pais": datos.get("country"),
    }


def id_de_playlist(url: str | None) -> str | None:
    """El id que hay dentro de un enlace de Spotify.

    Acepta lo que la gente pega de verdad: el enlace completo con `?si=…`, el
    URI `spotify:playlist:…` o el id pelado.
    """
    if not url:
        return None
    texto = url.strip()
    if texto.startswith("spotify:playlist:"):
        return texto.split(":")[-1][:40]
    if "open.spotify.com" in texto:
        resto = texto.split("/playlist/", 1)[-1]
        return resto.split("?")[0].split("/")[0][:40] or None
    if 15 <= len(texto) <= 40 and texto.isalnum():
        return texto
    return None


# ── el `state` del consentimiento ─────────────────────────────────────────
#
# El callback de OAuth NO puede estar detras de Authelia: Spotify redirige el
# navegador de la persona y, sin cookie de sesion, el codigo se pierde --y con
# el, la conexion--. Lo que protege una ruta publica de OAuth es el `state`:
# un valor aleatorio que emitimos nosotros, de UN SOLO USO y con vencimiento.
# Sin eso, cualquiera podria llamar al callback con un codigo propio y
# conectar SU cuenta de Spotify a este servidor.

_ESTADOS: dict[str, float] = {}
# Una hora. Quince minutos parecian de sobra y no lo eran: entre generar el
# enlace, leerlo en el telefono y pasar por la pantalla de Spotify, se vencio
# dos veces.
VIDA_ESTADO = 3600.0


def nuevo_estado() -> str:
    import secrets

    ahora = time.time()
    # Se limpian los vencidos de paso: no hace falta un job para diez claves.
    for clave, cuando in list(_ESTADOS.items()):
        if ahora - cuando > VIDA_ESTADO:
            del _ESTADOS[clave]
    estado = secrets.token_urlsafe(24)
    _ESTADOS[estado] = ahora
    return estado


def consumir_estado(estado: str) -> bool:
    """Valida y quema. Un `state` sirve una sola vez."""
    cuando = _ESTADOS.pop(estado or "", None)
    return cuando is not None and (time.time() - cuando) <= VIDA_ESTADO


# ── la cola de reproduccion ───────────────────────────────────────────────
#
# Este es el camino que funciona con una app en modo desarrollo. La playlist y
# la cola son cosas distintas: la playlist es el registro de la noche, la cola
# es lo que va a sonar ahora. Tinker quiere las dos, y de momento solo puede
# tener la segunda.


async def dispositivos(acceso: str) -> list[dict]:
    """Los aparatos donde la cuenta puede reproducir.

    Sin un dispositivo ACTIVO, Spotify rechaza cualquier cosa que se le mande
    a la cola: hace falta que haya musica sonando en algun lado.
    """
    async with httpx.AsyncClient(timeout=10) as cliente:
        respuesta = await cliente.get(
            f"{API}/me/player/devices", headers={"Authorization": f"Bearer {acceso}"}
        )
        if respuesta.status_code != 200:
            return []
        return respuesta.json().get("devices", []) or []


async def en_cola(acceso: str, spotify_id: str) -> None:
    """Manda una canción al final de la cola de lo que se esta escuchando."""
    async with httpx.AsyncClient(timeout=15) as cliente:
        respuesta = await cliente.post(
            f"{API}/me/player/queue",
            params={"uri": f"spotify:track:{spotify_id}"},
            headers={"Authorization": f"Bearer {acceso}"},
        )
        if respuesta.status_code >= 400:
            raise RuntimeError(f"HTTP {respuesta.status_code}: {respuesta.text[:250]}")


async def cola_de_reproduccion(acceso: str) -> dict:
    """Lo que suena Y lo que sigue, en UNA sola llamada.

    Una y no dos --`/me/player` aparte-- porque la app esta en modo desarrollo
    y ya nos contesto 429 QUOTA_EXCEEDED. `currently_playing` viene en la misma
    respuesta que `queue`, asi que pedir el estado por separado seria pagar el
    doble por el mismo dato.

    Devuelve un diccionario vacio y no levanta cuando no hay nada sonando: que
    nadie este escuchando musica no es un error que deba llegar a la pantalla.
    """
    async with httpx.AsyncClient(timeout=10) as cliente:
        respuesta = await cliente.get(
            f"{API}/me/player/queue", headers={"Authorization": f"Bearer {acceso}"}
        )
        if respuesta.status_code == 204 or respuesta.status_code >= 400:
            if respuesta.status_code >= 400:
                registro.info("cola %s: %s", respuesta.status_code, respuesta.text[:160])
            return {}
        return respuesta.json() or {}


async def reproducir(acceso: str, uris: list[str], dispositivo: str | None = None) -> None:
    """Pone a sonar una lista de temas EXPLICITA, sin pasar por ninguna playlist.

    Este es el endpoint que reemplaza lo que Spotify nos bloqueo: acepta hasta
    cien URIs sueltas, asi que el ranking de la noche se reproduce tal cual sin
    que exista una playlist en ningun lado.
    """
    async with httpx.AsyncClient(timeout=15) as cliente:
        respuesta = await cliente.put(
            f"{API}/me/player/play",
            params={"device_id": dispositivo} if dispositivo else None,
            json={"uris": uris[:100]},
            headers={"Authorization": f"Bearer {acceso}"},
        )
        if respuesta.status_code >= 400:
            raise RuntimeError(f"HTTP {respuesta.status_code}: {respuesta.text[:250]}")


async def transferir(acceso: str, dispositivo: str, sonar: bool = True) -> None:
    """Manda el sonido a un dispositivo. El que usa la pantalla al conectarse.

    Spotify contesta 202 y tarda un instante en aplicarlo; eso no es un error
    y por eso solo se levanta a partir de 400.
    """
    async with httpx.AsyncClient(timeout=15) as cliente:
        respuesta = await cliente.put(
            f"{API}/me/player",
            json={"device_ids": [dispositivo], "play": sonar},
            headers={"Authorization": f"Bearer {acceso}"},
        )
        if respuesta.status_code >= 400:
            raise RuntimeError(f"HTTP {respuesta.status_code}: {respuesta.text[:250]}")


async def estado_reproduccion(acceso: str) -> dict:
    """Que esta sonando y donde. Para el panel."""
    async with httpx.AsyncClient(timeout=10) as cliente:
        respuesta = await cliente.get(
            f"{API}/me/player", headers={"Authorization": f"Bearer {acceso}"}
        )
        if respuesta.status_code == 204:
            return {"sonando": False, "motivo": "no hay nada reproduciéndose"}
        if respuesta.status_code != 200:
            return {"sonando": False, "motivo": f"HTTP {respuesta.status_code}"}
        datos = respuesta.json()
        pista = datos.get("item") or {}
        return {
            "sonando": bool(datos.get("is_playing")),
            "dispositivo": (datos.get("device") or {}).get("name"),
            "tema": pista.get("name"),
            "artista": ", ".join(a["name"] for a in pista.get("artists", [])),
        }
