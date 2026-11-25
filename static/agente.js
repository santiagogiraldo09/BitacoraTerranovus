/* ===========================================================
   Asistente de formularios — panel flotante.

   Se incluye sólo en las páginas donde tiene sentido (Registros y
   Configuración) y sólo para administradores: la plantilla decide,
   este archivo no vuelve a comprobarlo porque el servidor ya rechaza
   a quien no corresponde.

   Construye su propio marcado para no duplicarlo en dos plantillas.
   =========================================================== */

(function () {
    'use strict';

    var panel, caja, texto, btnEnviar, btnBurbuja;
    var ocupado = false;

    var SALUDO = 'Soy el asistente de formularios. Descríbeme qué necesitas ' +
                 'registrar y te armo el formulario, reutilizando los campos ' +
                 'que tu empresa ya tiene.\n\n' +
                 'Sólo sé crear formularios: no consulto registros ni genero ' +
                 'informes.';

    // ── Utilidades ──────────────────────────────────────────

    function esc(v) {
        return String(v == null ? '' : v).replace(/[&<>"']/g, function (m) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;',
                     '"': '&quot;', "'": '&#39;' }[m];
        });
    }

    var NOMBRE_TIPO = {
        texto_corto: 'texto', texto_largo: 'texto largo', numero: 'número',
        imagen: 'imagen', fecha: 'fecha', hora: 'hora',
        seleccion: 'selección múltiple', seleccion_unica: 'selección',
        adjunto: 'adjunto', booleano: 'sí/no',
        moneda: 'moneda', porcentaje: 'porcentaje'
    };

    function alFinal() { caja.scrollTop = caja.scrollHeight; }

    // ── Pintado ─────────────────────────────────────────────

    function pintarMensaje(rol, contenido) {
        var div = document.createElement('div');
        div.className = 'ag-msg ' + (rol === 'usuario' ? 'usuario' : 'asistente');
        div.textContent = contenido || '';
        caja.appendChild(div);
        alFinal();
        return div;
    }

    function pintarPropuesta(propuesta, avisos, mensajeId) {
        var div = document.createElement('div');
        div.className = 'ag-propuesta';

        var campos = (propuesta.campos || []).map(function (c) {
            var nuevo = c.accion === 'crear';
            return '<div class="ag-campo">' +
                     '<span class="ag-tag ' + (nuevo ? 'nuevo' : 'reuso') + '">' +
                        (nuevo ? 'nuevo' : 'ya existe') + '</span>' +
                     '<span>' + esc(c.nombre) + (c.requerido ? ' *' : '') + '</span>' +
                     '<span class="ag-tipo">' +
                        esc(NOMBRE_TIPO[c.tipo] || c.tipo) + '</span>' +
                   '</div>';
        }).join('');

        var bloqueAvisos = (avisos && avisos.length)
            ? '<div class="ag-avisos">' +
                avisos.map(function (a) { return '<div>' + esc(a) + '</div>'; }).join('') +
              '</div>'
            : '';

        div.innerHTML =
            '<h5>' + esc(propuesta.nombre) + '</h5>' +
            '<div class="ag-resumen">' + (propuesta.campos || []).length +
                ' campos · ' + propuesta.reusados + ' reutilizados · ' +
                propuesta.nuevos + ' nuevos</div>' +
            '<div class="ag-campos">' + campos + '</div>' +
            bloqueAvisos +
            '<button class="ag-crear">Crear formulario</button>';

        div.querySelector('.ag-crear')
           .addEventListener('click', function () { crear(mensajeId, this); });

        caja.appendChild(div);
        alFinal();
    }

    // ── Conversación ────────────────────────────────────────

    async function cargar() {
        try {
            var res  = await fetch('/api/agente/conversacion');
            var data = await res.json();
            if (!data.success) throw new Error();

            caja.innerHTML = '';
            if (!data.mensajes.length) {
                pintarMensaje('asistente', SALUDO);
                return;
            }
            data.mensajes.forEach(function (m) {
                pintarMensaje(m.rol, m.contenido);
                if (m.propuesta) pintarPropuesta(m.propuesta, [], m.id);
            });
        } catch (e) {
            caja.innerHTML = '';
            pintarMensaje('asistente', 'No pude cargar la conversación.');
        }
    }

    async function enviar() {
        var valor = texto.value.trim();
        if (!valor || ocupado) return;

        ocupado = true;
        btnEnviar.disabled = true;
        texto.value = '';
        texto.style.height = '40px';

        pintarMensaje('usuario', valor);
        var esperando = pintarMensaje('asistente', 'Pensando…');
        esperando.classList.add('escribiendo');

        try {
            var res = await fetch('/api/agente/mensaje', {
                method:  'POST',
                headers: { 'Content-Type': 'application/json' },
                body:    JSON.stringify({ texto: valor })
            });
            var data = await res.json();
            esperando.remove();

            if (!data.success) {
                pintarMensaje('asistente', data.error || 'No pude responder.');
                return;
            }

            pintarMensaje('asistente', data.contenido);
            if (data.propuesta) {
                pintarPropuesta(data.propuesta, data.avisos, data.mensaje_id);
            }
        } catch (e) {
            esperando.remove();
            pintarMensaje('asistente', 'Se perdió la conexión. Intenta de nuevo.');
        } finally {
            ocupado = false;
            btnEnviar.disabled = false;
            texto.focus();
        }
    }

    async function crear(mensajeId, boton) {
        boton.disabled = true;
        boton.textContent = 'Creando…';
        try {
            var res = await fetch('/api/agente/crear', {
                method:  'POST',
                headers: { 'Content-Type': 'application/json' },
                body:    JSON.stringify({ mensaje_id: mensajeId })
            });
            var data = await res.json();

            if (!data.success) {
                boton.disabled = false;
                boton.textContent = 'Crear formulario';
                pintarMensaje('asistente', data.error || 'No se pudo crear.');
                return;
            }

            boton.textContent = 'Formulario creado';
            pintarMensaje('asistente',
                'Listo. Lo abro en el constructor para que lo revises.');

            // Se lleva al constructor, que es donde se ajusta el orden,
            // los grupos y las opciones de las selecciones.
            setTimeout(function () {
                // Si ya estamos en Configuración, cambiar sólo el ancla no
                // recarga la página: hay que mover la sección a mano.
                if (window.location.pathname === '/configuracion') {
                    window.location.hash = 'formularios';
                    if (typeof mostrarSeccion === 'function') {
                        mostrarSeccion('formularios');
                    }
                    if (typeof cargarFormularios === 'function') {
                        cargarFormularios();
                    }
                    cerrar();
                } else {
                    window.location.href = '/configuracion#formularios';
                }
            }, 1200);

        } catch (e) {
            boton.disabled = false;
            boton.textContent = 'Crear formulario';
            pintarMensaje('asistente', 'Se perdió la conexión.');
        }
    }

    async function reiniciar() {
        if (ocupado) return;
        await fetch('/api/agente/nueva', { method: 'POST' });
        caja.innerHTML = '';
        pintarMensaje('asistente', SALUDO);
    }

    // ── Montaje ─────────────────────────────────────────────

    function abrir() {
        panel.classList.add('abierto');
        btnBurbuja.classList.add('oculto');
        if (!caja.children.length) cargar();
        texto.focus();
    }

    function cerrar() {
        panel.classList.remove('abierto');
        btnBurbuja.classList.remove('oculto');
    }

    function montar() {
        btnBurbuja = document.createElement('button');
        btnBurbuja.className = 'ag-boton';
        btnBurbuja.title = 'Asistente de formularios';
        btnBurbuja.innerHTML = '<i class="fas fa-wand-magic-sparkles"></i>';
        btnBurbuja.addEventListener('click', abrir);

        panel = document.createElement('div');
        panel.className = 'ag-panel';
        panel.innerHTML =
            '<div class="ag-cabecera">' +
                '<i class="fas fa-wand-magic-sparkles" style="color:var(--naranja,#FFAF33)"></i>' +
                '<h4>Asistente de formularios</h4>' +
                '<button class="ag-nueva" title="Empezar de nuevo">' +
                    '<i class="fas fa-rotate-left"></i></button>' +
                '<button class="ag-cerrar" title="Cerrar">' +
                    '<i class="fas fa-times"></i></button>' +
            '</div>' +
            '<div class="ag-mensajes"></div>' +
            '<div class="ag-entrada">' +
                '<textarea placeholder="Ej: un formulario para control de calidad de concreto" ' +
                          'rows="1"></textarea>' +
                '<button class="ag-enviar"><i class="fas fa-paper-plane"></i></button>' +
            '</div>';

        document.body.appendChild(btnBurbuja);
        document.body.appendChild(panel);

        caja      = panel.querySelector('.ag-mensajes');
        texto     = panel.querySelector('textarea');
        btnEnviar = panel.querySelector('.ag-enviar');

        panel.querySelector('.ag-cerrar').addEventListener('click', cerrar);
        panel.querySelector('.ag-nueva').addEventListener('click', reiniciar);
        btnEnviar.addEventListener('click', enviar);

        // Enter envía; Shift+Enter hace salto de línea.
        texto.addEventListener('keydown', function (e) {
            if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); enviar(); }
        });
        texto.addEventListener('input', function () {
            this.style.height = '40px';
            this.style.height = Math.min(this.scrollHeight, 110) + 'px';
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', montar);
    } else {
        montar();
    }
})();
