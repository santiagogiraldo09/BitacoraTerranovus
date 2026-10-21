# -*- coding: utf-8 -*-
"""
informes.py — Plantillas de informe.

Una plantilla define la PAPELERÍA de un informe, no su contenido:

    Portada      lo que va en la primera hoja
    Encabezado   la franja que se repite arriba, desde la página 2
    Pie          la franja que se repite abajo, desde la página 2

El cuerpo no se diseña aquí: lo produce el informe según lo que se esté
generando. Esa separación es la que hace que una misma plantilla sirva
para un informe de avance, uno de seguridad o uno de paros.

Dos decisiones que sostienen el resto:

El encabezado y el pie tienen TRES RANURAS —izquierda, centro,
derecha— en vez de ser listas libres. Una franja que se repite en cada
página debe tener altura fija; si se pudieran apilar bloques, alguien
pone cinco y el encabezado se come media hoja. Tres ranuras lo hacen
imposible por construcción.

El estilo se escoge de una escala cerrada y no de un campo libre. Un
selector de puntos produce informes con el título en 9,5 y el cuerpo en
14, y vuelve frágil el cálculo de saltos de página en el PDF.
"""

from flask import Blueprint, request, jsonify, session, render_template

informes_bp = Blueprint('informes', __name__)

# ══════════════════════════════════════════════════════════════════════
#  BLOQUES
# ══════════════════════════════════════════════════════════════════════

# tipo -> (nombre visible, icono, ¿cabe en una ranura?)
BLOQUES = {
    'logo':      ('Logo de la empresa', 'fa-image',      True),
    'titulo':    ('Título',             'fa-heading',    True),
    'texto':     ('Texto',              'fa-align-left', True),
    'imagen':    ('Imagen',             'fa-photo-film', True),
    'separador': ('Separador',          'fa-minus',      False),
}

TIPOS_BLOQUE  = set(BLOQUES)
TIPOS_RANURA  = {t for t, (_, _, r) in BLOQUES.items() if r}
RANURAS       = ('izquierda', 'centro', 'derecha')
MAX_PORTADA   = 12
MAX_TEXTO     = 2000

# ── Escala de estilo, cerrada a propósito ────────────────────────────
# El valor en puntos lo usa el renderizador de PDF; el constructor sólo
# maneja las claves.
TAMANOS = {
    'titulo_grande': 24,
    'titulo':        17,
    'subtitulo':     13,
    'normal':        10,
    'pequeno':        8,
}
ALINEACIONES = {'izquierda', 'centro', 'derecha'}
COLORES      = {'marca', 'negro', 'gris'}
ESPACIOS     = {'compacto', 'normal', 'amplio'}

ESTILO_POR_DEFECTO = {
    'tamano':     'normal',
    'alineacion': 'izquierda',
    'negrita':    False,
    'cursiva':    False,
    'color':      'negro',
    'espacio':    'normal',
}

# ── Variables que se reemplazan al generar el informe ────────────────
VARIABLES = [
    {'clave': '{proyecto}',  'descripcion': 'Nombre del proyecto'},
    {'clave': '{empresa}',   'descripcion': 'Nombre de la empresa'},
    {'clave': '{periodo}',   'descripcion': 'Periodo que cubre el informe'},
    {'clave': '{fecha}',     'descripcion': 'Fecha de generación'},
    {'clave': '{desde}',     'descripcion': 'Primer día del periodo'},
    {'clave': '{hasta}',     'descripcion': 'Último día del periodo'},
    {'clave': '{usuario}',   'descripcion': 'Quien genera el informe'},
    {'clave': '{registros}', 'descripcion': 'Cantidad de registros del periodo'},
    {'clave': '{pagina}',    'descripcion': 'Número de página', 'solo_franja': True},
    {'clave': '{paginas}',   'descripcion': 'Total de páginas',  'solo_franja': True},
]


def _bloque(tipo, texto='', **estilo):
    e = dict(ESTILO_POR_DEFECTO)
    e.update(estilo)
    return {'tipo': tipo, 'texto': texto, 'config': {}, 'estilo': e}


# Una plantilla nueva nace usable. Enseña las variables con el ejemplo
# en vez de con un instructivo, y quien no quiera tocar nada ya tiene
# una papelería correcta.
PLANTILLA_POR_DEFECTO = {
    'version': 2,
    'portada': [
        _bloque('logo', alineacion='centro', espacio='amplio'),
        _bloque('titulo', '{proyecto}', tamano='titulo_grande',
                alineacion='centro', negrita=True, espacio='amplio'),
        _bloque('texto', 'Informe de avance · {periodo}',
                tamano='subtitulo', alineacion='centro', color='gris'),
        _bloque('separador', espacio='amplio'),
        _bloque('texto', 'Generado el {fecha}',
                tamano='pequeno', alineacion='centro', color='gris'),
    ],
    'encabezado': {
        'izquierda': _bloque('logo'),
        'centro':    _bloque('texto', '{proyecto}', tamano='pequeno',
                             alineacion='centro', color='gris'),
        'derecha':   _bloque('texto', '{periodo}', tamano='pequeno',
                             alineacion='derecha', color='gris'),
    },
    'pie': {
        'izquierda': _bloque('texto', '{empresa}', tamano='pequeno',
                             color='gris'),
        'centro':    None,
        'derecha':   _bloque('texto', 'Página {pagina} de {paginas}',
                             tamano='pequeno', alineacion='derecha',
                             color='gris'),
    },
}


# ══════════════════════════════════════════════════════════════════════
#  SANEAMIENTO
# ══════════════════════════════════════════════════════════════════════

def _limpiar_estilo(bruto):
    """Reconstruye el estilo con valores de la lista blanca.

    Nunca se guarda lo que llegó: se arma uno nuevo. Así un payload
    manipulado no puede meter CSS, tamaños arbitrarios ni claves extra
    que después el renderizador de PDF tendría que interpretar.
    """
    e = dict(ESTILO_POR_DEFECTO)
    if not isinstance(bruto, dict):
        return e
    if bruto.get('tamano') in TAMANOS:
        e['tamano'] = bruto['tamano']
    if bruto.get('alineacion') in ALINEACIONES:
        e['alineacion'] = bruto['alineacion']
    if bruto.get('color') in COLORES:
        e['color'] = bruto['color']
    if bruto.get('espacio') in ESPACIOS:
        e['espacio'] = bruto['espacio']
    e['negrita'] = bool(bruto.get('negrita'))
    e['cursiva'] = bool(bruto.get('cursiva'))
    return e


def _limpiar_config(tipo, bruto):
    if tipo != 'imagen' or not isinstance(bruto, dict):
        return {}
    url = str(bruto.get('url') or '').strip()
    # Sólo https: una URL http en un PDF que se envía por correo es una
    # advertencia de seguridad esperando a pasar.
    return {'url': url[:500]} if url.startswith('https://') else {}


def _limpiar_bloque(bruto, solo_ranura=False):
    if not isinstance(bruto, dict):
        return None
    tipo = str(bruto.get('tipo') or '').strip()
    if tipo not in TIPOS_BLOQUE:
        return None
    if solo_ranura and tipo not in TIPOS_RANURA:
        return None
    return {
        'tipo':   tipo,
        'texto':  str(bruto.get('texto') or '')[:MAX_TEXTO],
        'config': _limpiar_config(tipo, bruto.get('config')),
        'estilo': _limpiar_estilo(bruto.get('estilo')),
    }


def _limpiar_franja(bruto):
    bruto = bruto if isinstance(bruto, dict) else {}
    return {r: _limpiar_bloque(bruto.get(r), solo_ranura=True)
            for r in RANURAS}


def _limpiar_plantilla(bruto):
    """Deja la plantilla en la forma de tres zonas.

    Acepta también el formato anterior —una lista plana de bloques— y
    lo mete entero en la portada. Sin esto, las plantillas creadas
    antes de este cambio dejarían de abrirse.
    """
    if isinstance(bruto, list):
        bruto = {'portada': bruto}
    if not isinstance(bruto, dict):
        bruto = {}

    portada = []
    for item in (bruto.get('portada') or [])[:MAX_PORTADA]:
        b = _limpiar_bloque(item)
        if b:
            portada.append(b)

    return {
        'version':    2,
        'portada':    portada,
        'encabezado': _limpiar_franja(bruto.get('encabezado')),
        'pie':        _limpiar_franja(bruto.get('pie')),
    }


def _sesion_valida():
    return 'user_id' in session and session.get('empresa_id')


def _es_admin():
    return session.get('user_rol') == 'admin'


def _json(obj):
    import json
    return json.dumps(obj, ensure_ascii=False)


# ══════════════════════════════════════════════════════════════════════
#  PÁGINAS
# ══════════════════════════════════════════════════════════════════════

@informes_bp.route('/plantillas-informe/nueva')
@informes_bp.route('/plantillas-informe/<int:plantilla_id>')
def pagina_plantilla(plantilla_id=None):
    if not _sesion_valida():
        return render_template('PrincipalScreen.html')
    return render_template('plantilla_informe.html',
                           plantilla_id=plantilla_id or 0)


# ══════════════════════════════════════════════════════════════════════
#  API
# ══════════════════════════════════════════════════════════════════════

@informes_bp.route('/api/plantillas-informe', methods=['GET'])
def listar_plantillas():
    from app import db_connection
    from flask import current_app
    if not _sesion_valida():
        return jsonify({'error': 'No autorizado'}), 401

    try:
        with db_connection() as (conn, cursor):
            cursor.execute("""
                SELECT id, nombre, descripcion, secciones,
                       created_at, updated_at
                FROM plantillas_informe
                WHERE empresa_id = %s
                ORDER BY nombre
            """, (session['empresa_id'],))
            filas = cursor.fetchall()

        plantillas = []
        for f in filas:
            s = f[3] or {}
            portada = s.get('portada') if isinstance(s, dict) else s
            plantillas.append({
                'id':             f[0],
                'nombre':         f[1],
                'descripcion':    f[2] or '',
                'bloques':        len(portada or []),
                'creado_en':      f[4].strftime('%d/%m/%Y') if f[4] else '',
                'actualizado_en': f[5].strftime('%d/%m/%Y') if f[5] else '',
            })
        return jsonify({'plantillas': plantillas})
    except Exception as e:
        current_app.logger.exception('listar_plantillas')
        return jsonify({'error': str(e)}), 500


@informes_bp.route('/api/plantillas-informe/<int:plantilla_id>', methods=['GET'])
def obtener_plantilla(plantilla_id):
    from app import db_connection
    from flask import current_app
    if not _sesion_valida():
        return jsonify({'error': 'No autorizado'}), 401

    try:
        with db_connection() as (conn, cursor):
            cursor.execute("""
                SELECT id, nombre, descripcion, secciones
                FROM plantillas_informe
                WHERE id = %s AND empresa_id = %s
            """, (plantilla_id, session['empresa_id']))
            fila = cursor.fetchone()

        if not fila:
            return jsonify({'error': 'Plantilla no encontrada'}), 404

        return jsonify({'plantilla': {
            'id':          fila[0],
            'nombre':      fila[1],
            'descripcion': fila[2] or '',
            'secciones':   _limpiar_plantilla(fila[3]),
        }})
    except Exception as e:
        current_app.logger.exception('obtener_plantilla')
        return jsonify({'error': str(e)}), 500


@informes_bp.route('/api/plantillas-informe', methods=['POST'])
def crear_plantilla():
    from app import db_connection
    from flask import current_app
    if not _sesion_valida():
        return jsonify({'error': 'No autorizado'}), 401
    if not _es_admin():
        return jsonify({'error': 'Solo administradores'}), 403

    data   = request.get_json(silent=True) or {}
    nombre = str(data.get('nombre') or '').strip()[:120]
    if not nombre:
        return jsonify({'error': 'El nombre es obligatorio'}), 400

    # Sin secciones, nace con la papelería por defecto: una hoja en
    # blanco obliga a entender el constructor antes de obtener nada.
    secciones = (_limpiar_plantilla(data['secciones'])
                 if data.get('secciones') else PLANTILLA_POR_DEFECTO)

    try:
        with db_connection() as (conn, cursor):
            cursor.execute("""
                INSERT INTO plantillas_informe
                    (empresa_id, nombre, descripcion, secciones,
                     creada_por, activa)
                VALUES (%s, %s, %s, %s::jsonb, %s, TRUE)
                RETURNING id
            """, (session['empresa_id'], nombre,
                  str(data.get('descripcion') or '').strip()[:300],
                  _json(secciones), session.get('user_id')))
            nuevo_id = cursor.fetchone()[0]
            conn.commit()
        return jsonify({'success': True, 'id': nuevo_id})
    except Exception as e:
        current_app.logger.exception('crear_plantilla')
        return jsonify({'error': str(e)}), 500


@informes_bp.route('/api/plantillas-informe/<int:plantilla_id>', methods=['PUT'])
def editar_plantilla(plantilla_id):
    from app import db_connection
    from flask import current_app
    if not _sesion_valida():
        return jsonify({'error': 'No autorizado'}), 401
    if not _es_admin():
        return jsonify({'error': 'Solo administradores'}), 403

    data   = request.get_json(silent=True) or {}
    nombre = str(data.get('nombre') or '').strip()[:120]
    if not nombre:
        return jsonify({'error': 'El nombre es obligatorio'}), 400

    try:
        with db_connection() as (conn, cursor):
            cursor.execute("""
                UPDATE plantillas_informe
                SET nombre = %s, descripcion = %s,
                    secciones = %s::jsonb, updated_at = NOW()
                WHERE id = %s AND empresa_id = %s
                RETURNING id
            """, (nombre,
                  str(data.get('descripcion') or '').strip()[:300],
                  _json(_limpiar_plantilla(data.get('secciones'))),
                  plantilla_id, session['empresa_id']))
            fila = cursor.fetchone()
            conn.commit()

        if not fila:
            return jsonify({'error': 'Plantilla no encontrada'}), 404
        return jsonify({'success': True})
    except Exception as e:
        current_app.logger.exception('editar_plantilla')
        return jsonify({'error': str(e)}), 500


@informes_bp.route('/api/plantillas-informe/<int:plantilla_id>/duplicar',
                   methods=['POST'])
def duplicar_plantilla(plantilla_id):
    from app import db_connection
    from flask import current_app
    if not _sesion_valida():
        return jsonify({'error': 'No autorizado'}), 401
    if not _es_admin():
        return jsonify({'error': 'Solo administradores'}), 403

    try:
        with db_connection() as (conn, cursor):
            cursor.execute("""
                INSERT INTO plantillas_informe
                    (empresa_id, nombre, descripcion, secciones,
                     creada_por, activa)
                SELECT empresa_id,
                       LEFT(nombre || ' (copia)', 120),
                       descripcion, secciones, %s, TRUE
                FROM plantillas_informe
                WHERE id = %s AND empresa_id = %s
                RETURNING id
            """, (session.get('user_id'), plantilla_id, session['empresa_id']))
            fila = cursor.fetchone()
            conn.commit()

        if not fila:
            return jsonify({'error': 'Plantilla no encontrada'}), 404
        return jsonify({'success': True, 'id': fila[0]})
    except Exception as e:
        current_app.logger.exception('duplicar_plantilla')
        return jsonify({'error': str(e)}), 500


@informes_bp.route('/api/plantillas-informe/<int:plantilla_id>',
                   methods=['DELETE'])
def eliminar_plantilla(plantilla_id):
    from app import db_connection
    from flask import current_app
    if not _sesion_valida():
        return jsonify({'error': 'No autorizado'}), 401
    if not _es_admin():
        return jsonify({'error': 'Solo administradores'}), 403

    try:
        with db_connection() as (conn, cursor):
            cursor.execute("""
                DELETE FROM plantillas_informe
                WHERE id = %s AND empresa_id = %s
                RETURNING id
            """, (plantilla_id, session['empresa_id']))
            fila = cursor.fetchone()
            conn.commit()

        if not fila:
            return jsonify({'error': 'Plantilla no encontrada'}), 404
        return jsonify({'success': True})
    except Exception as e:
        current_app.logger.exception('eliminar_plantilla')
        return jsonify({'error': str(e)}), 500


@informes_bp.route('/api/plantillas-informe/catalogo', methods=['GET'])
def catalogo():
    """Todo lo que el constructor necesita para dibujarse."""
    if not _sesion_valida():
        return jsonify({'error': 'No autorizado'}), 401

    return jsonify({
        'bloques': [{'tipo': t, 'nombre': n, 'icono': i, 'en_ranura': r}
                    for t, (n, i, r) in BLOQUES.items()],
        'ranuras': list(RANURAS),
        'estilo': {
            'tamanos':      list(TAMANOS),
            'alineaciones': sorted(ALINEACIONES),
            'colores':      sorted(COLORES),
            'espacios':     sorted(ESPACIOS),
            'por_defecto':  ESTILO_POR_DEFECTO,
        },
        'variables':   VARIABLES,
        'por_defecto': PLANTILLA_POR_DEFECTO,
        'max_portada': MAX_PORTADA,
    })
