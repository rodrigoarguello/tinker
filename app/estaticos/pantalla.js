/* pantalla.js — el televisor del salón.
 *
 * Vive del WebSocket: cuando alguien pide una canción, el servidor la empuja
 * y la tarjeta entra. El panorama completo se vuelve a pedir con calma detrás
 * de cada empujón, porque el ranking sí exige contar de nuevo.
 *
 * Esta pantalla no tiene interacción: se abre en un monitor y se la deja. Por
 * eso todo lo que puede fallar --el socket, la red del lugar-- se reintenta
 * solo y se muestra abajo a la derecha.
 */
(() => {
  const partes = location.pathname.split('/').filter(Boolean);
  const slug = partes[0];
  const $ = (id) => document.getElementById(id);

  const escapar = (texto) => String(texto ?? '').replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));

  // El mismo tono por artista que usa el teléfono: quien eligió una canción
  // ahí la reconoce acá por el color antes de leer el título.
  // Una PALETA de tonos elegidos, no los 360 grados sueltos. Con el matiz
  // libre salian verdes acidos y amarillos que sobre el fondo oscuro chillan y
  // no pegan con el cobalto de la marca. Estos catorce conviven.
  const TONOS = [210, 224, 250, 268, 286, 305, 322, 338, 352, 8, 20, 32, 168, 188];
  const tono = (texto) => {
    let acumulado = 0;
    for (const letra of String(texto || '?')) acumulado = (acumulado * 31 + letra.charCodeAt(0)) % 4093;
    return TONOS[acumulado % TONOS.length];
  };

  const disco = (cancion) => {
    if (cancion.imagen) {
      return `<span class="disco"><img src="${escapar(cancion.imagen)}" alt=""></span>`;
    }
    const inicial = (cancion.titulo || '?').trim().charAt(0).toUpperCase();
    return `<span class="disco" data-tono="${tono(cancion.artista)}">${escapar(inicial)}</span>`;
  };

  // El tono va por el CSSOM y no por un atributo `style`: la CSP de este host
  // descarta los estilos inline en silencio. Ver participante.js.
  const pintarTonos = (raiz) => {
    for (const nodo of raiz.querySelectorAll('[data-tono]')) {
      nodo.style.setProperty('--tono', nodo.dataset.tono);
    }
  };

  // Ocho y no diez: con la tarjeta que irrumpe abajo, las dos ultimas filas
  // quedaban tapadas justo cuando el salon las mira.
  const TOPE_TOP = 8;

  $('qr').src = `/api/e/${slug}/qr.svg`;
  $('direccion').textContent = `${location.host}/${slug}`;

  /* ── pintar ───────────────────────────────────────────────────────── */

  const pintarTotales = (totales) => {
    $('c-personas').textContent = totales.personas;
    $('c-pedidos').textContent = totales.pedidos;
    $('c-canciones').textContent = totales.canciones;
  };

  let topAnterior = [];
  const pintarTop = (ranking) => {
    $('top').innerHTML = ranking.map((c, i) => `
      <li data-id="${escapar(c.id)}">
        <span class="puesto">${i + 1}</span>
        ${disco(c)}
        <span class="que">${escapar(c.titulo)}<span class="quien"> · ${escapar(c.artista)}</span></span>
        <span class="votos">${c.votos}</span>
      </li>`).join('');

    // La que subió de votos destella. Es como se entera el salón de que su
    // elección llegó, sin que nadie tenga que estar mirando el número.
    const antes = new Map(topAnterior.map((c) => [c.id, c.votos]));
    for (const fila of $('top').querySelectorAll('li')) {
      const id = fila.dataset.id;
      const c = ranking.find((x) => x.id === id);
      if (c && antes.has(id) && c.votos > antes.get(id)) fila.classList.add('destella');
    }
    topAnterior = ranking;
    pintarTonos($('top'));
  };

  const pintarArtistas = (artistas) => {
    $('artistas').innerHTML = artistas.map((a, i) => `
      <li>
        <span class="puesto">${i + 1}</span>
        ${disco({ titulo: a.artista, artista: a.artista })}
        <span class="que">${escapar(a.artista)}</span>
        <span class="votos">${a.votos}</span>
      </li>`).join('');
    pintarTonos($('artistas'));
  };

  /* ── sonando ahora ────────────────────────────────────────────────── */

  // El círculo que se cierra. El resto de la pantalla cuenta lo que la gente
  // eligió; esto muestra que eso es lo que SUENA. Si no hay nada sonando, la
  // caja no aparece: un reproductor vacío en un televisor es peor que ninguno.
  const pintarSonando = (estado) => {
    const caja = $('caja-sonando');
    const suena = estado && estado.sonando;
    caja.classList.toggle('oculto', !suena);
    if (!suena) return;

    const disco = $('sonando-disco');
    if (suena.imagen) {
      disco.innerHTML = `<img src="${escapar(suena.imagen)}" alt="">`;
      disco.removeAttribute('data-tono');
      disco.style.removeProperty('--tono');
    } else {
      disco.textContent = (suena.titulo || '?').trim().charAt(0).toUpperCase();
      disco.style.setProperty('--tono', tono(suena.artista));
    }
    $('sonando-titulo').textContent = suena.titulo;
    $('sonando-artista').textContent = suena.artista;
    // El voto se nombra solo cuando lo hay: los temas que Spotify encadena
    // solo no los eligió nadie y decir «0 personas» sería mentir al revés.
    $('sonando-votos').textContent = suena.votos === 1
      ? 'La eligió 1 persona'
      : (suena.votos > 1 ? `La eligieron ${suena.votos} personas` : '');

    const siguen = (estado.siguen || []).filter(Boolean);
    $('siguen').classList.toggle('oculto', siguen.length === 0);
    $('siguen').innerHTML = siguen.map((c, i) => `
      <li>
        <span class="orden">${i + 1}</span>
        <span class="que"><b>${escapar(c.titulo)}</b> · ${escapar(c.artista)}</span>
      </li>`).join('');
  };

  /* ── el parlante ──────────────────────────────────────────────────────
   *
   * Spotify nos cerró todos los endpoints de playlist y nos dejó abiertos los
   * del reproductor, así que la pantalla deja de mirar la música desde afuera
   * y pasa a ser el aparato que la toca: un dispositivo de Spotify Connect
   * llamado «Tinker — pantalla».
   *
   * EL TOKEN NO VIVE EN ESTA PÁGINA. Se pide a una ruta bajo /api/admin/, que
   * el proxy sólo deja pasar con sesión de Authelia. Quien puso la pantalla en
   * el televisor lo recibe; cualquiera que abra la misma URL recibe 401, no se
   * entera de nada y ve la pantalla de siempre.
   */

  let reproductor = null;
  let dispositivo = null;

  const traerToken = async () => {
    const respuesta = await fetch('/api/admin/spotify/token-reproductor');
    if (!respuesta.ok) return { error: (await respuesta.json().catch(() => ({}))) };
    return respuesta.json();
  };

  const nota = (texto) => {
    $('nota-parlante').textContent = texto;
    $('nota-parlante').classList.toggle('oculto', !texto);
  };

  const prepararParlante = async () => {
    const datos = await traerToken().catch(() => null);
    if (!datos) return;
    if (datos.error) {
      // Sin sesión no se dice nada: es el caso normal de cualquier persona que
      // abra la pantalla. Los permisos viejos SÍ se avisan, porque los arregla
      // una persona volviendo a conectar la cuenta.
      const detalle = datos.error.detail || datos.error;
      if (detalle && detalle.error === 'faltan_permisos') {
        $('caja-sonando').classList.remove('oculto');
        nota('Para que suene desde acá hay que volver a conectar Spotify en /admin: la cuenta se autorizó sin el permiso de reproducción.');
      }
      return;
    }

    window.onSpotifyWebPlaybackSDKReady = () => {
      reproductor = new Spotify.Player({
        name: 'Tinker — pantalla',
        // El SDK lo vuelve a llamar solo cuando el token vence, a la hora.
        getOAuthToken: (entregar) => {
          traerToken().then((d) => { if (d && d.token) entregar(d.token); }).catch(() => {});
        },
        volume: 0.85,
      });

      reproductor.addListener('ready', ({ device_id }) => {
        dispositivo = device_id;
        $('caja-sonando').classList.remove('oculto');
        $('parlante').classList.remove('oculto');
        nota('');
      });
      reproductor.addListener('not_ready', () => { dispositivo = null; });
      // Los tres errores que sí importan, dichos en castellano: el resto del
      // salón no puede depender de que alguien mire la consola.
      reproductor.addListener('authentication_error', () => nota('Spotify rechazó la sesión. Volvé a conectar la cuenta en /admin.'));
      reproductor.addListener('account_error', () => nota('Reproducir desde el navegador necesita Spotify Premium.'));
      reproductor.addListener('initialization_error', ({ message }) => nota(`No se pudo iniciar el reproductor: ${message}`));

      reproductor.connect();
    };

    const guion = document.createElement('script');
    guion.src = 'https://sdk.scdn.co/spotify-player.js';
    guion.async = true;
    document.head.appendChild(guion);
  };

  // El clic no es decorativo: ningún navegador deja que una página empiece a
  // sonar sola, y `activateElement` es la forma que da el SDK de gastar ese
  // gesto. Sin él, el dispositivo aparece en Spotify y no emite audio.
  $('parlante').addEventListener('click', async () => {
    if (!reproductor || !dispositivo) return;
    const boton = $('parlante');
    boton.textContent = 'Conectando…';
    try {
      if (reproductor.activateElement) await reproductor.activateElement();
      const respuesta = await fetch('/api/admin/spotify/dispositivo', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ dispositivo, sonar: true }),
      });
      if (!respuesta.ok) throw new Error(await respuesta.text());
      boton.classList.add('sonando-aca');
      boton.textContent = 'Sonando en esta pantalla';
      nota('');
      setTimeout(traerSonando, 1500);
    } catch (error) {
      boton.textContent = 'Sonar en esta pantalla';
      nota('No se pudo mandar el sonido acá. Probá de nuevo.');
    }
  });

  /* ── el cerebro del público ───────────────────────────────────────── */

  // Cómo se nombra cada género en la pantalla. El código usa etiquetas cortas
  // porque son claves; un televisor a cuatro metros necesita otra cosa.
  const NOMBRES = {
    '80-90': 'Los 80 y 90', electronica: 'Electrónica', romantica: 'Románticas',
    paraguaya: 'Paraguaya', brasilera: 'Brasilera', ranchera: 'Ranchera',
    reggaeton: 'Reggaetón', otros: 'Otras',
  };
  const nombrar = (g) => NOMBRES[g] || g.charAt(0).toUpperCase() + g.slice(1);

  let generoLider = null;

  const pintarCerebro = (perfil) => {
    if (!perfil || !perfil.generos || !perfil.generos.length) return;

    $('cerebro-generos').innerHTML = perfil.generos.slice(0, 5).map((g) => `
      <div class="genero">
        <span class="que">${escapar(nombrar(g.genero))}</span>
        <div class="riel"><i data-peso="${g.peso}"></i></div>
      </div>`).join('');
    // CSSOM y no `style=`: la CSP es `style-src 'self'` y descarta los estilos
    // en línea EN SILENCIO. Ya costó una tarde, con todos los discos del mismo
    // color y ningún error en la consola.
    for (const barra of $('cerebro-generos').querySelectorAll('.riel > i')) {
      barra.style.setProperty('width', `${Math.round(barra.dataset.peso * 100)}%`);
    }

    // Un medidor sin dato se esconde entero. Una barra en cero se lee como
    // "la energía del salón es cero", que es una afirmación, y no la tenemos:
    // lo que no sabemos no se dibuja. `mainstream` es el caso real -- Spotify
    // no manda `popularity` a las apps en modo Development.
    const medir = (id, valor) => {
      const barra = $(id);
      const caja = barra.closest('.medidor');
      const hay = valor !== null && valor !== undefined;
      caja.classList.toggle('oculto', !hay);
      if (hay) barra.style.setProperty('width', `${Math.round(valor * 100)}%`);
    };
    medir('cerebro-energia', perfil.energia);
    medir('cerebro-mainstream', perfil.mainstream);

    // La línea de abajo dice lo último que aprendió, en castellano. Si el
    // género líder cambió, ESO es la noticia: es el momento en que el salón
    // giró, y es lo que la gente mira.
    const lider = perfil.generos[0];
    const personas = perfil.personas || 0;
    let texto;
    if (personas <= 1) {
      texto = 'Con una sola persona todavía no hay gusto del salón.';
    } else if (generoLider && lider.genero !== generoLider) {
      texto = `El salón giró hacia ${nombrar(lider.genero).toLowerCase()}.`;
    } else {
      texto = `${nombrar(lider.genero)} manda entre ${personas} personas `
            + `y ${perfil.elecciones} elecciones.`;
    }
    if ($('cerebro-aprendido').textContent !== texto) {
      $('cerebro-aprendido').textContent = texto;
      $('cerebro-aprendido').classList.remove('destella');
      void $('cerebro-aprendido').offsetWidth;
      $('cerebro-aprendido').classList.add('destella');
    }
    generoLider = lider.genero;
  };

  const pintarTodo = (datos) => {
    $('nombre-evento').textContent = datos.evento.nombre;
    $('lugar').textContent = datos.evento.lugar || '';
    document.title = `${datos.evento.nombre} · pantalla`;
    pintarTotales(datos.totales);
    pintarTop(datos.ranking.slice(0, TOPE_TOP));
    pintarArtistas(datos.artistas);
    pintarCerebro(datos.perfil);
  };

  /* ── la tarjeta que irrumpe ───────────────────────────────────────── */

  const cola = [];
  let mostrando = false;

  const siguiente = () => {
    if (mostrando || cola.length === 0) return;
    mostrando = true;
    const { tipo, cancion } = cola.shift();
    $('rotulo').textContent = tipo === 'nueva' ? 'Nueva elección' : '¡Otro voto!';
    $('viva-titulo').textContent = cancion.titulo;
    $('viva-artista').textContent = cancion.artista;
    $('viva-cuantos').textContent = cancion.votos === 1
      ? 'La eligió una persona'
      : `Ya la eligieron ${cancion.votos} personas`;
    const tarjeta = $('tarjeta');
    tarjeta.classList.add('entra');
    setTimeout(() => {
      tarjeta.classList.remove('entra');
      // El tiempo de salida tiene que coincidir con la transición del CSS:
      // sacarla antes deja la tarjeta siguiente pisando a la que se va.
      setTimeout(() => { mostrando = false; siguiente(); }, 700);
    }, 4200);
  };

  const encolar = (mensaje) => {
    // Con mucha gente entran ráfagas. Más de seis en cola quiere decir que
    // nadie va a llegar a leerlas: se quedan las últimas, que son las que la
    // gente del salón acaba de tocar.
    cola.push(mensaje);
    if (cola.length > 6) cola.splice(0, cola.length - 6);
    siguiente();
  };

  /* ── rotación de escenas ──────────────────────────────────────────── */

  let escena = 0;
  setInterval(() => {
    escena = (escena + 1) % 3;
    $('escena-top').classList.toggle('oculto', escena !== 0);
    $('escena-artistas').classList.toggle('oculto', escena !== 1);
    $('escena-cerebro').classList.toggle('oculto', escena !== 2);
  }, 14000);

  /* ── datos ────────────────────────────────────────────────────────── */

  const traerPanorama = async () => {
    const respuesta = await fetch(`/api/e/${slug}/panorama`);
    if (respuesta.ok) pintarTodo(await respuesta.json());
  };

  // Una vez al abrir. Después no se vuelve a pedir: el job lo empuja por el
  // socket cuando cambia, y así N televisores no son N llamadas a Spotify.
  const traerSonando = async () => {
    const respuesta = await fetch(`/api/e/${slug}/sonando`);
    if (respuesta.ok) pintarSonando(await respuesta.json());
  };

  let relojRefresco = null;
  const refrescarConCalma = () => {
    clearTimeout(relojRefresco);
    relojRefresco = setTimeout(traerPanorama, 1500);
  };

  const conexion = (viva) => {
    $('pulso').classList.toggle('frio', !viva);
    $('conexion').textContent = viva ? 'en vivo' : 'reconectando…';
  };

  const conectar = () => {
    const socket = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/${slug}`);
    socket.addEventListener('open', () => conexion(true));
    socket.addEventListener('message', (e) => {
      const mensaje = JSON.parse(e.data);
      if (mensaje.tipo === 'panorama') { pintarTodo(mensaje); return; }
      if (mensaje.tipo === 'latido') return;
      if (mensaje.tipo === 'estado') { traerPanorama(); return; }
      // El perfil llega con CADA elección, no cada quince segundos: que las
      // barras se muevan en el momento en que alguien toca una tarjeta es lo
      // que hace que el agente se vea trabajar.
      if (mensaje.tipo === 'perfil') { pintarCerebro(mensaje.perfil); return; }
      if (mensaje.tipo === 'sonando') { pintarSonando(mensaje); return; }
      if (mensaje.totales) pintarTotales(mensaje.totales);
      if (mensaje.tipo === 'nueva' || mensaje.tipo === 'voto') encolar(mensaje);
      refrescarConCalma();
    });
    socket.addEventListener('close', () => { conexion(false); setTimeout(conectar, 3000); });
    socket.addEventListener('error', () => socket.close());
  };

  traerPanorama().catch(() => {});
  traerSonando().catch(() => {});
  prepararParlante().catch(() => {});
  conectar();
})();
