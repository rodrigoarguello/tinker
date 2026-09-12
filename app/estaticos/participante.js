/* participante.js — el juego, en el teléfono de la gente.
 *
 * Cinco tandas de cinco canciones. Se toca una de cada tanda y la siguiente
 * se arma con lo que se eligió. Sin teclado, sin formulario, sin login.
 *
 * La identidad es un UUID en localStorage: sirve para recordarle a esta
 * persona lo que ya eligió y para que la base rechace el mismo voto dos
 * veces. No identifica a nadie y no se comparte con nadie.
 *
 * La verdad vive en el servidor, no acá: `entrar` devuelve el estado completo
 * --ronda en curso, elegidas, las cinco tarjetas-- así que recargar la página
 * en medio de una fiesta no pierde nada. localStorage guarda una sola cosa, y
 * es el UUID.
 */
(() => {
  const slug = location.pathname.split('/').filter(Boolean)[0];
  const $ = (id) => document.getElementById(id);

  const dispositivo = (() => {
    const clave = 'tinker.dispositivo';
    let valor = null;
    try { valor = localStorage.getItem(clave); } catch { /* modo privado */ }
    if (!valor) {
      valor = (crypto.randomUUID ? crypto.randomUUID()
                                 : String(Date.now()) + Math.random().toString(16).slice(2));
      try { localStorage.setItem(clave, valor); } catch { /* se pierde al cerrar */ }
    }
    return valor;
  })();

  let evento = null;
  let juego = null;
  let eligiendo = false;

  /* ── servidor ─────────────────────────────────────────────────────── */

  const pedir = async (ruta, opciones = {}) => {
    const respuesta = await fetch(`/api/e/${slug}${ruta}`, {
      headers: { 'Content-Type': 'application/json' },
      ...opciones,
    });
    const datos = await respuesta.json().catch(() => ({}));
    if (!respuesta.ok) {
      throw Object.assign(new Error('falló'), { datos: datos.detail || datos, estado: respuesta.status });
    }
    return datos;
  };

  /* ── piezas ───────────────────────────────────────────────────────── */

  const escapar = (texto) => String(texto ?? '').replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));

  // El tono del disco sale del ARTISTA, no del azar: así el mismo artista es
  // siempre del mismo color y la pantalla tiene memoria visual. Con carátula
  // de Spotify esto queda de respaldo.
  // Una PALETA de tonos elegidos, no los 360 grados sueltos. Con el matiz
  // libre salian verdes acidos y amarillos que sobre el fondo oscuro chillan y
  // no pegan con el cobalto de la marca. Estos catorce conviven.
  const TONOS = [210, 224, 250, 268, 286, 305, 322, 338, 352, 8, 20, 32, 168, 188];
  const tono = (texto) => {
    let acumulado = 0;
    for (const letra of String(texto || '?')) acumulado = (acumulado * 31 + letra.charCodeAt(0)) % 4093;
    return TONOS[acumulado % TONOS.length];
  };

  // OJO: el tono NO puede ir en un atributo `style` del HTML. La CSP de este
  // host es `style-src 'self'` y el navegador descarta los estilos inline EN
  // SILENCIO -- se descubrio con los cinco discos del mismo azul. Por el CSSOM
  // (`setProperty`) sí pasa: CSP no gobierna las propiedades asignadas por JS.
  const disco = (cancion, clase = 'disco') => {
    if (cancion.imagen) {
      return `<span class="${clase}"><img src="${escapar(cancion.imagen)}" alt="" loading="lazy"></span>`;
    }
    const inicial = (cancion.titulo || '?').trim().charAt(0).toUpperCase();
    return `<span class="${clase}" data-tono="${tono(cancion.artista)}">${escapar(inicial)}</span>`;
  };

  // Se llama despues de cada innerHTML que haya puesto discos.
  const pintarTonos = (raiz) => {
    for (const nodo of raiz.querySelectorAll('[data-tono]')) {
      nodo.style.setProperty('--tono', nodo.dataset.tono);
    }
  };

  let relojBrindis = null;
  const brindar = (texto) => {
    const globo = $('brindis');
    globo.textContent = texto;
    globo.classList.add('visible');
    clearTimeout(relojBrindis);
    relojBrindis = setTimeout(() => globo.classList.remove('visible'), 1900);
  };

  const mostrar = (cual) => {
    for (const seccion of ['juego', 'cierre', 'cerrado']) {
      $(seccion).classList.toggle('oculto', seccion !== cual);
    }
  };

  /* ── pintar ───────────────────────────────────────────────────────── */

  const pintarCabecera = () => {
    $('evento-nombre').textContent = evento.nombre;
    $('cuantas').textContent = juego.elegidas.length;

    $('elegidas').innerHTML = '';
    for (const cancion of juego.elegidas) {
      const chip = document.createElement('div');
      chip.className = 'chip';
      chip.innerHTML = `${disco(cancion)}<b>${escapar(cancion.titulo)}</b>
                        <button aria-label="Quitar ${escapar(cancion.titulo)}">×</button>`;
      chip.querySelector('button').addEventListener('click', () => quitar(cancion));
      $('elegidas').appendChild(chip);
      pintarTonos(chip);
    }

    const ronda = juego.ronda || evento.rondas;
    $('puntos').innerHTML = '';
    const cuantosPuntos = Math.min(juego.rondas, 10);
    for (let i = 1; i <= cuantosPuntos; i++) {
      const punto = document.createElement('span');
      const pasada = i < ronda;
      punto.className = 'punto' + (pasada ? ' hecho'
        : (i === ronda && juego.estado === 'jugando' ? ' actual' : ''));
      $('puntos').appendChild(punto);
    }
    $('ronda-texto').textContent = juego.estado === 'completado'
      ? 'Terminaste'
      : `Tanda ${ronda} de ${juego.rondas}`;
  };

  const yaElegida = (id) => juego.elegidas.some((c) => c.id === id);

  // Lo único que el agente dice en voz alta. Va en el subtítulo que ya existía
  // como texto fijo: es el lugar donde la vista ya tenía lugar para una línea,
  // así que no hace falta mover nada de la pantalla.
  const PIE_POR_DEFECTO = 'Tocá todas las que quieras escuchar hoy.';

  const pintarMensaje = (tanda) => {
    const pie = $('pregunta-pie');
    if (!pie) return;
    const texto = (tanda && tanda.mensaje) || PIE_POR_DEFECTO;
    if (pie.textContent === texto) return;   // sin parpadeo si no cambió
    pie.textContent = texto;
    pie.classList.remove('cambia');
    void pie.offsetWidth;                    // reinicia la animación
    pie.classList.add('cambia');
  };

  const pintarTanda = (tanda) => {
    const caja = $('tanda');
    caja.classList.remove('resolviendo');
    caja.innerHTML = '';
    $('preparando').classList.add('oculto');
    pintarMensaje(tanda);

    for (const cancion of tanda.canciones) {
      const marcada = yaElegida(cancion.id);
      const tarjeta = document.createElement('button');
      tarjeta.className = 'tarjeta' + (marcada ? ' marcada' : '');
      tarjeta.type = 'button';
      tarjeta.innerHTML = `
        ${disco(cancion)}
        <span class="texto">
          <span class="titulo">${escapar(cancion.titulo)}</span>
          <span class="artista">${escapar(cancion.artista)}</span>
        </span>
        <span class="elegir">${marcada ? '✓ Elegida' : 'Elegir'}</span>`;
      tarjeta.addEventListener('click', () => elegir(cancion, tanda.ronda, tarjeta));
      caja.appendChild(tarjeta);
      pintarTonos(tarjeta);
    }
  };

  const pintarEsqueleto = () => {
    const caja = $('tanda');
    caja.classList.remove('resolviendo');
    caja.innerHTML = '';
    for (let i = 0; i < 5; i++) {
      const hueso = document.createElement('div');
      hueso.className = 'esqueleto';
      hueso.innerHTML = '<i></i><div><u></u><s></s></div>';
      caja.appendChild(hueso);
    }
    $('preparando').classList.remove('oculto');
  };

  const pintarCierre = async () => {
    $('cuantas-final').textContent = juego.elegidas.length;
    $('evento-final').textContent = evento.nombre;
    $('mis-cinco').innerHTML = juego.elegidas.map((c) => `
      <div class="tarjeta">
        ${disco(c)}
        <span class="texto">
          <span class="titulo">${escapar(c.titulo)}</span>
          <span class="artista">${escapar(c.artista)}</span>
        </span>
      </div>`).join('');
    pintarTonos($('mis-cinco'));

    // Y lo que va eligiendo el resto: la parte que hace que alguien se quede
    // mirando la pantalla en vez de guardar el teléfono.
    try {
      const datos = await pedir('/panorama');
      $('top-evento').innerHTML = datos.ranking.slice(0, 5).map((c, i) => `
        <li>
          <span class="puesto">${i + 1}</span>
          <span>${escapar(c.titulo)} <span class="quien">· ${escapar(c.artista)}</span></span>
          <span class="votos">${c.votos}</span>
        </li>`).join('');
    } catch { $('top-evento').innerHTML = ''; }
  };

  /* ── el juego ─────────────────────────────────────────────────────── */

  const pintar = async () => {
    pintarCabecera();
    if (!evento.abierto) { pintarCerrado(); return; }
    if (juego.estado === 'completado') { mostrar('cierre'); await pintarCierre(); return; }
    mostrar('juego');
    $('pie-otras').classList.toggle('oculto', !juego.tanda);
    if (juego.tanda) pintarTanda(juego.tanda);
    else { pintarEsqueleto(); esperarTanda(juego.ronda); }
  };

  const pintarCerrado = () => {
    mostrar('cerrado');
    const enlace = $('playlist-cerrado');
    if (evento.playlist_url) {
      enlace.href = evento.playlist_url;
      enlace.classList.remove('oculto');
    } else {
      enlace.classList.add('oculto');
      $('texto-cerrado').textContent =
        'La recepción de canciones terminó. En un rato publicamos la playlist acá mismo.';
    }
  };

  // Marcar una NO cambia la tanda: de cinco tarjetas te pueden gustar tres, y
  // la primera versión te robaba las otras dos al primer toque. Si ya estaba
  // marcada, el toque la desmarca.
  const elegir = async (cancion, ronda, tarjeta) => {
    if (eligiendo) return;
    eligiendo = true;
    const estaba = yaElegida(cancion.id);
    // La marca se pinta ANTES de la respuesta: el dedo no espera a la red.
    tarjeta.classList.toggle('marcada', !estaba);
    const rotulo = tarjeta.querySelector('.elegir');
    if (rotulo) rotulo.textContent = estaba ? 'Elegir' : '✓ Elegida';
    try {
      const datos = estaba
        ? await pedir(`/elegidas/${encodeURIComponent(cancion.id)}?dispositivo=${encodeURIComponent(dispositivo)}`,
                      { method: 'DELETE' })
        : await pedir('/elegir', {
            method: 'POST',
            body: JSON.stringify({ dispositivo, ronda, cancion: cancion.id }),
          });
      evento = datos.evento;
      juego = datos.juego;
      pintarCabecera();
      brindar(estaba ? 'La saqué' : 'Elegida');
    } catch (error) {
      const codigo = error.datos && error.datos.error;
      if (codigo === 'evento_cerrado') { brindar('La recepción cerró'); await recargar(); }
      else { brindar('No se pudo, probá de nuevo'); await recargar(); }
    } finally {
      eligiendo = false;
    }
  };

  // «Ninguna me gusta»: rechazo explícito de las cinco. Vale mucho más que
  // cinco no-elegidas sueltas, y el servidor lo usa para cambiar de rumbo.
  const otrasCinco = async () => {
    if (eligiendo || !juego || !juego.tanda) return;
    eligiendo = true;
    $('tanda').classList.add('resolviendo');
    $('otras').disabled = true;
    try {
      const datos = await pedir('/rechazar', {
        method: 'POST',
        body: JSON.stringify({ dispositivo, ronda: juego.tanda.ronda }),
      });
      evento = datos.evento;
      juego = datos.juego;
      brindar('Buscamos otras');
      await pintar();
    } catch {
      brindar('No se pudo, probá de nuevo');
      await recargar();
    } finally {
      eligiendo = false;
      $('otras').disabled = false;
    }
  };

  // Otras cinco SIN rechazar: las que no marcaste quedan como señal débil.
  const siguientes = async () => {
    if (eligiendo) return;
    eligiendo = true;
    $('tanda').classList.add('resolviendo');
    try {
      const datos = await pedir('/avanzar', {
        method: 'POST',
        body: JSON.stringify({ dispositivo }),
      });
      evento = datos.evento;
      juego = datos.juego;
      await pintar();
    } catch { brindar('No se pudo'); await recargar(); }
    finally { eligiendo = false; }
  };

  // «Dejame elegir otras más», desde la pantalla de cierre.
  const elegirMas = async () => {
    try {
      const datos = await pedir('/seguir', {
        method: 'POST',
        body: JSON.stringify({ dispositivo }),
      });
      evento = datos.evento;
      juego = datos.juego;
      brindar('Cinco más');
      await pintar();
    } catch (error) {
      brindar((error.datos && error.datos.error) === 'demasiadas_rondas'
        ? 'Ya elegiste muchísimas'
        : 'No se pudo');
    }
  };

  /* ── el buscador de grupos ─────────────────────────────────────────── */

  let relojGrupo = null;
  const buscarGrupo = (texto) => {
    clearTimeout(relojGrupo);
    if (texto.trim().length < 2) {
      $('temas-grupo').innerHTML = '';
      $('caja-grupo').classList.add('oculto');
      return;
    }
    $('buscando-grupo').classList.remove('oculto');
    relojGrupo = setTimeout(async () => {
      try {
        const datos = await pedir(`/grupo?q=${encodeURIComponent(texto)}`);
        pintarTemasDelGrupo(datos.temas, datos.parecidos || [], texto);
      } catch {
        $('temas-grupo').innerHTML = '<li class="aviso">No se pudo buscar. Probá de nuevo.</li>';
      } finally { $('buscando-grupo').classList.add('oculto'); }
    }, 450);
  };

  const tarjetaBuscada = (tema) => {
    const marcada = juego.elegidas.some(
      (c) => c.titulo === tema.titulo && c.artista === tema.artista);
    const fila = document.createElement('li');
    fila.innerHTML = `
      <button class="tarjeta${marcada ? ' marcada' : ''}" type="button">
        ${disco(tema)}
        <span class="texto">
          <span class="titulo">${escapar(tema.titulo)}</span>
          <span class="artista">${escapar(tema.artista)}</span>
        </span>
        <span class="elegir">${marcada ? '✓ Elegida' : 'Elegir'}</span>
      </button>`;
    const boton = fila.querySelector('button');
    boton.addEventListener('click', () => elegirBuscada(tema, boton));
    pintarTonos(fila);
    return fila;
  };

  const pintarTemasDelGrupo = (temas, parecidos, buscado) => {
    const caja = $('temas-grupo');
    caja.innerHTML = '';
    // El panel se muestra recién con resultados, y con el nombre de lo que se
    // buscó arriba: es lo que la persona está mirando, no un apéndice.
    $('caja-grupo').classList.remove('oculto');
    $('titulo-grupo').textContent = temas.length
      ? `Temas de ${buscado}`
      : 'No encontré ese grupo';
    if (!temas.length && !parecidos.length) {
      caja.innerHTML = `<li class="aviso">No encontré temas de "${escapar(buscado)}". Probá con el nombre completo.</li>`;
      return;
    }
    for (const tema of temas) caja.appendChild(tarjetaBuscada(tema));
    // Lo que propone el modelo cuando el grupo no aparece o trae poco: gente
    // parecida. Va abajo y aclarado, para que no se confunda con lo pedido.
    if (parecidos.length) {
      const rotulo = document.createElement('li');
      rotulo.className = 'aviso';
      rotulo.textContent = temas.length
        ? 'Si te gusta ese grupo, quizá también:'
        : `No encontré "${buscado}". Quizá te guste:`;
      caja.appendChild(rotulo);
      for (const tema of parecidos) caja.appendChild(tarjetaBuscada(tema));
    }
  };

  const elegirBuscada = async (tema, boton) => {
    if (eligiendo) return;
    eligiendo = true;
    boton.classList.add('marcada');
    boton.querySelector('.elegir').textContent = '✓ Elegida';
    try {
      const datos = await pedir('/elegir-buscada', {
        method: 'POST',
        body: JSON.stringify({ dispositivo, titulo: tema.titulo, artista: tema.artista }),
      });
      evento = datos.evento;
      juego = datos.juego;
      pintarCabecera();
      brindar('Elegida');
    } catch {
      boton.classList.remove('marcada');
      boton.querySelector('.elegir').textContent = 'Elegir';
      brindar('No se pudo');
    } finally { eligiendo = false; }
  };

  const quitar = async (cancion) => {
    try {
      const datos = await pedir(
        `/elegidas/${encodeURIComponent(cancion.id)}?dispositivo=${encodeURIComponent(dispositivo)}`,
        { method: 'DELETE' },
      );
      evento = datos.evento;
      juego = datos.juego;
      brindar('Podés elegir otra');
      await pintar();
    } catch { brindar('No se pudo quitar'); }
  };

  // El poll de la tanda siguiente. El servidor dice cuándo volver a preguntar,
  // y si se le acaba la paciencia arma una tanda él mismo: acá nunca se
  // espera para siempre.
  const esperarTanda = async (ronda, intentos = 0) => {
    if (intentos > 20) { $('preparando').textContent = 'Tocá para reintentar'; return; }
    try {
      const datos = await pedir(`/tanda?dispositivo=${encodeURIComponent(dispositivo)}&ronda=${ronda}`);
      if (datos.lista) {
        juego.tanda = { ronda: datos.ronda, canciones: datos.canciones };
        pintarTanda(juego.tanda);
        return;
      }
      setTimeout(() => esperarTanda(ronda, intentos + 1), datos.reintentar_en_ms || 500);
    } catch {
      setTimeout(() => esperarTanda(ronda, intentos + 1), 1200);
    }
  };

  /* ── arranque ─────────────────────────────────────────────────────── */

  const recargar = async () => {
    const datos = await pedir('/entrar', {
      method: 'POST',
      body: JSON.stringify({ dispositivo }),
    });
    evento = datos.evento;
    juego = datos.juego;
    document.title = evento.nombre;
    await pintar();
  };

  // El mismo canal que mira el televisor. Acá sirve para una sola cosa, y es
  // la que cierra el circuito: cuando el evento pasa a CERRADO, este teléfono
  // cambia solo a la playlist sin que nadie recargue nada.
  const conectar = () => {
    const socket = new WebSocket(
      `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/${slug}`);
    socket.addEventListener('message', (e) => {
      const mensaje = JSON.parse(e.data);
      if (mensaje.tipo === 'estado') recargar();
    });
    socket.addEventListener('close', () => setTimeout(conectar, 5000));
  };

  $('otras').addEventListener('click', otrasCinco);
  $('siguientes').addEventListener('click', siguientes);
  $('grupo').addEventListener('input', (e) => buscarGrupo(e.target.value));

  // Tocar la lupa --del teclado o la del costado-- baja el teclado y busca en
  // el momento. El `blur()` es lo único que cierra el teclado en iOS, y sin
  // eso tapaba justo los resultados que la persona quería ver.
  $('forma-grupo').addEventListener('submit', (e) => {
    e.preventDefault();
    clearTimeout(relojGrupo);
    $('grupo').blur();
    const texto = $('grupo').value.trim();
    if (texto.length < 2) return;
    $('buscando-grupo').classList.remove('oculto');
    (async () => {
      try {
        const datos = await pedir(`/grupo?q=${encodeURIComponent(texto)}`);
        pintarTemasDelGrupo(datos.temas, datos.parecidos || [], texto);
        // Y los resultados a la vista, sin que haya que buscarlos con el dedo.
        $('caja-grupo').scrollIntoView({behavior: 'smooth', block: 'start'});
      } catch {
        $('temas-grupo').innerHTML = '<li class="aviso">No se pudo buscar. Probá de nuevo.</li>';
      } finally { $('buscando-grupo').classList.add('oculto'); }
    })();
  });
  $('elegir-mas').addEventListener('click', elegirMas);

  recargar().then(conectar).catch((error) => {
    const codigo = error.datos && error.datos.error;
    $('problema').classList.remove('oculto');
    $('problema').textContent = codigo === 'repertorio_insuficiente'
      ? 'Este evento todavía no tiene canciones cargadas. Avisale a quien lo organiza.'
      : 'No pudimos cargar el evento. Probá de nuevo en un momento.';
  });
})();
