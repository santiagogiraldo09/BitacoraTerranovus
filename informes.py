# ════════════════════════════════════════════════════════════════
#  Bitácora Digital — Plantillas de informe personalizadas
#  Blueprint: rutas, API y catálogo de datos insertables.
#
#  Vive aparte de app.py a propósito: son ~600 líneas que no tienen
#  por qué engordar un archivo que ya pasa de 7.000.
#
#  Las dependencias de app.py (db_connection, _PDFTablero) se
#  importan DENTRO de cada función, igual que hace api_movil.py.
#  Importarlas arriba crearía un ciclo: app importa este módulo
#  para registrarlo, y este importaría app al cargarse.
# ════════════════════════════════════════════════════════════════

import json
import copy
from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for

informes_bp = Blueprint('informes', __name__)


# ── Catálogo de bloques permitidos ──────────────────────────────
# Lista blanca: una sección con un 'tipo' que no esté aquí se
# descarta al guardar. Impide que un cliente manipulado inyecte
# un tipo que el renderizador no sabe dibujar.

TIPOS_BLOQUE = {
    'portada',      # logo, título, periodo, filtros aplicados
    'texto',        # texto libre con datos insertables
    'imagen',       # imagen subida por el usuario
    'resumen_ia',   # narrativa generada sobre cifras ya calculadas
    'cifras',       # total del periodo, comparación, por formulario
    'indicadores',  # agregados de los campos de los formularios
    'firmas',       # bloques de firma con cargo
}

ANCHOS_BLOQUE = {'completo', 'mitad'}

MAX_SECCIONES = 40   # tope defensivo: una plantilla no es un libro


# ════════════════════════════════════════════════════════════════
#  AYUDANTES
# ════════════════════════════════════════════════════════════════

def _sesion_valida():
    """Devuelve (empresa_id, user_id) o (None, None) si no hay sesión."""
    return session.get('empresa_id'), session.get('user_id')


def _es_admin():
    return session.get('user_rol') == 'admin'


def _plantilla_de_mi_empresa(cursor, plantilla_id, empresa_id):
    """Barrera de tenant. Toda ruta que reciba un id pasa por aquí:
    sin esto, cambiar el número en la URL daría acceso a la plantilla
    de otra organización."""
    cursor.execute("""
        SELECT 1 FROM plantillas_informe
        WHERE id = %s AND empresa_id = %s
    """, (plantilla_id, empresa_id))
    return cursor.fetchone() is not None


def _diseno_predeterminado(cursor, empresa_id):
    """Id del diseño por defecto de la empresa, o None."""
    cursor.execute("""
        SELECT id FROM disenos_informe
        WHERE empresa_id = %s AND es_predeterminado
        LIMIT 1
    """, (empresa_id,))
    fila = cursor.fetchone()
    return fila[0] if fila else None


def _nuevo_id_seccion(existentes):
    """Identificador corto y único dentro de la plantilla.

    Las secciones NO se identifican por posición: al arrastrar para
    reordenar, la posición cambia y el id debe seguir apuntando al
    mismo bloque.
    """
    n = 1
    while f"s{n}" in existentes:
        n += 1
    return f"s{n}"


def _limpiar_secciones(crudas):
    """Normaliza y valida lo que llega del constructor.

    Nunca se guarda tal cual lo que manda el navegador: se reconstruye
    sección por sección con solo las claves que el renderizador
    entiende. Lo desconocido se descarta en silencio.
    """
    if not isinstance(crudas, list):
        return []

    limpias, ids = [], set()

    for cruda in crudas[:MAX_SECCIONES]:
        if not isinstance(cruda, dict):
            continue

        tipo = str(cruda.get('tipo') or '').strip()
        if tipo not in TIPOS_BLOQUE:
            continue

        sid = str(cruda.get('id') or '').strip()
        if not sid or sid in ids:
            sid = _nuevo_id_seccion(ids)
        ids.add(sid)

        ancho = cruda.get('ancho')
        if ancho not in ANCHOS_BLOQUE:
            ancho = 'completo'

        config = cruda.get('config')
        if not isinstance(config, dict):
            config = {}

        limpias.append({
            'id':               sid,
            'tipo':             tipo,
            'ancho':            ancho,
            'config':           config,
            'ocultar_si_vacio': bool(cruda.get('ocultar_si_vacio'))
        })

    return limpias


# ════════════════════════════════════════════════════════════════
#  PÁGINA DEL CONSTRUCTOR
# ════════════════════════════════════════════════════════════════

@informes_bp.route('/plantillas-informe/nueva')
@informes_bp.route('/plantillas-informe/<int:plantilla_id>')
def constructor_plantilla(plantilla_id=None):
    """El constructor. La plantilla se carga por API desde el cliente;
    aquí solo se resuelven la marca y los permisos."""
    from app import db_connection

    empresa_id, user_id = _sesion_valida()
    if not user_id:
        return redirect(url_for('login'))
    if not _es_admin():
        return redirect(url_for('configuracion'))

    logo_actual, color_primario = None, '#FFAF33'
    try:
        with db_connection() as (conn, cursor):
            # Si el id no es de esta empresa, se trata como plantilla
            # nueva en lugar de revelar que existe.
            if plantilla_id and not _plantilla_de_mi_empresa(cursor, plantilla_id, empresa_id):
                plantilla_id = None

            cursor.execute("""
                SELECT logo_url, COALESCE(NULLIF(color_primario, ''), '#FFAF33')
                FROM empresas WHERE id = %s
            """, (empresa_id,))
            fila = cursor.fetchone()
            if fila:
                logo_actual, color_primario = fila
    except Exception as e:
        print(f"[PLANTILLAS] Error cargando el constructor: {e}")

    return render_template('plantilla_informe.html',
                           plantilla_id=plantilla_id or 0,
                           logo_actual=logo_actual,
                           color_primario=color_primario,
                           user_rol=session.get('user_rol', ''))


# ════════════════════════════════════════════════════════════════
#  API — PLANTILLAS
# ════════════════════════════════════════════════════════════════

@informes_bp.route('/api/plantillas-informe', methods=['GET'])
def listar_plantillas():
    """Plantillas de la empresa, más lo que necesita el modal de
    creación: proyectos y diseños disponibles."""
    from app import db_connection

    empresa_id, user_id = _sesion_valida()
    if not user_id:
        return jsonify({'error': 'No autorizado'}), 401
    if not _es_admin():
        return jsonify({'error': 'No autorizado'}), 403

    try:
        with db_connection() as (conn, cursor):
            cursor.execute("""
                SELECT p.id, p.nombre, p.descripcion, p.proyecto_id,
                       pr.nombre_proyecto, p.diseno_id, d.nombre,
                       jsonb_array_length(p.secciones) AS bloques,
                       p.activa, p.updated_at
                FROM plantillas_informe p
                LEFT JOIN proyectos pr      ON pr.id = p.proyecto_id
                LEFT JOIN disenos_informe d ON d.id  = p.diseno_id
                WHERE p.empresa_id = %s
                ORDER BY p.nombre
            """, (empresa_id,))

            plantillas = [{
                'id':              r[0],
                'nombre':          r[1],
                'descripcion':     r[2] or '',
                'proyecto_id':     r[3],
                'proyecto_nombre': r[4] or 'Todos los proyectos',
                'diseno_id':       r[5],
                'diseno_nombre':   r[6] or 'Sin diseño',
                'bloques':         r[7] or 0,
                'activa':          r[8],
                'actualizada':     r[9].strftime('%d/%m/%Y %H:%M') if r[9] else ''
            } for r in cursor.fetchall()]

            cursor.execute("""
                SELECT id, nombre_proyecto FROM proyectos
                WHERE empresa_id = %s
                ORDER BY nombre_proyecto
            """, (empresa_id,))
            proyectos = [{'id': r[0], 'nombre': r[1]} for r in cursor.fetchall()]

            cursor.execute("""
                SELECT id, nombre, es_predeterminado FROM disenos_informe
                WHERE empresa_id = %s
                ORDER BY es_predeterminado DESC, nombre
            """, (empresa_id,))
            disenos = [{'id': r[0], 'nombre': r[1], 'predeterminado': r[2]}
                       for r in cursor.fetchall()]

        return jsonify({'success': True, 'plantillas': plantillas,
                        'proyectos': proyectos, 'disenos': disenos})

    except Exception as e:
        print(f"[PLANTILLAS] Error listando: {e}")
        return jsonify({'error': str(e)}), 500


@informes_bp.route('/api/plantillas-informe/<int:plantilla_id>', methods=['GET'])
def obtener_plantilla(plantilla_id):
    """Una plantilla con sus secciones, para abrirla en el constructor."""
    from app import db_connection

    empresa_id, user_id = _sesion_valida()
    if not user_id:
        return jsonify({'error': 'No autorizado'}), 401
    if not _es_admin():
        return jsonify({'error': 'No autorizado'}), 403

    try:
        with db_connection() as (conn, cursor):
            cursor.execute("""
                SELECT p.id, p.nombre, p.descripcion, p.proyecto_id,
                       p.diseno_id, p.secciones, p.activa,
                       d.logo_url,
                       COALESCE(NULLIF(d.color_primario, ''), '#FFAF33')
                FROM plantillas_informe p
                LEFT JOIN disenos_informe d ON d.id = p.diseno_id
                WHERE p.id = %s AND p.empresa_id = %s
            """, (plantilla_id, empresa_id))
            r = cursor.fetchone()
            if not r:
                return jsonify({'error': 'Plantilla no encontrada'}), 404

        return jsonify({'success': True, 'plantilla': {
            'id':          r[0],
            'nombre':      r[1],
            'descripcion': r[2] or '',
            'proyecto_id': r[3],
            'diseno_id':   r[4],
            'secciones':   r[5] or [],
            'activa':      r[6],
            'diseno': {'logo_url': r[7], 'color': r[8]}
        }})

    except Exception as e:
        print(f"[PLANTILLAS] Error obteniendo {plantilla_id}: {e}")
        return jsonify({'error': str(e)}), 500


@informes_bp.route('/api/plantillas-informe', methods=['POST'])
def crear_plantilla():
    """Crea una plantilla. Si no se indica diseño, toma el
    predeterminado de la empresa."""
    from app import db_connection

    empresa_id, user_id = _sesion_valida()
    if not user_id:
        return jsonify({'error': 'No autorizado'}), 401
    if not _es_admin():
        return jsonify({'error': 'No autorizado'}), 403

    data   = request.get_json() or {}
    nombre = (data.get('nombre') or '').strip()
    if not nombre:
        return jsonify({'success': False, 'error': 'Ponle un nombre a la plantilla'}), 400

    try:
        with db_connection() as (conn, cursor):
            diseno_id = data.get('diseno_id') or _diseno_predeterminado(cursor, empresa_id)

            # El proyecto debe ser de esta empresa; si no, queda en NULL
            # (plantilla corporativa) en lugar de rechazar la creación.
            proyecto_id = data.get('proyecto_id') or None
            if proyecto_id:
                cursor.execute("""
                    SELECT 1 FROM proyectos WHERE id = %s AND empresa_id = %s
                """, (proyecto_id, empresa_id))
                if not cursor.fetchone():
                    proyecto_id = None

            secciones = _limpiar_secciones(data.get('secciones') or [])

            cursor.execute("""
                INSERT INTO plantillas_informe
                    (empresa_id, diseno_id, nombre, descripcion,
                     proyecto_id, secciones, creada_por)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (empresa_id, diseno_id, nombre,
                  (data.get('descripcion') or '').strip() or None,
                  proyecto_id, json.dumps(secciones), user_id))
            nuevo_id = cursor.fetchone()[0]
            conn.commit()

        return jsonify({'success': True, 'id': nuevo_id})

    except Exception as e:
        print(f"[PLANTILLAS] Error creando: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@informes_bp.route('/api/plantillas-informe/<int:plantilla_id>', methods=['PUT'])
def guardar_plantilla(plantilla_id):
    """Guarda lo que el constructor tiene en pantalla.

    Se actualiza con COALESCE sobre cada campo: el constructor puede
    mandar solo las secciones sin reenviar nombre y descripción.
    """
    from app import db_connection

    empresa_id, user_id = _sesion_valida()
    if not user_id:
        return jsonify({'error': 'No autorizado'}), 401
    if not _es_admin():
        return jsonify({'error': 'No autorizado'}), 403

    data = request.get_json() or {}

    try:
        with db_connection() as (conn, cursor):
            if not _plantilla_de_mi_empresa(cursor, plantilla_id, empresa_id):
                return jsonify({'error': 'Plantilla no encontrada'}), 404

            # Solo se tocan las secciones si vinieron en la petición.
            secciones_json = None
            if 'secciones' in data:
                secciones_json = json.dumps(_limpiar_secciones(data['secciones']))

            proyecto_id = data.get('proyecto_id')
            if proyecto_id:
                cursor.execute("""
                    SELECT 1 FROM proyectos WHERE id = %s AND empresa_id = %s
                """, (proyecto_id, empresa_id))
                if not cursor.fetchone():
                    proyecto_id = None

            cursor.execute("""
                UPDATE plantillas_informe SET
                    nombre      = COALESCE(NULLIF(%s, ''), nombre),
                    descripcion = COALESCE(%s, descripcion),
                    proyecto_id = %s,
                    diseno_id   = COALESCE(%s, diseno_id),
                    secciones   = COALESCE(%s::jsonb, secciones),
                    activa      = COALESCE(%s, activa)
                WHERE id = %s AND empresa_id = %s
            """, ((data.get('nombre') or '').strip(),
                  data.get('descripcion'),
                  proyecto_id,
                  data.get('diseno_id'),
                  secciones_json,
                  data.get('activa'),
                  plantilla_id, empresa_id))
            conn.commit()

        return jsonify({'success': True})

    except Exception as e:
        print(f"[PLANTILLAS] Error guardando {plantilla_id}: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@informes_bp.route('/api/plantillas-informe/<int:plantilla_id>', methods=['DELETE'])
def eliminar_plantilla(plantilla_id):
    from app import db_connection

    empresa_id, user_id = _sesion_valida()
    if not user_id:
        return jsonify({'error': 'No autorizado'}), 401
    if not _es_admin():
        return jsonify({'error': 'No autorizado'}), 403

    try:
        with db_connection() as (conn, cursor):
            if not _plantilla_de_mi_empresa(cursor, plantilla_id, empresa_id):
                return jsonify({'error': 'Plantilla no encontrada'}), 404

            cursor.execute("""
                DELETE FROM plantillas_informe
                WHERE id = %s AND empresa_id = %s
            """, (plantilla_id, empresa_id))
            conn.commit()

        return jsonify({'success': True})

    except Exception as e:
        print(f"[PLANTILLAS] Error eliminando {plantilla_id}: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@informes_bp.route('/api/plantillas-informe/<int:plantilla_id>/duplicar', methods=['POST'])
def duplicar_plantilla(plantilla_id):
    """Clonar es como se arma la segunda plantilla: se parte de una
    que ya funciona y se cambia lo que haga falta."""
    from app import db_connection

    empresa_id, user_id = _sesion_valida()
    if not user_id:
        return jsonify({'error': 'No autorizado'}), 401
    if not _es_admin():
        return jsonify({'error': 'No autorizado'}), 403

    try:
        with db_connection() as (conn, cursor):
            cursor.execute("""
                INSERT INTO plantillas_informe
                    (empresa_id, diseno_id, nombre, descripcion,
                     proyecto_id, secciones, creada_por)
                SELECT empresa_id, diseno_id, nombre || ' (copia)',
                       descripcion, proyecto_id, secciones, %s
                FROM plantillas_informe
                WHERE id = %s AND empresa_id = %s
                RETURNING id
            """, (user_id, plantilla_id, empresa_id))
            fila = cursor.fetchone()
            if not fila:
                return jsonify({'error': 'Plantilla no encontrada'}), 404
            conn.commit()

        return jsonify({'success': True, 'id': fila[0]})

    except Exception as e:
        print(f"[PLANTILLAS] Error duplicando {plantilla_id}: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ════════════════════════════════════════════════════════════════
#  API — CATÁLOGO DE DATOS INSERTABLES
#  Lo que alimenta el selector "+ Insertar dato" del bloque de texto.
#  El usuario nunca escribe {{...}}: escoge de esta lista.
# ════════════════════════════════════════════════════════════════

DATOS_SISTEMA = [
    {'clave': 'proyecto',        'nombre': 'Nombre del proyecto',   'grupo': 'Proyecto'},
    {'clave': 'empresa',         'nombre': 'Nombre de la empresa',  'grupo': 'Proyecto'},
    {'clave': 'etiqueta',        'nombre': 'Periodo del informe',   'grupo': 'Periodo'},
    {'clave': 'fecha_desde',     'nombre': 'Fecha inicial',         'grupo': 'Periodo'},
    {'clave': 'fecha_hasta',     'nombre': 'Fecha final',           'grupo': 'Periodo'},
    {'clave': 'fecha_generacion','nombre': 'Fecha de generación',   'grupo': 'Periodo'},
    {'clave': 'total',           'nombre': 'Total de registros',    'grupo': 'Cifras'},
    {'clave': 'total_previo',    'nombre': 'Registros del periodo anterior', 'grupo': 'Cifras'},
    {'clave': 'total_usuarios',  'nombre': 'Personas que reportaron','grupo': 'Cifras'},
    {'clave': 'autor',           'nombre': 'Quién genera el informe','grupo': 'Autoría'},
]


@informes_bp.route('/api/plantillas-informe/datos-disponibles', methods=['GET'])
def datos_disponibles():
    """Catálogo para el selector de datos.

    Devuelve los datos del sistema —siempre los mismos— más los campos
    de los formularios que de verdad se usan en el proyecto elegido.
    Sin proyecto, se ofrecen los formularios de toda la empresa.
    """
    from app import db_connection

    empresa_id, user_id = _sesion_valida()
    if not user_id:
        return jsonify({'error': 'No autorizado'}), 401

    proyecto_id = request.args.get('proyecto_id', type=int)

    # Tipos que tiene sentido insertar en una frase. Una imagen o un
    # adjunto no se escriben dentro de un párrafo.
    TIPOS_INSERTABLES = ('texto_corto', 'texto_largo', 'numero', 'moneda',
                         'porcentaje', 'fecha', 'hora', 'booleano',
                         'seleccion', 'seleccion_unica', 'seleccion_dinamica')

    formularios = []
    try:
        with db_connection() as (conn, cursor):
            if proyecto_id:
                # Los que realmente tienen registros en ese proyecto:
                # ofrecer un formulario vacío solo genera huecos.
                cursor.execute("""
                    SELECT DISTINCT f.id, f.nombre, f.campos
                    FROM formularios f
                    JOIN respuestas_formulario rf ON rf.formulario_id = f.id
                    WHERE f.empresa_id = %s AND rf.id_proyecto = %s
                    ORDER BY f.nombre
                """, (empresa_id, proyecto_id))
            else:
                cursor.execute("""
                    SELECT id, nombre, campos FROM formularios
                    WHERE empresa_id = %s
                    ORDER BY nombre
                """, (empresa_id,))

            crudos = cursor.fetchall()

            # Todos los ids de campo de una vez, no una consulta por
            # formulario: con diez formularios serían diez viajes.
            todos_ids = set()
            for _, _, config in crudos:
                for item in (config or []):
                    if isinstance(item, dict) and item.get('tipo') in ('grupo', 'fin_grupo'):
                        continue
                    cid = item.get('id') if isinstance(item, dict) else item
                    if cid:
                        todos_ids.add(cid)

            catalogo = {}
            if todos_ids:
                cursor.execute("""
                    SELECT id, nombre, tipo FROM campos_globales
                    WHERE id = ANY(%s) AND empresa_id = %s
                """, (list(todos_ids), empresa_id))
                catalogo = {r[0]: (r[1], r[2]) for r in cursor.fetchall()}

            for fid, fnombre, config in crudos:
                campos, gid, grupo = [], None, None
                for item in (config or []):
                    if isinstance(item, dict) and item.get('tipo') == 'grupo':
                        gid, grupo = item.get('gid') or '', item.get('nombre') or 'Grupo'
                        continue
                    if isinstance(item, dict) and item.get('tipo') == 'fin_grupo':
                        gid, grupo = None, None
                        continue

                    cid = item.get('id') if isinstance(item, dict) else item
                    if cid not in catalogo:
                        continue
                    nombre, tipo = catalogo[cid]
                    if tipo not in TIPOS_INSERTABLES:
                        continue

                    campos.append({
                        'campo_id': cid,
                        'nombre':   f"{grupo} → {nombre}" if grupo else nombre,
                        'tipo':     tipo,
                        'gid':      gid or ''
                    })

                if campos:
                    formularios.append({'id': fid, 'nombre': fnombre, 'campos': campos})

        return jsonify({'success': True,
                        'sistema': DATOS_SISTEMA,
                        'formularios': formularios})

    except Exception as e:
        print(f"[PLANTILLAS] Error armando el catálogo de datos: {e}")
        return jsonify({'error': str(e)}), 500


# ════════════════════════════════════════════════════════════════
#  API — BLOQUES GUARDADOS
#  El equivalente al "Layout block" de Content Builder: un bloque
#  que la empresa arma una vez y reutiliza en cualquier plantilla.
# ════════════════════════════════════════════════════════════════

@informes_bp.route('/api/bloques-guardados', methods=['GET'])
def listar_bloques():
    from app import db_connection

    empresa_id, user_id = _sesion_valida()
    if not user_id:
        return jsonify({'error': 'No autorizado'}), 401

    try:
        with db_connection() as (conn, cursor):
            cursor.execute("""
                SELECT id, nombre, descripcion, contenido
                FROM bloques_guardados
                WHERE empresa_id = %s
                ORDER BY nombre
            """, (empresa_id,))
            bloques = [{'id': r[0], 'nombre': r[1],
                        'descripcion': r[2] or '', 'contenido': r[3]}
                       for r in cursor.fetchall()]

        return jsonify({'success': True, 'bloques': bloques})

    except Exception as e:
        print(f"[PLANTILLAS] Error listando bloques: {e}")
        return jsonify({'error': str(e)}), 500


@informes_bp.route('/api/bloques-guardados', methods=['POST'])
def crear_bloque():
    """Guarda una sección del constructor como bloque reutilizable."""
    from app import db_connection

    empresa_id, user_id = _sesion_valida()
    if not user_id:
        return jsonify({'error': 'No autorizado'}), 401
    if not _es_admin():
        return jsonify({'error': 'No autorizado'}), 403

    data   = request.get_json() or {}
    nombre = (data.get('nombre') or '').strip()
    if not nombre:
        return jsonify({'success': False, 'error': 'Ponle un nombre al bloque'}), 400

    # Pasa por la misma validación que una plantilla: un bloque
    # guardado es una sección y debe cumplir las mismas reglas.
    limpio = _limpiar_secciones([data.get('contenido') or {}])
    if not limpio:
        return jsonify({'success': False, 'error': 'El bloque no es válido'}), 400

    try:
        with db_connection() as (conn, cursor):
            cursor.execute("""
                INSERT INTO bloques_guardados
                    (empresa_id, nombre, descripcion, contenido, creado_por)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (empresa_id, nombre) DO UPDATE
                    SET contenido = EXCLUDED.contenido,
                        descripcion = EXCLUDED.descripcion
                RETURNING id
            """, (empresa_id, nombre,
                  (data.get('descripcion') or '').strip() or None,
                  json.dumps(limpio[0]), user_id))
            nuevo_id = cursor.fetchone()[0]
            conn.commit()

        return jsonify({'success': True, 'id': nuevo_id})

    except Exception as e:
        print(f"[PLANTILLAS] Error creando bloque: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@informes_bp.route('/api/bloques-guardados/<int:bloque_id>', methods=['DELETE'])
def eliminar_bloque(bloque_id):
    from app import db_connection

    empresa_id, user_id = _sesion_valida()
    if not user_id:
        return jsonify({'error': 'No autorizado'}), 401
    if not _es_admin():
        return jsonify({'error': 'No autorizado'}), 403

    try:
        with db_connection() as (conn, cursor):
            cursor.execute("""
                DELETE FROM bloques_guardados
                WHERE id = %s AND empresa_id = %s
            """, (bloque_id, empresa_id))
            conn.commit()

        return jsonify({'success': True})

    except Exception as e:
        print(f"[PLANTILLAS] Error eliminando bloque {bloque_id}: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500