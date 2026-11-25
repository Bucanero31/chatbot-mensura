/* Chatbot Mensura: pantalla. JS plano, sin dependencias. Rutas siempre RELATIVAS. */
(function () {
  'use strict';
  window.LX = window.LX || {};

  var BASE = location.pathname.slice(0, location.pathname.lastIndexOf('/') + 1);
  var CLAVE = 'lx-chatbot-mensura';
  var REDUCIDO = !!(window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches);
  var ETQ_ESTADO = { vigente: 'vigente', por_confirmar: 'por confirmar', derogada: 'derogada', borrador: 'borrador' };

  var $ = function (id) { return document.getElementById(id); };
  var columna = $('columna'), hilo = $('hilo'), entrada = $('entrada'), btnEnviar = $('enviar');
  var turnos = [];       // {q, resp, final, valorado, ...}
  var enVuelo = false;
  var generacion = 0;    // sube con cada «nueva conversación»: corta sondeos viejos

  /* ---------- utilidades ---------- */
  function esc(s) {
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
  function el(tag, cls, html) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (html != null) e.innerHTML = html;
    return e;
  }
  function api(ruta, opciones) {
    return fetch(BASE + ruta, opciones).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) {
        if (!r.ok) { var e = new Error(j.error || ('Error ' + r.status)); e.status = r.status; throw e; }
        return j;
      });
    });
  }
  function postJson(ruta, cuerpo) {
    return api(ruta, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(cuerpo) });
  }
  function abajo(forzar) {
    var cerca = hilo.scrollHeight - hilo.scrollTop - hilo.clientHeight < 160;
    if (forzar || cerca) hilo.scrollTop = hilo.scrollHeight;
  }

  /* ---------- formato del texto ---------- */
  function citas(html, nmax) {
    return html.replace(/(?:\[\d+(?:\s*,\s*\d+)*\])+/g, function (grupo) {
      var out = '';
      (grupo.match(/\d+/g) || []).forEach(function (d) {
        var n = parseInt(d, 10);
        out += (n >= 1 && n <= nmax)
          ? '<button type="button" class="cita" data-n="' + n + '" aria-label="Abrir la fuente ' + n + '">' + n + '</button>'
          : '<span class="cita-rota" title="Fuente inexistente">' + n + '</span>';
      });
      return out;
    });
  }
  function formato(texto, nmax) {
    var lineas = String(texto || '').replace(/\r/g, '').split('\n');
    var html = '', par = [], lista = null, tipo = '';
    function inline(t) { return citas(esc(t).replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>'), nmax); }
    function cierraPar() { if (par.length) { html += '<p>' + par.map(inline).join('<br>') + '</p>'; par = []; } }
    function cierraLista() { if (lista) { html += '<' + tipo + '>' + lista.map(function (x) { return '<li>' + inline(x) + '</li>'; }).join('') + '</' + tipo + '>'; lista = null; } }
    lineas.forEach(function (l) {
      var u = /^\s*[-*•]\s+(.*)$/.exec(l), o = /^\s*\d+[.)]\s+(.*)$/.exec(l);
      if (!l.trim()) { cierraPar(); cierraLista(); }
      else if (u || o) {
        var t = u ? 'ul' : 'ol';
        cierraPar();
        if (lista && tipo !== t) cierraLista();
        if (!lista) { lista = []; tipo = t; }
        lista.push((u || o)[1]);
      } else { cierraLista(); par.push(l); }
    });
    cierraPar(); cierraLista();
    return html;
  }
  function plano(texto) { return String(texto || '').replace(/\*\*(.+?)\*\*/g, '$1'); }

  /* ---------- guardado ---------- */
  function guardar() {
    try {
      var datos = turnos.slice(-30).map(function (t) {
        return { q: t.q, resp: t.resp, final: t.final || null, nofinal: !!t.nofinal, valorado: t.valorado || null };
      });
      localStorage.setItem(CLAVE, JSON.stringify(datos));
    } catch (e) { /* sin almacenamiento: funciona igual */ }
  }
  function cargar() {
    try { var v = JSON.parse(localStorage.getItem(CLAVE) || '[]'); return Array.isArray(v) ? v : []; }
    catch (e) { return []; }
  }

  /* ---------- panel de la fuente ---------- */
  var panel = $('panel'), panelCuerpo = $('panel-cuerpo'), panelTitulo = $('panel-titulo');
  var ultimoFoco = null, pedidoPanel = 0;

  function insignia(estado) {
    return '<span class="ins ' + esc(estado) + '">' + esc(ETQ_ESTADO[estado] || estado || '') + '</span>';
  }
  function resaltar(texto, extracto) {
    if (!extracto) return esc(texto);
    var rangos = [];
    String(extracto).split(/…|\.\.\./).forEach(function (p) {
      p = p.trim();
      if (p.length < 12) return;
      var i = texto.indexOf(p);
      if (i >= 0) rangos.push([i, i + p.length]);
    });
    if (!rangos.length) return esc(texto);
    rangos.sort(function (a, b) { return a[0] - b[0]; });
    var out = '', pos = 0;
    rangos.forEach(function (r) {
      if (r[0] < pos) return;
      out += esc(texto.slice(pos, r[0])) + '<mark>' + esc(texto.slice(r[0], r[1])) + '</mark>';
      pos = r[1];
    });
    return out + esc(texto.slice(pos));
  }
  function abrirFuente(f, extracto) {
    if (!f) return;
    ultimoFoco = document.activeElement;
    panel.hidden = false;
    var yo = ++pedidoPanel;
    panelTitulo.textContent = f.norma + ' · ' + f.articulo;
    panelCuerpo.innerHTML = '<p class="buscando">Cargando…</p>';
    var q = 'api/articulo?norma=' + encodeURIComponent(f.norma) + '&articulo=' + encodeURIComponent(f.articulo || '') +
      '&apartado=' + encodeURIComponent(f.apartado || '');
    api(q).then(function (a) {
      if (yo !== pedidoPanel) return;
      var ref = a.articulo + (a.apartado ? ' · ' + a.apartado : '');
      panelTitulo.textContent = a.norma + ' · ' + ref;
      var h = '<h3>' + esc(a.norma_titulo || a.norma) + '</h3>' +
        '<p class="sec">' + esc(ref) + (a.titulo ? ' — ' + esc(a.titulo) : '') +
        (a.seccion ? '<br>' + esc(a.seccion) : '') + ' ' + insignia(a.estado) + '</p>';
      if (a.aviso) h += '<p class="nota-ambar">⚠ ' + esc(a.aviso) + '</p>';
      h += '<div class="literal">' + resaltar(a.texto || '', extracto) + '</div>';
      h += '<div class="panel-nav">' +
        '<button type="button" class="lx-boton lx-secundario" id="p-ant" aria-label="Artículo anterior"' + (a.anterior ? '' : ' disabled') + '>← anterior</button>' +
        '<button type="button" class="lx-boton lx-secundario" id="p-sig" aria-label="Artículo siguiente"' + (a.siguiente ? '' : ' disabled') + '>siguiente →</button>' +
        (a.url ? '<a class="lx-boton" target="_blank" rel="noopener" href="' + esc(a.url) + '">Abrir el PDF oficial</a>' : '') + '</div>';
      panelCuerpo.innerHTML = h;
      panelCuerpo.scrollTop = 0;
      function ir(x) { return function () { abrirFuente({ norma: a.norma, articulo: x.articulo, apartado: x.apartado }); }; }
      if (a.anterior) $('p-ant').onclick = ir(a.anterior);
      if (a.siguiente) $('p-sig').onclick = ir(a.siguiente);
    }).catch(function (e) {
      if (yo !== pedidoPanel) return;
      panelCuerpo.innerHTML = '<p class="nota-ambar">No pude cargar el artículo. ' + esc(e.message) + '</p>';
    });
  }
  function cerrarPanel() {
    if (panel.hidden) return;
    panel.hidden = true; pedidoPanel++;
    if (ultimoFoco && ultimoFoco.focus) try { ultimoFoco.focus(); } catch (e) {}
  }
  $('panel-cerrar').onclick = cerrarPanel;
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') cerrarPanel(); });

  /* ---------- tarjeta de respuesta ---------- */
  function refsTexto(fuentes) {
    return (fuentes || []).map(function (f) {
      return '[' + f.n + '] ' + f.norma + ', ' + f.articulo + (f.apartado ? ' ' + f.apartado : '') + (f.titulo ? ' (' + f.titulo + ')' : '');
    }).join('\n');
  }
  function copiar(texto, boton) {
    function listo() { var t = boton.textContent; boton.textContent = 'Copiado'; setTimeout(function () { boton.textContent = t; }, 1500); }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(texto).then(listo, function () { viejo(); });
    } else viejo();
    function viejo() {
      var ta = el('textarea'); ta.value = texto; ta.style.position = 'fixed'; ta.style.opacity = '0';
      document.body.appendChild(ta); ta.select();
      try { document.execCommand('copy'); listo(); } catch (e) {}
      document.body.removeChild(ta);
    }
  }

  function chip(f) {
    var b = el('button', 'chip');
    b.type = 'button';
    b.setAttribute('aria-label', 'Abrir fuente ' + f.n + ': ' + f.norma + ' ' + f.articulo);
    var titulo = f.titulo ? (f.titulo.length > 40 ? f.titulo.slice(0, 38) + '…' : f.titulo) : '';
    b.innerHTML = '<span class="n">' + esc(f.n) + '</span><span class="t"><b>' + esc(f.norma) + '</b> ' + esc(f.articulo) +
      (f.apartado ? ' ' + esc(f.apartado) : '') + (titulo ? ' <span>· ' + esc(titulo) + '</span>' : '') + '</span>' +
      insignia(f.estado) + (f.aviso ? '<span title="' + esc(f.aviso) + '" aria-label="Aviso: ' + esc(f.aviso) + '">⚠</span>' : '');
    b.onclick = function () { abrirFuente(f, f.extracto); };
    return b;
  }

  // Construye la tarjeta de un turno y devuelve {raiz, ...}. t.resp = respuesta de /preguntar.
  function construirTarjeta(t, vivo) {
    var r = t.resp, fuentes = r.fuentes || [], nf = fuentes.length;
    var raiz = el('div', 'tarjeta');
    var bFinal = el('div', 'res-final'); bFinal.hidden = true;
    var bRapida = el('div', 'citas-blq');
    var notas = el('div');
    var fu = el('div', 'fuentes');
    fuentes.forEach(function (f) { fu.appendChild(chip(f)); });
    var pie = el('div', 'pie-tarj');
    raiz.appendChild(bFinal); raiz.appendChild(bRapida); raiz.appendChild(notas);
    if (nf) raiz.appendChild(fu);
    raiz.appendChild(pie);

    raiz.addEventListener('click', function (e) {
      var c = e.target.closest && e.target.closest('.cita');
      if (c) { var f = fuentes[parseInt(c.getAttribute('data-n'), 10) - 1]; abrirFuente(f, f && f.extracto); }
    });

    var textoVisible = function () { return t.final ? t.final.texto : (r.cache ? r.cache.texto : (r.rapida && r.rapida.texto) || ''); };

    function pintaRapida(etiqueta) {
      var texto = r.cache ? r.cache.texto : (r.rapida && r.rapida.texto) || '';
      bRapida.innerHTML = '<span class="etq">' + etiqueta + '</span><div class="cuerpo">' + formato(texto, nf) + '</div>';
    }
    function pliegaRapida() {
      var cuerpo = bRapida.querySelector('.cuerpo');
      var d = el('details', 'citas');
      d.innerHTML = '<summary>Ver citas literales</summary>';
      d.appendChild(cuerpo);
      bRapida.replaceWith(d);
      bRapida = d;
    }
    function avisos(lista, prefijo) {
      if (lista && lista.length) notas.appendChild(el('div', 'nota-ambar', '<b>' + prefijo + '</b> ' + lista.map(esc).join(' ')));
    }
    function muestraFinal(f, nota) {
      bFinal.hidden = false;
      bFinal.innerHTML = '<span class="etq">Respuesta</span><div class="cuerpo">' + formato(f.texto, nf) + '</div>';
      if (!bRapida.classList.contains('citas')) pliegaRapida();
      // Si el redactor no encontró la respuesta, las citas literales NO se esconden: a veces
      // la respuesta está en ellas y el modelo no la vio (caso real del 24-nov-2026).
      if (/no encuentro/i.test(f.texto || '') && bRapida.tagName === 'DETAILS') {
        bRapida.open = true;
        var s = bRapida.querySelector('summary');
        if (s) s.textContent = 'Lo más cercano en la normativa (citas literales)';
      }
      notas.innerHTML = '';
      avisos(f.avisos, 'Compruébalo:');
      if (f.modelo) notas.appendChild(el('p', 'meta', esc(f.modelo) + (f.segundos != null ? ' · ' + esc(f.segundos) + ' s' : '')));
    }
    function sinRedactor() {
      notas.appendChild(el('div', 'nota-gris', 'El redactor no está disponible; se muestran las citas literales.'));
    }

    // contenido inicial
    if (r.cache) {
      pintaRapida('Respondida antes');
      avisos(r.cache.avisos, 'Compruébalo:');
    } else {
      pintaRapida('Citas literales');
      if (r.rapida && r.rapida.confianza === 'baja')
        notas.appendChild(el('div', 'nota-ambar', 'No encontré una respuesta clara; esto es lo más cercano.'));
    }
    if (!vivo) {
      if (t.final) muestraFinal(t.final);
      else if (t.nofinal) sinRedactor();
    }

    // pie
    var bUp = el('button', '', '👍'), bDown = el('button', '', '👎'), bCop = el('button', '', 'Copiar');
    bUp.type = bDown.type = bCop.type = 'button';
    bUp.setAttribute('aria-label', 'Respuesta útil'); bDown.setAttribute('aria-label', 'Respuesta no útil');
    bCop.setAttribute('aria-label', 'Copiar la respuesta');
    pie.appendChild(bUp); pie.appendChild(bDown); pie.appendChild(bCop);
    var gr = el('span', 'gracias'); pie.appendChild(gr);
    function gracias() { bUp.hidden = bDown.hidden = true; gr.textContent = 'Gracias, lo tendré en cuenta'; }
    if (t.valorado) gracias();
    bUp.onclick = function () {
      postJson('api/valorar', { id: r.id, util: true }).catch(function () {});
      t.valorado = 'si'; gracias(); guardar();
    };
    bDown.onclick = function () {
      postJson('api/valorar', { id: r.id, util: false }).catch(function () {});
      t.valorado = 'no'; gracias(); guardar();
      var f = el('form', 'falto');
      f.innerHTML = '<input type="text" aria-label="¿Qué faltó?" placeholder="¿Qué faltó? (opcional)"><button type="submit" aria-label="Enviar comentario">Enviar</button>';
      f.onsubmit = function (ev) {
        ev.preventDefault();
        var c = f.querySelector('input').value.trim();
        if (c) postJson('api/valorar', { id: r.id, util: false, comentario: c }).catch(function () {});
        f.remove();
      };
      pie.appendChild(f);
      f.querySelector('input').focus();
    };
    bCop.onclick = function () {
      var refs = refsTexto(fuentes);
      copiar(plano(textoVisible()) + (refs ? '\n\nFuentes:\n' + refs : ''), bCop);
    };

    return {
      raiz: raiz, notas: notas, bFinal: bFinal,
      pintaRapida: pintaRapida, muestraFinal: muestraFinal, sinRedactor: sinRedactor, pliegaRapida: pliegaRapida,
      bRapida: function () { return bRapida; }
    };
  }

  /* ---------- redacción: sondeo y efecto de escritura ---------- */
  function seguirRedaccion(t, tarj, gen) {
    var r = t.resp, nf = (r.fuentes || []).length;
    var desde = 0, cola = null, caja = null, texto = '', pendiente = '', temporizador = null, terminado = false;

    function vivo() { return gen === generacion; }
    function asegura() {
      if (caja) return;
      tarj.bFinal.hidden = false;
      tarj.bFinal.innerHTML = '<span class="etq">Respuesta</span><div class="cuerpo escribiendo"></div>';
      caja = tarj.bFinal.querySelector('.cuerpo');
      if (!tarj.bRapida().classList.contains('citas')) tarj.pliegaRapida();
    }
    function avanzaTexto() {
      if (!pendiente) { clearInterval(temporizador); temporizador = null; return; }
      var k = Math.max(1, Math.ceil(pendiente.length / 25));
      texto += pendiente.slice(0, k); pendiente = pendiente.slice(k);
      caja.textContent = texto; abajo();
    }
    function añade(trozo) {
      if (!trozo) return;
      asegura();
      if (REDUCIDO) { texto += trozo; caja.textContent = texto; abajo(); return; }
      pendiente += trozo;
      if (!temporizador) temporizador = setInterval(avanzaTexto, 25);
    }
    function fin(f) {
      terminado = true; clearInterval(temporizador); temporizador = null;
      if (cola) { cola.remove(); cola = null; }
      t.final = f; tarj.muestraFinal(f); guardar(); abajo();
    }
    function sinModelo() {
      terminado = true; clearInterval(temporizador); temporizador = null;
      if (cola) { cola.remove(); cola = null; }
      if (caja) { tarj.bFinal.hidden = true; tarj.bFinal.innerHTML = ''; }
      t.nofinal = true; tarj.sinRedactor(); guardar();
    }
    function sondea() {
      if (!vivo() || terminado) return;
      api('api/respuesta/' + encodeURIComponent(r.id) + '?desde=' + desde).then(function (d) {
        if (!vivo()) return;
        if (d.estado === 'en_cola') {
          var n = d.posicion || 0;
          var msg = 'En cola: ' + n + ' pregunta' + (n === 1 ? '' : 's') + ' delante';
          if (!cola) { cola = el('p', 'cola'); tarj.notas.appendChild(cola); }
          cola.textContent = msg;
          return setTimeout(sondea, 1000);
        }
        if (cola) { cola.remove(); cola = null; }
        if (d.estado === 'escribiendo') {
          añade(d.texto); desde = d.n != null ? d.n : desde + (d.texto || '').length;
          return setTimeout(sondea, 300);
        }
        if (d.estado === 'listo' && d.final) return fin(d.final);
        if (d.estado === 'listo') { añade(d.texto); return setTimeout(function () { sondea(); }, 300); }
        sinModelo();
      }).catch(function (e) {
        if (!vivo()) return;
        if (e.status === 404 || e.status === 400) return sinModelo();
        setTimeout(sondea, 2000);
      });
    }
    sondea();
  }

  /* ---------- hilo ---------- */
  function burbuja(q) { var b = el('div', 'msg-yo'); b.textContent = q; columna.appendChild(b); return b; }

  function pintaVacio() {
    columna.innerHTML = '';
    var v = el('div', 'vacio');
    v.innerHTML = '<h2>Normativa de mensura</h2><p>Pregúntame sobre la Ley 108-05, los reglamentos y las disposiciones técnicas de mensura. Respondo citando el artículo exacto.</p>';
    var s = el('div', 'sugs'); v.appendChild(s); columna.appendChild(v);
    api('api/sugerencias').then(function (d) {
      (d.preguntas || []).forEach(function (p) {
        var b = el('button', 'sug'); b.type = 'button'; b.textContent = p;
        b.onclick = function () { enviar(p); };
        s.appendChild(b);
      });
    }).catch(function () {});
  }

  // Solo las vueltas de ESTA visita: las restauradas de otro día no son «la conversación»,
  // y mandarlas impedía usar la caché y mezclaba temas (hallazgo 7 de la revisión). El
  // servidor solo usa la pregunta, recortada.
  function historial() {
    return turnos.filter(function (t) { return t.sesion; }).slice(-3).map(function (t) {
      return { pregunta: String(t.q || '').slice(0, 300) };
    });
  }

  function enviar(texto) {
    texto = String(texto || '').trim();
    if (!texto || enVuelo) return;
    var vacio = columna.querySelector('.vacio'); if (vacio) vacio.remove();
    enVuelo = true; btnEnviar.disabled = true;
    var gen = generacion;
    burbuja(texto);
    var espera = el('div', 'tarjeta'); espera.innerHTML = '<p class="buscando" style="margin:0">Buscando en la normativa…</p>';
    columna.appendChild(espera); abajo(true);
    var hist = historial();
    entrada.value = ''; crece();

    postJson('api/preguntar', { pregunta: texto, historial: hist }).then(function (r) {
      if (gen !== generacion) return;
      espera.remove();
      var t = { q: texto, resp: r, sesion: true };
      turnos.push(t);
      var tarj = construirTarjeta(t, true);
      columna.appendChild(tarj.raiz); abajo(true);
      guardar();
      if (!r.cache && (r.redaccion === 'en_cola' || r.redaccion === 'escribiendo')) seguirRedaccion(t, tarj, gen);
      else if (!r.cache && r.redaccion === 'no_disponible') { t.nofinal = true; tarj.sinRedactor(); guardar(); }
    }).catch(function (e) {
      if (gen !== generacion) return;
      espera.className = 'tarjeta';
      espera.innerHTML = '<div class="nota-ambar" style="margin:0">No pude obtener la respuesta. ' + esc(e.message) + '</div>';
    }).then(function () {
      enVuelo = false; btnEnviar.disabled = false;
      if (document.activeElement === document.body) entrada.focus();
    });
  }

  function restaurar() {
    var datos = cargar();
    if (!datos.length) return pintaVacio();
    columna.innerHTML = '';
    datos.forEach(function (d) {
      if (!d || !d.resp) return;
      var t = { q: d.q, resp: d.resp, final: d.final, nofinal: d.nofinal, valorado: d.valorado };
      // una redacción que quedó a medias no se retoma: se queda con las citas
      if (!t.final && !t.nofinal && !t.resp.cache && t.resp.redaccion !== 'no_disponible') t.nofinal = false;
      turnos.push(t);
      burbuja(t.q);
      columna.appendChild(construirTarjeta(t, false).raiz);
    });
    if (!turnos.length) return pintaVacio();
    abajo(true);
  }

  function nueva() {
    generacion++; turnos = []; enVuelo = false; btnEnviar.disabled = false;
    cerrarPanel();
    try { localStorage.removeItem(CLAVE); } catch (e) {}
    pintaVacio(); entrada.focus();
  }

  /* ---------- compositor ---------- */
  function crece() {
    entrada.style.height = 'auto';
    entrada.style.height = Math.min(entrada.scrollHeight, 150) + 'px';
  }
  entrada.addEventListener('input', crece);
  entrada.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); enviar(entrada.value); }
  });
  $('form').addEventListener('submit', function (e) { e.preventDefault(); enviar(entrada.value); });
  $('btn-nueva').onclick = nueva;

  /* ---------- cabecera: estado ---------- */
  function salud() {
    api('api/salud').then(function (s) {
      var e = $('estado'), m = s.modelo || {};
      e.className = 'chat-estado' + (m.disponible ? ' ok' : '');
      e.querySelector('.txt').textContent = m.disponible ? 'Redactor local: ' + (m.nombre || '') : 'Modo citas';
      var c = s.corpus || {}, a = s.actualizacion || {};
      e.title = 'Corpus ' + (c.version || '?') + (a.ultima ? ' · última actualización ' + String(a.ultima).replace('T', ' ').slice(0, 16) : '') +
        (m.dispositivo ? ' · ' + m.dispositivo : '');
      $('btn-novedades').classList.toggle('lx-oculto', !(a.novedades > 0));
    }).catch(function () {
      var e = $('estado'); e.className = 'chat-estado'; e.querySelector('.txt').textContent = 'Sin conexión con el servidor';
    });
  }
  $('btn-novedades').onclick = function () {
    var dlg = $('dlg-nov'), lista = $('dlg-lista');
    lista.innerHTML = '<p>Cargando…</p>';
    if (dlg.showModal) dlg.showModal(); else dlg.setAttribute('open', '');
    api('api/novedades').then(function (d) {
      lista.innerHTML = (d.novedades || []).map(function (n) {
        return '<p class="nov"><small>' + esc(n.fecha) + '</small>' + esc(n.texto) + '</p>';
      }).join('') || '<p>No hay novedades.</p>';
    }).catch(function () { lista.innerHTML = '<p>No pude cargar las novedades.</p>'; });
  };
  $('dlg-cerrar').onclick = function () { var d = $('dlg-nov'); if (d.close) d.close(); else d.removeAttribute('open'); };

  /* ---------- arranque ---------- */
  if (location.pathname.indexOf('/chatbot-mensura') === 0) $('volver').classList.remove('lx-oculto');
  restaurar(); salud(); setInterval(salud, 60000); entrada.focus();

  LX.chat = { base: BASE, enviar: enviar, nueva: nueva, formato: formato, turnos: function () { return turnos; } };
})();
