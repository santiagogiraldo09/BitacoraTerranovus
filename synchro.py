# -*- coding: utf-8 -*-
"""
synchro.py — Conexión de cada usuario con Synchro Control (Bentley).

A diferencia de Power BI, que LEE datos de Bitácora, aquí Bitácora
ESCRIBE en Synchro. Por eso la conexión es por usuario y no por
empresa: un formulario creado desde Bitácora debe quedar firmado por
la persona que lo llenó, no por una identidad de servicio. Así la
auditoría de Synchro sigue sirviendo y los permisos los resuelve
Bentley, sin que Bitácora tenga que replicar ninguna regla.

El flujo es OAuth 2.0 con código de autorización:

  1. El usuario pulsa Conectar y se va a la página de Bentley.
  2. Se autentica allá —su contraseña nunca pasa por Bitácora— y
     autoriza la aplicación.
  3. Bentley lo devuelve con un código de un solo uso.
  4. El servidor cambia ese código por un access token (1 hora) y un
     refresh token (larga vida), y guarda el segundo cifrado.

El refresh token se cifra con Fernet antes de tocar la base: es una
credencial de larga vida, y sin cifrar, una fuga de la base entregaría
todas las cuentas de Bentley de golpe.
"""

import os
import time
import secrets
from datetime import datetime, timedelta, timezone

import requests
from flask import (Blueprint, request, jsonify, session,
                   redirect, url_for, current_app)

synchro_bp = Blueprint('synchro', __name__)

# ── Bentley IMS ──────────────────────────────────────────────────────
IMS            = 'https://ims.bentley.com'
URL_AUTORIZAR  = f'{IMS}/connect/authorize'
URL_TOKEN      = f'{IMS}/connect/token'
URL_USERINFO   = f'{IMS}/connect/userinfo'

# 'offline_access' es el que entrega el refresh token. Sin él todo
# funciona hasta que pasa una hora y el usuario aparece desconectado.
SCOPES  = 'itwin-platform offline_access'
TIMEOUT = 15
MARGEN_EXPIRACION = 120     # segundos de colchón antes de renovar

VUELTA = '/configuracion#integraciones'


def _cfg(nombre):
    return os.environ.get(nombre, '')


def _configurado():
    return bool(_cfg('SYNCHRO_CLIENT_ID') and _cfg('SYNCHRO_CLIENT_SECRET')
                and _cfg('SYNCHRO_FERNET_KEY'))


# ══════════════════════════════════════════════════════════════════════
#  CIFRADO
# ══════════════════════════════════════════════════════════════════════

def _fernet():
    from cryptography.fernet import Fernet
    clave = _cfg('SYNCHRO_FERNET_KEY')
    if not clave:
        raise RuntimeError('Falta SYNCHRO_FERNET_KEY')
    return Fernet(clave.encode())


def cifrar(texto):
    return _fernet().encrypt((texto or '').encode()).decode()


def descifrar(texto):
    return _fernet().decrypt((texto or '').encode()).decode()


# ══════════════════════════════════════════════════════════════════════
#  FLUJO DE AUTORIZACIÓN
# ══════════════════════════════════════════════════════════════════════

@synchro_bp.route('/integraciones/synchro/conectar')
def conectar():
    """Manda al usuario a la pantalla de Bentley."""
    if 'user_id' not in session:
        return redirect('/')
    if not _configurado():
        return redirect(VUELTA + '?synchro=no_configurado')

    # El 'state' ata la vuelta a esta sesión: sin él, cualquiera podría
    # provocar que un usuario conecte una cuenta ajena.
    estado = secrets.token_urlsafe(24)
    session['synchro_state'] = estado

    from urllib.parse import urlencode
    params = {
        'client_id':     _cfg('SYNCHRO_CLIENT_ID'),
        'redirect_uri':  _cfg('SYNCHRO_REDIRECT_URI'),
        'response_type': 'code',
        'scope':         SCOPES,
        'state':         estado,
    }
    return redirect(f'{URL_AUTORIZAR}?{urlencode(params)}')


@synchro_bp.route('/integraciones/synchro/callback')
def callback():
    """Recibe el código de Bentley y lo cambia por tokens."""
    from app import db_connection

    if 'user_id' not in session:
        return redirect('/')

    if request.args.get('error'):
        current_app.logger.warning(
            "[SYNCHRO] Bentley rechazó: %s — %s",
            request.args.get('error'),
            request.args.get('error_description'))
        return redirect(VUELTA + '?synchro=rechazado')

    estado_recibido = request.args.get('state')
    estado_esperado = session.pop('synchro_state', None)
    if not estado_esperado or estado_recibido != estado_esperado:
        return redirect(VUELTA + '?synchro=estado_invalido')

    codigo = request.args.get('code')
    if not codigo:
        return redirect(VUELTA + '?synchro=sin_codigo')

    try:
        r = requests.post(URL_TOKEN, timeout=TIMEOUT, data={
            'grant_type':    'authorization_code',
            'code':          codigo,
            'redirect_uri':  _cfg('SYNCHRO_REDIRECT_URI'),
            'client_id':     _cfg('SYNCHRO_CLIENT_ID'),
            'client_secret': _cfg('SYNCHRO_CLIENT_SECRET'),
        })
        if r.status_code != 200:
            current_app.logger.warning("[SYNCHRO] token %s: %s",
                                       r.status_code, r.text[:400])
            return redirect(VUELTA + '?synchro=error_token')

        datos   = r.json()
        acceso  = datos.get('access_token')
        refresh = datos.get('refresh_token')
        expira  = int(datos.get('expires_in') or 3600)

        if not refresh:
            # Sin refresh token la conexión duraría una hora. Mejor
            # avisar que dejar al usuario creyendo que quedó conectado.
            current_app.logger.warning(
                "[SYNCHRO] Bentley no devolvió refresh_token; "
                "revisar que el scope offline_access esté concedido.")
            return redirect(VUELTA + '?synchro=sin_refresh')

        cuenta, sub = _identidad(acceso)

        with db_connection() as (conn, cursor):
            cursor.execute("""
                INSERT INTO synchro_conexiones
                    (empresa_id, user_id, bentley_sub, cuenta,
                     refresh_token, access_token, expira_en)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (user_id) DO UPDATE SET
                    empresa_id     = EXCLUDED.empresa_id,
                    bentley_sub    = EXCLUDED.bentley_sub,
                    cuenta         = EXCLUDED.cuenta,
                    refresh_token  = EXCLUDED.refresh_token,
                    access_token   = EXCLUDED.access_token,
                    expira_en      = EXCLUDED.expira_en,
                    actualizado_en = NOW()
            """, (session.get('empresa_id'), session['user_id'], sub, cuenta,
                  cifrar(refresh), cifrar(acceso),
                  datetime.now(timezone.utc) + timedelta(seconds=expira)))
            conn.commit()

        return redirect(VUELTA + '?synchro=ok')

    except Exception as e:
        current_app.logger.exception('synchro callback')
        return redirect(VUELTA + '?synchro=error')


def _identidad(access_token):
    """Quién es el usuario en Bentley, para mostrarlo en la tarjeta.

    Es informativo: si falla, la conexión sigue siendo válida y se
    muestra sin nombre en vez de romper el flujo entero.
    """
    try:
        r = requests.get(URL_USERINFO, timeout=TIMEOUT,
                         headers={'Authorization': f'Bearer {access_token}'})
        if r.status_code == 200:
            d = r.json()
            return (d.get('email') or d.get('preferred_username')
                    or d.get('name'), d.get('sub'))
    except Exception:
        pass
    return None, None


# ══════════════════════════════════════════════════════════════════════
#  TOKENS DE TRABAJO
# ══════════════════════════════════════════════════════════════════════

def token_de_usuario(cursor, user_id):
    """Access token válido del usuario, renovándolo si hace falta.

    Devuelve None si no hay conexión o si el refresh ya no sirve —por
    ejemplo porque el usuario revocó el acceso en Bentley—. Quien llame
    debe tratar ese None como "hay que reconectar", no como un error.
    """
    cursor.execute("""
        SELECT refresh_token, access_token, expira_en
        FROM synchro_conexiones WHERE user_id = %s
    """, (user_id,))
    fila = cursor.fetchone()
    if not fila:
        return None

    refresh_cif, acceso_cif, expira = fila

    if acceso_cif and expira:
        restante = (expira - datetime.now(timezone.utc)).total_seconds()
        if restante > MARGEN_EXPIRACION:
            try:
                return descifrar(acceso_cif)
            except Exception:
                pass    # clave cambiada: se renueva abajo

    try:
        refresh = descifrar(refresh_cif)
    except Exception:
        current_app.logger.warning(
            "[SYNCHRO] No se pudo descifrar el refresh de %s; "
            "probablemente cambió SYNCHRO_FERNET_KEY.", user_id)
        return None

    try:
        r = requests.post(URL_TOKEN, timeout=TIMEOUT, data={
            'grant_type':    'refresh_token',
            'refresh_token': refresh,
            'client_id':     _cfg('SYNCHRO_CLIENT_ID'),
            'client_secret': _cfg('SYNCHRO_CLIENT_SECRET'),
        })
    except Exception:
        current_app.logger.exception('synchro refresh')
        return None

    if r.status_code != 200:
        current_app.logger.warning("[SYNCHRO] refresh %s: %s",
                                   r.status_code, r.text[:300])
        return None

    d       = r.json()
    acceso  = d.get('access_token')
    expira  = int(d.get('expires_in') or 3600)
    # Bentley puede rotar el refresh token: si manda uno nuevo, el
    # anterior deja de servir y hay que guardarlo.
    nuevo_refresh = d.get('refresh_token') or refresh

    cursor.execute("""
        UPDATE synchro_conexiones
        SET access_token = %s, refresh_token = %s,
            expira_en = %s, actualizado_en = NOW()
        WHERE user_id = %s
    """, (cifrar(acceso), cifrar(nuevo_refresh),
          datetime.now(timezone.utc) + timedelta(seconds=expira), user_id))

    return acceso


# ══════════════════════════════════════════════════════════════════════
#  API PARA LA INTERFAZ
# ══════════════════════════════════════════════════════════════════════

@synchro_bp.route('/api/integraciones/synchro/estado', methods=['GET'])
def estado():
    from app import db_connection
    if 'user_id' not in session:
        return jsonify({'error': 'No autorizado'}), 401

    if not _configurado():
        return jsonify({'configurado': False, 'conectado': False,
                        'cuenta': None})

    try:
        with db_connection() as (conn, cursor):
            cursor.execute("""
                SELECT cuenta, actualizado_en FROM synchro_conexiones
                WHERE user_id = %s
            """, (session['user_id'],))
            fila = cursor.fetchone()
    except Exception:
        current_app.logger.exception('synchro estado')
        return jsonify({'error': 'No se pudo consultar la conexión'}), 500

    if not fila:
        return jsonify({'configurado': True, 'conectado': False,
                        'cuenta': None})

    return jsonify({
        'configurado': True,
        'conectado':   True,
        'cuenta':      fila[0] or 'tu cuenta de Bentley',
        'desde':       fila[1].strftime('%d/%m/%Y') if fila[1] else '',
    })


@synchro_bp.route('/api/integraciones/synchro/desconectar', methods=['POST'])
def desconectar():
    """Borra la conexión local.

    No revoca el permiso en Bentley: eso lo hace el usuario desde su
    cuenta. Aquí sólo se elimina el token, que es lo que Bitácora
    controla.
    """
    from app import db_connection
    if 'user_id' not in session:
        return jsonify({'error': 'No autorizado'}), 401

    try:
        with db_connection() as (conn, cursor):
            cursor.execute("DELETE FROM synchro_conexiones WHERE user_id = %s",
                           (session['user_id'],))
            conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        current_app.logger.exception('synchro desconectar')
        return jsonify({'error': str(e)}), 500

@synchro_bp.route('/api/integraciones/synchro/definicion')
def ver_definicion():
    """Temporal: vuelca la definición cruda de un formulario.

    Sin parámetros recorre los proyectos del usuario hasta dar con uno
    que tenga formularios aprobados y devuelve el primero. Se puede
    acotar con ?itwin_id= o ?id= si se quiere uno concreto.
    """
    import json as _j
    if 'user_id' not in session:
        return jsonify({'error': 'No autorizado'}), 401

    token = _token_actual()
    if not token:
        return jsonify({'error': 'Reconecta tu cuenta de Synchro'}), 409

    definicion_id = request.args.get('id')

    if not definicion_id:
        itwins = [request.args.get('itwin_id')] if request.args.get('itwin_id') else []

        if not itwins:
            r = _get(token, f'{API}/itwins/',
                     {'subClass': 'Project', 'status': 'Active', '$top': 50},
                     version='v1')
            if r.status_code != 200:
                return jsonify({'paso': 'itwins', 'estado': r.status_code,
                                'detalle': r.text[:400]}), 502
            itwins = [t.get('id') for t in (r.json().get('iTwins') or [])]

        if not itwins:
            return jsonify({'error': 'No tienes proyectos en Synchro'}), 404

        encontrados = []
        for tid in itwins:
            r = _get(token, f'{API}/forms/form-definitions',
                     {'iTwinId': tid}, version='v2')
            if r.status_code != 200:
                continue
            defs = r.json().get('formDefinitions') or []
            if defs:
                encontrados.append({'itwin_id': tid, 'formularios': [
                    {'id': d.get('id'), 'nombre': d.get('displayName'),
                     'tipo': d.get('type')} for d in defs]})
                definicion_id = defs[0].get('id')
                break

        if not definicion_id:
            return jsonify({'error': 'Ninguno de tus proyectos tiene '
                                     'formularios aprobados',
                            'proyectos_revisados': len(itwins)}), 404

    r = _get(token, f'{API}/forms/form-definitions/{definicion_id}',
             version='v2')
    if r.status_code != 200:
        return jsonify({'paso': 'definicion', 'estado': r.status_code,
                        'detalle': r.text[:400]}), 502

    d = (r.json() or {}).get('formDefinition') or {}
    bruto = d.get('definition')
    try:
        d['definition'] = _j.loads(bruto) if isinstance(bruto, str) else bruto
    except Exception:
        d['definition_parseo'] = 'no se pudo parsear'

    return jsonify(d)