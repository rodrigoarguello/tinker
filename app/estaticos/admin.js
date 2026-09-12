/* admin.js — crear eventos, cargarles el repertorio y moverlos de estado.
 *
 * No hay login acá: esta página y su API están detrás de Authelia por
 * configuración del proxy. Si algún día se llega a /admin sin pasar por ahí,
 * el problema está en el .conf de nginx, no en este archivo.
 */
(() => {
  const $ = (id) => document.getElementById(id);
  const escapar = (t) => String(t ?? '').replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));

  const ETIQUETA = { previo: 'Previo', en_vivo: 'En vivo', cerrado: 'Cerrado' };
  const SIGUIENTE = { previo: 'en_vivo', en_vivo: 'cerrado', cerrado: 'en_vivo' };
  const ACCION = { previo: 'Poner en vivo', en_vivo: 'Cerrar recepción', cerrado: 'Reabrir' };

  let eventos = [];

  const listar = async () => {
    const respuesta = await fetch('/api/admin/eventos');
    if (!respuesta.ok) { $('filas').innerHTML = '<tr><td colspan="7">No se pudo cargar.</td></tr>'; return; }
    eventos = (await respuesta.json()).eventos;

    $('filas').innerHTML = eventos.map((e) => `
      <tr>
        <td>
          <b>${escapar(e.nombre)}</b><br>
          <a href="/${escapar(e.slug)}">/${escapar(e.slug)}</a> ·
          <a href="/${escapar(e.slug)}/pantalla">pantalla</a>
        </td>
        <td><span class="estado ${e.estado}">${ETIQUETA[e.estado]}</span></td>
        <td>${e.repertorio}</td>
        <td>${e.totales.personas}</td>
        <td>${e.totales.pedidos}</td>
        <td>${e.ia} / ${e.tope_ia}</td>
        <td>
          <button class="boton suave" data-slug="${escapar(e.slug)}" data-estado="${e.estado}">${ACCION[e.estado]}</button>
          <a class="boton suave" href="/api/admin/eventos/${escapar(e.slug)}/resultado">Resultado</a>
        </td>
      </tr>`).join('');

    $('evento-repertorio').innerHTML = eventos.map(
      (e) => `<option value="${escapar(e.slug)}">${escapar(e.nombre)} (${e.repertorio})</option>`
    ).join('');
    for (const id of ['evento-playlist', 'evento-grupos']) {
      $(id).innerHTML = eventos.map(
        (e) => `<option value="${escapar(e.slug)}">${escapar(e.nombre)}</option>`
      ).join('');
    }
    mostrarPlaylist();
    verSpotify();

    for (const boton of $('filas').querySelectorAll('button[data-slug]')) {
      boton.addEventListener('click', () => cambiarEstado(boton));
    }
  };

  const cambiarEstado = async (boton) => {
    const destino = SIGUIENTE[boton.dataset.estado];
    if (destino === 'cerrado'
        && !confirm('Cerrar la recepción: los teléfonos dejan de poder elegir. ¿Seguimos?')) return;
    boton.disabled = true;
    const respuesta = await fetch(`/api/admin/eventos/${boton.dataset.slug}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ estado: destino }),
    });
    if (!respuesta.ok) {
      const error = await respuesta.json().catch(() => ({}));
      const detalle = error.detail || {};
      alert(detalle.error === 'sin_repertorio'
        ? `Este evento tiene ${detalle.tiene} canciones y el mínimo es ${detalle.minimo}. `
          + 'Cargale el repertorio antes de ponerlo en vivo.'
        : 'No se pudo cambiar el estado.');
    }
    listar();
  };

  const mostrarPlaylist = () => {
    const evento = eventos.find((e) => e.slug === $('evento-playlist').value);
    $('playlist').value = (evento && evento.playlist_url) || '';
  };
  $('evento-playlist').addEventListener('change', mostrarPlaylist);

  $('guardar-playlist').addEventListener('click', async () => {
    const aviso = $('resultado-playlist');
    const respuesta = await fetch(`/api/admin/eventos/${$('evento-playlist').value}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ playlist_url: $('playlist').value.trim() || null }),
    });
    aviso.classList.remove('oculto');
    aviso.textContent = respuesta.ok
      ? 'Guardado. Cuando el evento cierre, los teléfonos van a mostrar este enlace.'
      : 'No se pudo guardar.';
    listar();
  });

  $('crear').addEventListener('click', async () => {
    const cuerpo = {
      nombre: $('nombre').value.trim(),
      slug: $('slug').value.trim(),
      lugar: $('lugar').value.trim() || null,
      fecha: $('fecha').value || null,
      rondas: Number($('rondas').value) || 5,
    };
    const respuesta = await fetch('/api/admin/eventos', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(cuerpo),
    });
    const aviso = $('error-crear');
    if (respuesta.ok) {
      aviso.classList.add('oculto');
      $('nombre').value = ''; $('slug').value = ''; $('lugar').value = '';
      listar();
      return;
    }
    const error = await respuesta.json().catch(() => ({}));
    const codigo = (error.detail && error.detail.error) || 'no_se_pudo';
    aviso.textContent = {
      slug_ocupado: 'Ya existe un evento con esa dirección.',
      slug_invalido: 'La dirección corta va en minúsculas, sin espacios ni acentos: san-lorenzo-2026.',
    }[codigo] || 'No se pudo crear. Revisá los datos.';
    aviso.classList.remove('oculto');
  });

  $('etiquetar').addEventListener('click', async () => {
    const slug = $('evento-repertorio').value;
    const aviso = $('resultado-repertorio');
    const boton = $('etiquetar');
    aviso.classList.remove('oculto');
    aviso.textContent = 'Etiquetando… esto puede tardar un minuto por cada 40 canciones.';
    boton.disabled = true;
    try {
      const respuesta = await fetch(`/api/admin/eventos/${slug}/repertorio/etiquetar`, { method: 'POST' });
      if (!respuesta.ok) { aviso.textContent = 'No se pudo etiquetar.'; return; }
      const datos = await respuesta.json();
      if (datos.error === 'sin_modelo') {
        aviso.textContent = 'No hay ninguna clave de IA configurada. El juego funciona igual, con género solamente.';
        return;
      }
      aviso.textContent = datos.pendientes === 0
        ? 'Ya estaba todo etiquetado.'
        : `${datos.etiquetadas} de ${datos.pendientes} canciones etiquetadas en ${datos.lotes} lote(s).`
          + (datos.fallados ? ` ${datos.fallados} lote(s) fallaron: volvé a correrlo.` : '');
    } finally {
      boton.disabled = false;
    }
  });

  $('cargar').addEventListener('click', async () => {
    const slug = $('evento-repertorio').value;
    const aviso = $('resultado-repertorio');
    aviso.classList.remove('oculto');
    aviso.textContent = 'Cargando…';
    const respuesta = await fetch(`/api/admin/eventos/${slug}/repertorio`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ texto: $('texto-repertorio').value, reemplazar: true }),
    });
    if (!respuesta.ok) { aviso.textContent = 'No se pudo cargar el repertorio.'; return; }
    const datos = await respuesta.json();
    let texto = `${datos.entraron} canciones cargadas · ${datos.total} en total.`;
    if (!datos.alcanza) texto += ` ⚠ Faltan para el mínimo de ${datos.minimo}: con menos, la quinta ronda se queda sin tarjetas.`;
    if (datos.sin_resolver.length) {
      texto += `\n\nNo pude leer estas líneas (falta el guión entre tema y artista):\n`
             + datos.sin_resolver.join('\n');
    }
    aviso.style.whiteSpace = 'pre-wrap';
    aviso.style.textAlign = 'left';
    aviso.textContent = texto;
    listar();
  });

  $('ver').addEventListener('click', async () => {
    const slug = $('evento-repertorio').value;
    const respuesta = await fetch(`/api/admin/eventos/${slug}/repertorio`);
    if (!respuesta.ok) return;
    const datos = await respuesta.json();
    $('texto-repertorio').value = datos.canciones
      .map((c) => `${c.titulo} — ${c.artista}`).join('\n');
    const aviso = $('resultado-repertorio');
    aviso.classList.remove('oculto');
    aviso.style.textAlign = 'center';
    aviso.textContent = `${datos.total} canciones en el repertorio de este evento.`;
  });

  /* ── Spotify ─────────────────────────────────────────────────────── */

  const ETIQUETAS_SPOTIFY = {
    credenciales: 'Credenciales de la app',
    conectado: 'Cuenta conectada',
    cuenta: 'Cuenta',
    modo: 'Cuándo entra una canción',
    votos_minimos: 'Votos necesarios',
    canciones_con_spotify_id: 'Temas encontrados en Spotify',
    canciones_sin_spotify_id: 'Temas sin buscar todavía',
  };

  const verSpotify = async () => {
    const respuesta = await fetch('/api/admin/spotify/estado');
    if (!respuesta.ok) return;
    const estado = await respuesta.json();
    $('spotify-que-falta').textContent = estado.que_falta
      || 'Todo listo: las canciones elegidas entran solas a la playlist.';
    $('spotify-estado').innerHTML = Object.entries(ETIQUETAS_SPOTIFY).map(([clave, etiqueta]) => {
      let valor = estado[clave];
      if (valor === true) valor = 'sí';
      else if (valor === false) valor = 'no';
      else if (valor === null || valor === undefined) valor = '—';
      return `<tr><th>${etiqueta}</th><td>${escapar(valor)}</td></tr>`;
    }).join('') + Object.entries(estado.cola || {}).map(
      ([nombre, cuantas]) => `<tr><th>En la cola · ${escapar(nombre)}</th><td>${cuantas}</td></tr>`
    ).join('');
    $('spotify-conectar').classList.toggle('oculto', !estado.credenciales);
  };

  const conResultado = async (destino, ruta, opciones) => {
    const aviso = $(destino);
    aviso.classList.remove('oculto');
    aviso.style.whiteSpace = 'pre-wrap';
    aviso.style.textAlign = 'left';
    aviso.textContent = 'Trabajando…';
    try {
      const respuesta = await fetch(ruta, opciones);
      const datos = await respuesta.json();
      if (!respuesta.ok) {
        const detalle = datos.detail || datos;
        aviso.textContent = detalle.error === 'sin_credenciales'
          ? 'Faltan las credenciales de Spotify: creá la app en developer.spotify.com y pegá '
            + 'SPOTIFY_CLIENT_ID y SPOTIFY_CLIENT_SECRET en el .env del proyecto.'
          : `No se pudo: ${JSON.stringify(detalle)}`;
        return null;
      }
      return datos;
    } catch (error) {
      aviso.textContent = 'No se pudo: ' + error.message;
      return null;
    }
  };

  // El puente mientras Spotify no habilite la escritura: la lista ordenada por
  // votos, lista para pegar en la playlist.
  $('spotify-enlaces').addEventListener('click', async () => {
    const datos = await conResultado('spotify-resultado',
      `/api/admin/eventos/${$('evento-grupos').value}/spotify/enlaces`);
    if (!datos) return;
    const caja = $('spotify-uris');
    caja.classList.remove('oculto');
    caja.value = datos.uris.join('\n');
    caja.select();
    let copiado = false;
    try { copiado = document.execCommand('copy'); } catch { copiado = false; }
    $('spotify-resultado').textContent =
      `${datos.uris.length} canciones ordenadas por votos${copiado ? ' · copiadas al portapapeles' : ' (copialas del cuadro de abajo)'}.\n`
      + 'En Spotify: abrí la playlist, tocá «Agregar» y pegá. Spotify reconoce los enlaces.\n'
      + (datos.sin_spotify.length
          ? `\n${datos.sin_spotify.length} no las encontré en Spotify:\n` + datos.sin_spotify.join('\n')
          : '');
  });

  // Sin playlist: Spotify nos bloqueó todos esos endpoints (403, incluso para
  // crear una vacía) y nos dejó abiertos los del reproductor. `play` acepta la
  // lista de URIs directamente, así que el ranking suena sin que exista ninguna
  // playlist. Es un botón y no un job porque REEMPLAZA lo que esté sonando en
  // el salón, y esa decisión la toma una persona.
  $('spotify-sonar').addEventListener('click', async () => {
    const slug = $('evento-grupos').value;
    if (!confirm('Va a reemplazar lo que esté sonando ahora por el ranking del evento. ¿Dale?')) return;
    const datos = await conResultado('spotify-resultado',
      `/api/admin/eventos/${slug}/spotify/reproducir`, { method: 'POST' });
    if (!datos) return;
    $('spotify-resultado').textContent =
      `Sonando ${datos.temas} temas por orden de votos en ${datos.dispositivo}.`;
  });

  $('spotify-resolver').addEventListener('click', async () => {
    const datos = await conResultado('spotify-resultado',
      `/api/admin/eventos/${$('evento-grupos').value}/spotify/resolver`, { method: 'POST' });
    if (!datos) return;
    $('spotify-resultado').textContent =
      `Encontré ${datos.resueltas} de ${datos.revisadas} temas en Spotify.`
      + (datos.sin_encontrar.length ? `\n\nNo encontré:\n${datos.sin_encontrar.join('\n')}` : '');
    verSpotify();
  });

  $('spotify-sincronizar').addEventListener('click', async () => {
    const datos = await conResultado('spotify-resultado',
      `/api/admin/eventos/${$('evento-grupos').value}/spotify/sincronizar`, { method: 'POST' });
    if (!datos) return;
    $('spotify-resultado').textContent =
      `Encoladas ${datos.encoladas}. Agregadas a la playlist: ${datos.agregadas || 0}.`
      + (datos.duplicadas ? ` Ya estaban: ${datos.duplicadas}.` : '')
      + (datos.motivo ? ` (${datos.motivo})` : '');
    verSpotify();
    verCola();
  });

  const verCola = async () => {
    const respuesta = await fetch('/api/admin/spotify/cola');
    if (!respuesta.ok) return;
    const { cola } = await respuesta.json();
    $('spotify-cola').innerHTML = cola.length
      ? '<tr><th>Tema</th><th>Estado</th><th>Intentos</th></tr>' + cola.map((f) => `
          <tr>
            <td>${escapar(f.titulo)} <span style="color:var(--muted)">· ${escapar(f.artista)}</span></td>
            <td>${escapar(f.estado)}</td>
            <td>${f.intentos}${f.error ? ' · ' + escapar(f.error) : ''}</td>
          </tr>`).join('')
      : '<tr><td>La cola está vacía.</td></tr>';
  };
  document.querySelector('#panel-spotify details').addEventListener('toggle', verCola);

  /* ── los grupos ──────────────────────────────────────────────────── */

  $('cargar-grupos').addEventListener('click', async () => {
    const slug = $('evento-grupos').value;
    const porTanda = Number($('por-tanda').value);
    await fetch(`/api/admin/eventos/${slug}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ prioritarias_por_tanda: porTanda }),
    });
    const datos = await conResultado('resultado-grupos',
      `/api/admin/eventos/${slug}/grupos`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          texto: $('texto-grupos').value,
          temas_por_grupo: Number($('temas-por-grupo').value) || 8,
          reemplazar: false,
        }),
      });
    if (!datos) return;
    let texto = `${datos.grupos} grupos · ${datos.entraron} temas nuevos.\n`
      + `En el repertorio hay ${datos.prioritarias_en_repertorio} temas de tus grupos, `
      + `de ${datos.total_repertorio} en total. Por tanda: ${datos.por_tanda} de 5.`;
    if (datos.sin_temas.length) {
      texto += `\n\nNo encontré temas de: ${datos.sin_temas.join(', ')}`
             + `\n(probá escribiendo el nombre más completo, hay artistas con el mismo nombre)`;
    }
    if (datos.detalle.length) {
      texto += '\n\n' + datos.detalle.map((g) => `  ${g.grupo}: ${g.temas} temas`).join('\n');
    }
    $('resultado-grupos').textContent = texto;
    $('cuantas-por-tanda').textContent = datos.por_tanda;
    listar();
  });

  $('ver-grupos').addEventListener('click', async () => {
    const respuesta = await fetch(`/api/admin/eventos/${$('evento-grupos').value}/grupos`);
    if (!respuesta.ok) return;
    const datos = await respuesta.json();
    $('cuantas-por-tanda').textContent = datos.por_tanda;
    $('por-tanda').value = datos.por_tanda;
    $('texto-grupos').value = datos.grupos.map(
      (g) => g.nota ? `${g.nombre} — ${g.nota}` : g.nombre
    ).join('\n');
    const aviso = $('resultado-grupos');
    aviso.classList.remove('oculto');
    aviso.style.whiteSpace = 'pre-wrap';
    aviso.textContent = datos.grupos.length
      ? datos.grupos.map((g) => `  ${g.nombre}: ${g.canciones} temas`).join('\n')
      : 'Todavía no cargaste grupos.';
  });

  /* ── inteligencia musical ────────────────────────────────────────── */

  const cuadro = (titulo, filas, columnas) => {
    if (!filas || !filas.length) return '';
    return `<h3 style="font-size:13px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);margin:1.2rem 0 .5rem">${titulo}</h3>`
      + '<table class="tabla">' + filas.map((f) => '<tr>' + columnas.map(
          (c) => `<td>${escapar(typeof c === 'function' ? c(f) : f[c])}</td>`
        ).join('') + '</tr>').join('') + '</table>';
  };

  const verInteligencia = async () => {
    const caja = $('inteligencia');
    caja.textContent = 'Cargando…';
    const respuesta = await fetch(`/api/admin/inteligencia?slug=${$('evento-grupos').value}`);
    if (!respuesta.ok) { caja.textContent = 'No se pudo cargar.'; return; }
    const d = await respuesta.json();
    const c = d.catalogo;

    let html = `<table class="tabla">
      <tr><th>Canciones activas</th><td>${c.activas} ${c.alcanza ? '' : `⚠ mínimo ${c.minimo}`} (objetivo ${c.objetivo})</td></tr>
      <tr><th>Paraguayas activas</th><td>${c.paraguayas} (piso ${c.piso_paraguay})</td></tr>
      <tr><th>Explotar / explorar</th><td>${d.explorar.explotar}% / ${d.explorar.explorar}%</td></tr>
      <tr><th>Por estado</th><td>${Object.entries(c.por_estado).map(([k, v]) => `${k}: ${v}`).join(' · ') || '—'}</td></tr>
      <tr><th>Por género</th><td>${Object.entries(c.por_genero).map(([k, v]) => `${k}: ${v}`).join(' · ') || '—'}</td></tr>
    </table>`;

    html += cuadro('Fuentes musicales', d.fuentes,
      ['nombre', 'estado', (f) => f.encontradas + ' encontradas', (f) => f.nuevas + ' nuevas',
       (f) => f.error || (f.ultima ? f.ultima.slice(0, 16).replace('T', ' ') : '—')]);
    html += cuadro('Tendencia en Paraguay', d.tendencia_py,
      ['titulo', 'artista', 'estado']);
    html += cuadro('Más mostradas', d.metricas.mas_mostradas,
      ['titulo', 'artista', (f) => `${f.mostrada} veces`, (f) => `${f.elegida} elegida`,
       (f) => f.conversion === null ? 'sin muestra' : `${Math.round(f.conversion * 100)}%`]);
    html += cuadro('Mejor conversión', d.metricas.mejor_conversion,
      ['titulo', 'artista', (f) => `${Math.round(f.conversion * 100)}%`]);
    html += cuadro('Peor conversión', d.metricas.peor_conversion,
      ['titulo', 'artista', (f) => `${Math.round(f.conversion * 100)}%`]);
    html += cuadro('Más rechazadas', d.metricas.mas_rechazadas.filter((f) => f.rechazada > 0),
      ['titulo', 'artista', (f) => `${f.rechazada} rechazos`]);
    html += cuadro('Fatigadas ahora', d.fatigadas,
      ['titulo', 'artista', (f) => `−${f.castigo}`]);

    if (d.perfil) {
      html += `<h3 style="font-size:13px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);margin:1.2rem 0 .5rem">Lo que le gusta a este evento</h3>
        <table class="tabla">
          <tr><th>Elecciones</th><td>${d.perfil.elecciones}</td></tr>
          <tr><th>Géneros</th><td>${Object.entries(d.perfil.generos).sort((a,b)=>b[1]-a[1]).map(([k,v])=>`${k}: ${v}`).join(' · ') || '—'}</td></tr>
          <tr><th>Décadas</th><td>${Object.entries(d.perfil.decadas).sort((a,b)=>b[1]-a[1]).map(([k,v])=>`${k}: ${v}`).join(' · ') || '—'}</td></tr>
          <tr><th>Artistas</th><td>${Object.entries(d.perfil.artistas).map(([k,v])=>`${k}: ${v}`).join(' · ') || '—'}</td></tr>
          <tr><th>Géneros rechazados</th><td>${Object.entries(d.perfil.rechazados).map(([k,v])=>`${k}: ${v}`).join(' · ') || '—'}</td></tr>
        </table>`;
    }
    caja.innerHTML = html;
  };

  $('ver-inteligencia').addEventListener('click', verInteligencia);

  const correrJob = async (nombre, boton) => {
    boton.disabled = true;
    const original = boton.textContent;
    boton.textContent = 'Buscando…';
    try {
      const respuesta = await fetch(`/api/admin/jobs/${nombre}`, { method: 'POST' });
      const datos = await respuesta.json();
      boton.textContent = original;
      alert(`${nombre}: ${JSON.stringify(datos.resultado)}`);
      verInteligencia();
    } catch (error) {
      boton.textContent = original;
      alert('No se pudo: ' + error.message);
    } finally {
      boton.disabled = false;
    }
  };
  $('job-py').addEventListener('click', (e) => correrJob('music:discover:py', e.target));
  $('job-generos').addEventListener('click', (e) => correrJob('music:discover:genres', e.target));
  $('job-refill').addEventListener('click', (e) => correrJob('music:refill', e.target));

  listar();
})();
