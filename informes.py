# -*- coding: utf-8 -*-
"""
informes.py — Plantillas de informe personalizadas.

El usuario arma su informe con bloques, igual que arma un formulario con
campos. La diferencia es que aquí los bloques ya vienen definidos por IAC:
no hay que crearlos, sólo arrastrarlos y configurarlos.

Dos familias de bloques:

  De contenido   El usuario escribe lo que va dentro (título, texto,
                 imagen, firmas). Sale igual en todos los informes.
  De datos       Se llenan solos al generar el informe (cifras del
                 periodo, indicadores, tabla de registros, resumen IA).
                 El usuario sólo elige el origen.

Esa diferencia es la que convierte esto en una plantilla y no en un
documento: lo que escribe se conserva, lo que es dato se recalcula.

El estilo de cada bloque se escoge de una escala cerrada, no de un campo
libre. Un selector de tamaño en puntos produce informes con el título en
9,5 y el cuerpo en 14, y además vuelve frágil el cálculo de saltos de
página en el PDF. Cinco pasos cubren lo que alguien necesita de verdad.
"""

from flask import Blueprint, request, jsonify, session, render_template

informes_bp = Blueprint('informes', __name__)

# ══════════════════════════════════════════════════════════════════════
#  CATÁLOGO DE BLOQUES
# ══════════════════════════════════════════════════════════════════════

# tipo -> (nombre visible, familia, icono)
BLOQUES = {
    'portada':     ('Portada',               'contenido', 'fa-file-image'),
    'logo':        ('Logo de la empresa',    'contenido', 'fa-image'),
    'titulo':      ('Título',                'contenido', 'fa-heading'),
    'texto':       ('Texto',                 'contenido', 'fa-align-left'),
    'imagen':      ('Imagen',                'contenido', 'fa-photo-film'),
    'separador':   ('Separador',             'contenido', 'fa-minus'),
    'salto_pagina':('Salto de página',       'contenido', 'fa-scissors'),
    'firmas':      ('Firmas',                'contenido', 'fa-signature'),
    'cifras':      ('Cifras del periodo',    'datos',     'fa-chart-simple'),
    'indicadores': ('Indicadores por formulario', 'datos', 'fa-list-check'),
    'tabla':       ('Tabla de registros',    'datos',     'fa-table'),
    'resumen_ia':  ('Resumen con IA',        'datos',     'fa-wand-magic-sparkles'),
}

TIPOS_BLOQUE  = set(BLOQUES)
ANCHOS_BLOQUE = {'completo', 'mitad'}
MAX_SECCIONES = 40
MAX_TEXTO     = 4000

# ── Escala de estilo, cerrada a propósito ────────────────────────────
# El valor en puntos lo usa el renderizador de PDF; el constructor sólo
# maneja las claves.
TAMANOS = {
    'titulo_grande': 20,
    'titulo':        16,
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
]


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
    """Configuración de los bloques de datos, por tipo."""
    if not isinstance(bruto, dict):
        return {}

    def _entero(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    cfg = {}

    if tipo in ('indicadores', 'tabla', 'resumen_ia'):
        fid = _entero(bruto.get('formulario_id'))
        if fid:
            cfg['formulario_id'] = fid

    if tipo == 'tabla':
        columnas = bruto.get('columnas')
        if isinstance(columnas, list):
            cfg['columnas'] = [str(c)[:40] for c in columnas[:12]]
        cfg['max_filas'] = min(max(_entero(bruto.get('max_filas')) or 20, 1), 200)

    if tipo == 'indicadores':
        cfg['max_indicadores'] = min(
            max(_entero(bruto.get('max_indicadores')) or 8, 1), 20)

    if tipo == 'imagen':
        url = str(bruto.get('url') or '').strip()
        if url.startswith('https://'):
            cfg['url'] = url[:500]

    if tipo == 'firmas':
        nombres = bruto.get('nombres')
        if isinstance(nombres, list):
            cfg['nombres'] = [str(n)[:60] for n in nombres[:4]]

    return cfg


def _limpiar_secciones(bruto):
    """Reconstruye la lista de bloques con sólo las claves conocidas."""
    if not isinstance(bruto, list):
        return []

    secciones = []
    for item in bruto[:MAX_SECCIONES]:
        if not isinstance(item, dict):
            continue
        tipo = str(item.get('tipo') or '').strip()
        if tipo not in TIPOS_BLOQUE:
            continue

        ancho = item.get('ancho')
        secciones.append({
            'tipo':   tipo,
            'ancho':  ancho if ancho in ANCHOS_BLOQUE else 'completo',
            'texto':  str(item.get('texto') or '')[:MAX_TEXTO],
            'config': _limpiar_config(tipo, item.get('config')),
            'estilo': _limpiar_estilo(item.get('estilo')),
        })
    return secciones


def _sesion_valida():
    return 'user_id' in session and session.get('empresa_id')


def _es_admin():
    return session.get('user_rol') == 'admin'


# ══════════════════════════════════════════════════════════════════════
#  PÁGINAS
# ══════════════════════════════════════════════════════════════════════

@informes_bp.route('/plantillas-informe/nueva')
@informes_bp.route('/plantillas-informe/<int:plantilla_id>')
def pagina_plantilla(plantilla_id=None):
    """Constructor de plantillas (plantilla_informe.html)."""
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

        plantillas = [{
            'id':            f[0],
            'nombre':        f[1],
            'descripcion':   f[2] or '',
            'bloques':       len(f[3] or []),
            'creado_en':     f[4].strftime('%d/%m/%Y') if f[4] else '',
            'actualizado_en': f[5].strftime('%d/%m/%Y') if f[5] else '',
        } for f in filas]

        return jsonify({'plantillas': plantillas})
    except Exception as e:
        from flask import current_app
        current_app.logger.exception('listar_plantillas')
        return jsonify({'error': str(e)}), 500


@informes_bp.route('/api/plantillas-informe/<int:plantilla_id>', methods=['GET'])
def obtener_plantilla(plantilla_id):
    from app import db_connection
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
            'secciones':   fila[3] or [],
        }})
    except Exception as e:
        from flask import current_app
        current_app.logger.exception('obtener_plantilla')
        return jsonify({'error': str(e)}), 500


@informes_bp.route('/api/plantillas-informe', methods=['POST'])
def crear_plantilla():
    from app import db_connection
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
                INSERT INTO plantillas_informe
                    (empresa_id, nombre, descripcion, secciones,
                     creada_por, activa)
                VALUES (%s, %s, %s, %s::jsonb, %s, TRUE)
                RETURNING id
            """, (session['empresa_id'], nombre,
                  str(data.get('descripcion') or '').strip()[:300],
                  _json(_limpiar_secciones(data.get('secciones'))),
                  session.get('user_id')))
            nuevo_id = cursor.fetchone()[0]
            conn.commit()

        return jsonify({'success': True, 'id': nuevo_id})
    except Exception as e:
        from flask import current_app
        current_app.logger.exception('crear_plantilla')
        return jsonify({'error': str(e)}), 500


@informes_bp.route('/api/plantillas-informe/<int:plantilla_id>', methods=['PUT'])
def editar_plantilla(plantilla_id):
    from app import db_connection
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
                  _json(_limpiar_secciones(data.get('secciones'))),
                  plantilla_id, session['empresa_id']))
            fila = cursor.fetchone()
            conn.commit()

        if not fila:
            return jsonify({'error': 'Plantilla no encontrada'}), 404
        return jsonify({'success': True})
    except Exception as e:
        from flask import current_app
        current_app.logger.exception('editar_plantilla')
        return jsonify({'error': str(e)}), 500


@informes_bp.route('/api/plantillas-informe/<int:plantilla_id>/duplicar',
                   methods=['POST'])
def duplicar_plantilla(plantilla_id):
    from app import db_connection
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
        from flask import current_app
        current_app.logger.exception('duplicar_plantilla')
        return jsonify({'error': str(e)}), 500


@informes_bp.route('/api/plantillas-informe/<int:plantilla_id>',
                   methods=['DELETE'])
def eliminar_plantilla(plantilla_id):
    from app import db_connection
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
        from flask import current_app
        current_app.logger.exception('eliminar_plantilla')
        return jsonify({'error': str(e)}), 500


@informes_bp.route('/api/plantillas-informe/catalogo', methods=['GET'])
def catalogo():
    """Todo lo que el constructor necesita para dibujarse.

    Un solo llamado en lugar de tres: bloques, escala de estilo,
    variables y formularios de la empresa.
    """
    from app import db_connection
    if not _sesion_valida():
        return jsonify({'error': 'No autorizado'}), 401

    formularios = []
    try:
        with db_connection() as (conn, cursor):
            cursor.execute("""
                SELECT id, nombre FROM formularios
                WHERE empresa_id = %s
                ORDER BY nombre
            """, (session['empresa_id'],))
            formularios = [{'id': r[0], 'nombre': r[1]}
                           for r in cursor.fetchall()]
    except Exception:
        from flask import current_app
        current_app.logger.exception('catalogo formularios')

    return jsonify({
        'bloques': [{'tipo': t, 'nombre': n, 'familia': f, 'icono': i}
                    for t, (n, f, i) in BLOQUES.items()],
        'estilo': {
            'tamanos':      list(TAMANOS),
            'alineaciones': sorted(ALINEACIONES),
            'colores':      sorted(COLORES),
            'espacios':     sorted(ESPACIOS),
            'por_defecto':  ESTILO_POR_DEFECTO,
        },
        'variables':   VARIABLES,
        'formularios': formularios,
    })


def _json(obj):
    import json
    return json.dumps(obj, ensure_ascii=False)
