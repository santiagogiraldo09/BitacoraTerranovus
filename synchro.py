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
import json
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

VUELTA = '/configuracion'


def _volver(resultado):
    """URL de regreso con el resultado del intento.

    El parámetro va ANTES del ancla a propósito: en '/x#y?z=1' el
    navegador considera '?z=1' parte del fragmento, location.search
    queda vacío y la página nunca se entera de cómo fue la conexión.
    """
    return f'{VUELTA}?synchro={resultado}#integraciones'


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
        return redirect(_volver('no_configurado'))

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
        return redirect(_volver('rechazado'))

    estado_recibido = request.args.get('state')
    estado_esperado = session.pop('synchro_state', None)
    if not estado_esperado or estado_recibido != estado_esperado:
        return redirect(_volver('estado_invalido'))

    codigo = request.args.get('code')
    if not codigo:
        return redirect(_volver('sin_codigo'))

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
            return redirect(_volver('error_token'))

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
            return redirect(_volver('sin_refresh'))

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

        return redirect(_volver('ok'))

    except Exception as e:
        current_app.logger.exception('synchro callback')
        return redirect(_volver('error'))


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


# ══════════════════════════════════════════════════════════════════════
#  LECTURA DE SYNCHRO
# ══════════════════════════════════════════════════════════════════════

API = 'https://api.bentley.com'


def _get(token, url, params=None, version='v1'):
    """GET contra la plataforma iTwin con el token del usuario."""
    return requests.get(url, params=params or {}, timeout=TIMEOUT, headers={
        'Authorization': f'Bearer {token}',
        'Accept': f'application/vnd.bentley.itwin-platform.{version}+json',
    })


def _token_actual():
    """Token válido del usuario de la sesión, o None si hay que reconectar.

    Abre su propia conexión porque token_de_usuario() puede renovar y
    escribir en la tabla, y ese UPDATE necesita commit.
    """
    from app import db_connection
    user_id = session.get('user_id')
    if not user_id:
        return None
    with db_connection() as (conn, cursor):
        token = token_de_usuario(cursor, user_id)
        conn.commit()
    return token


@synchro_bp.route('/api/integraciones/synchro/itwins', methods=['GET'])
def listar_itwins():
    """Proyectos de Synchro a los que el usuario tiene acceso."""
    if 'user_id' not in session:
        return jsonify({'error': 'No autorizado'}), 401

    token = _token_actual()
    if not token:
        return jsonify({'error': 'Reconecta tu cuenta de Synchro',
                        'reconectar': True}), 409

    try:
        r = _get(token, f'{API}/itwins/', {
            'subClass': 'Project',
            'status':   'Active',
            '$top':     100,
        }, version='v1')

        if r.status_code != 200:
            current_app.logger.warning("[SYNCHRO] itwins %s: %s",
                                       r.status_code, r.text[:300])
            return jsonify({'error': f'Bentley respondio {r.status_code}'}), 502

        proyectos = [{
            'id':     t.get('id'),
            'nombre': t.get('displayName') or t.get('number') or t.get('id'),
            'numero': t.get('number') or '',
        } for t in (r.json().get('iTwins') or [])]

        return jsonify({'proyectos': proyectos})

    except Exception as e:
        current_app.logger.exception('synchro itwins')
        return jsonify({'error': str(e)}), 500


@synchro_bp.route('/api/integraciones/synchro/formularios', methods=['GET'])
def listar_formularios():
    """Definiciones de formulario de un proyecto de Synchro.

    Sólo las aprobadas: las borradores y las archivadas no deberían
    ofrecerse para que alguien las llene desde Bitácora.
    """
    if 'user_id' not in session:
        return jsonify({'error': 'No autorizado'}), 401

    itwin_id = request.args.get('itwin_id')
    if not itwin_id:
        return jsonify({'error': 'Falta el proyecto'}), 400

    token = _token_actual()
    if not token:
        return jsonify({'error': 'Reconecta tu cuenta de Synchro',
                        'reconectar': True}), 409

    try:
        r = _get(token, f'{API}/forms/form-definitions',
                 {'iTwinId': itwin_id}, version='v2')

        if r.status_code == 404:
            # Bentley usa 404 también cuando falta permiso sobre el iTwin.
            return jsonify({'formularios': [], 'sin_acceso': True})

        if r.status_code != 200:
            current_app.logger.warning("[SYNCHRO] form-definitions %s: %s",
                                       r.status_code, r.text[:300])
            return jsonify({'error': f'Bentley respondio {r.status_code}'}), 502

        formularios = [{
            'id':     f.get('id'),
            'nombre': f.get('displayName') or f.get('id'),
            'tipo':   f.get('type') or '',
        } for f in (r.json().get('formDefinitions') or [])]

        return jsonify({'formularios': formularios})

    except Exception as e:
        current_app.logger.exception('synchro form-definitions')
        return jsonify({'error': str(e)}), 500


@synchro_bp.route('/api/integraciones/synchro/definicion', methods=['GET'])
def ver_definicion():
    """TEMPORAL — vuelca la definición cruda de un formulario.

    Sin parámetros recorre los proyectos del usuario hasta dar con uno
    que tenga formularios aprobados y devuelve el primero. Se puede
    acotar con ?itwin_id= o ?id= si se quiere uno concreto.

    Existe sólo para inspeccionar el formato de 'definition', que la
    documentación de Bentley no describe. Se borra después.
    """
    import json as _j
    if 'user_id' not in session:
        return jsonify({'error': 'No autorizado'}), 401

    token = _token_actual()
    if not token:
        return jsonify({'error': 'Reconecta tu cuenta de Synchro'}), 409

    try:
        definicion_id = request.args.get('id')

        if not definicion_id:
            itwins = ([request.args.get('itwin_id')]
                      if request.args.get('itwin_id') else [])

            if not itwins:
                # Sin filtro de estado: un proyecto en Trial o Inactive sigue
                # siendo un proyecto al que te pueden haber agregado.
                r = _get(token, f'{API}/itwins/',
                        {'subClass': 'Project', '$top': 100},
                        version='v1')
                if r.status_code != 200:
                    return jsonify({'paso': 'itwins', 'estado': r.status_code,
                                    'detalle': r.text[:400]}), 502
                itwins = [t.get('id') for t in (r.json().get('iTwins') or [])]

            if not itwins:
                return jsonify({'error': 'No tienes proyectos en Synchro'}), 404

            for tid in itwins:
                r = _get(token, f'{API}/forms/form-definitions',
                         {'iTwinId': tid}, version='v2')
                if r.status_code != 200:
                    continue
                defs = r.json().get('formDefinitions') or []
                if defs:
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

    except Exception as e:
        current_app.logger.exception('synchro ver_definicion')
        return jsonify({'error': str(e)}), 500


# ══════════════════════════════════════════════════════════════════════
#  INTERPRETACIÓN DE UNA DEFINICIÓN
#
#  Synchro entrega el diseño del formulario como un árbol de controles
#  pensado para su propio editor. Aquí se traduce a la forma que
#  entiende Bitácora: una lista plana de campos con etiqueta, tipo y
#  opciones, que sirve para tres cosas — dibujar el formulario, repartir
#  un dictado entre sus campos, y armar el envío de vuelta.
# ══════════════════════════════════════════════════════════════════════

# Bindings que no son campos propios del formulario sino atributos del
# sistema: viven en la raíz de la instancia, no dentro de 'properties'.
# Synchro no es consistente con el guion bajo: manda 'Status' y
# 'AssignedTo' pelados, pero '_Subject', '_DueDate' y '_AssignedUsers'
# con prefijo. Se registran las dos formas de los que varían.
BINDINGS_SISTEMA = {
    'Status':          'status',
    'AssignedTo':      'assignee',
    '_AssignedUsers':  'assignees',
    '_DueDate':        'dueDate',
    'DueDate':         'dueDate',
    '_Subject':        'subject',
    'Subject':         'subject',
    '_Description':    'description',
    'Description':     'description',
}

# Propiedades que calcula Synchro y que nunca se deben enviar.
PROPIEDADES_SISTEMA = {'_AttachmentCount', '_ExternalFileCount',
                       '_FormCount', 'Project'}


def _tipo_bitacora(tipo_control, props, tipo_dato):
    """Traduce el control de Synchro al vocabulario de campos de Bitácora."""
    if tipo_control == 'TextBox':
        if tipo_dato == 'date':
            return 'fecha'
        if tipo_dato in ('int', 'double', 'number'):
            return 'numero'
        return 'texto_corto' if props.get('IsSingleLine') else 'texto_largo'

    if tipo_control == 'SelectList':
        return 'seleccion_unica' if props.get('IsSingleSelect') else 'seleccion'

    if tipo_control == 'ImageSelector':
        return 'imagen'

    if tipo_control == 'Weather':
        return 'clima'

    if tipo_control.lower() in ('checkbox', 'check', 'toggle'):
        return 'booleano'

    return 'texto_corto'


def _opciones(props):
    """Opciones de una lista desplegable.

    'ListItems' llega como texto: o un arreglo JSON, o el marcador
    '@userlist', que significa "los usuarios del proyecto" y hay que
    resolver con otra llamada.
    """
    bruto = props.get('ListItems')
    if not isinstance(bruto, str) or not bruto.strip():
        return [], False
    if bruto.strip() == '@userlist':
        return [], True
    try:
        valores = json.loads(bruto)
        return ([str(v) for v in valores], False) if isinstance(valores, list) else ([], False)
    except Exception:
        return [], False


def _ruta_de(binding, etiqueta, rutas_conocidas):
    """Dónde vive el valor dentro de la instancia, y con cuánta certeza.

    Devuelve (ruta, origen), donde origen es uno de:
      sistema              atributo conocido de la raíz
      propiedad            ruta confirmada por la API
      supuesta             se aplicó la convención, sin confirmar
      sistema_desconocido  parece interno de Synchro y no sabemos dónde va

    La distinción importa al escribir: sólo las dos primeras son
    seguras de enviar.
    """
    if binding in BINDINGS_SISTEMA:
        return BINDINGS_SISTEMA[binding], 'sistema'

    candidata = f'properties.{etiqueta}'

    if candidata in rutas_conocidas:
        return candidata, 'propiedad'

    # Los bindings con guion bajo son atributos internos. Si no está en
    # la tabla de arriba, no sabemos su sitio: mejor marcarlo que
    # mandarlo a ciegas bajo 'properties'.
    if binding.startswith('_'):
        return candidata, 'sistema_desconocido'

    return candidata, 'supuesta'


def campos_de_definicion(definicion_completa):
    """Lista de campos de un formulario de Synchro.

    Recibe el objeto 'formDefinition' tal como lo devuelve la API, con
    'definition' ya parseado o todavía como texto.
    """
    d = definicion_completa or {}

    arbol = d.get('definition')
    if isinstance(arbol, str):
        try:
            arbol = json.loads(arbol)
        except Exception:
            arbol = {}
    form = (arbol or {}).get('Form') or {}

    # Binding -> etiqueta y tipo del dato
    bindings = {}
    for item in (form.get('DataBindings') or []):
        db = (item or {}).get('DataBinding') or {}
        b = db.get('Binding')
        if b and b not in bindings:
            bindings[b] = {'etiqueta': db.get('DisplayLabel') or b,
                           'tipo': (db.get('Type') or '').lower()}

    rutas = {x.get('property') for x in (d.get('dataBindings') or [])
             if isinstance(x, dict)}

    campos, vistos = [], set()

    def control(tipo_control, props, etiqueta_externa=None,
                requerido_externo=None):
        binding = props.get('Binding')
        if not binding or binding in vistos:
            return
        vistos.add(binding)

        info     = bindings.get(binding, {})
        clave    = info.get('etiqueta') or binding
        tipo_dato = info.get('tipo') or ''

        if clave in PROPIEDADES_SISTEMA:
            return

        ruta, origen = _ruta_de(binding, clave, rutas)
        opciones, es_usuarios = _opciones(props)

        campos.append({
            'binding':   binding,
            'ruta':      ruta,
            'origen':    origen,
            # 'clima' es un control compuesto de Synchro —temperatura,
            # viento, condiciones— sin equivalente en Bitácora.
            'enviable':  origen in ('sistema', 'propiedad')
                         and tipo_control != 'Weather',
            'clave':     clave,
            'etiqueta':  etiqueta_externa or props.get('DisplayLabel') or clave,
            'tipo':      _tipo_bitacora(tipo_control, props, tipo_dato),
            'requerido': bool(requerido_externo if requerido_externo is not None
                              else props.get('IsRequired')),
            'opciones':  opciones,
            'usuarios':  es_usuarios,
            'control':   tipo_control,
        })

    def recorrer(nodos):
        for nodo in (nodos or []):
            if not isinstance(nodo, dict):
                continue
            for tipo_control, props in nodo.items():
                if not isinstance(props, dict):
                    continue

                if tipo_control == 'Panel':
                    recorrer(props.get('Children'))

                elif tipo_control == 'LabeledControl':
                    # El control real va dentro; la etiqueta y el
                    # obligatorio los pone el envoltorio.
                    interno = props.get('Control') or {}
                    for t2, p2 in interno.items():
                        if isinstance(p2, dict):
                            control(t2, p2, props.get('DisplayLabel'),
                                    props.get('IsRequired'))

                elif tipo_control == 'Label':
                    pass        # decorativo

                else:
                    control(tipo_control, props)

    recorrer(form.get('Controls'))
    return campos


@synchro_bp.route('/api/integraciones/synchro/campos', methods=['GET'])
def ver_campos():
    """TEMPORAL — interpreta todas las definiciones del usuario.

    Permite comprobar el parser contra formularios reales antes de
    construir nada encima.
    """
    if 'user_id' not in session:
        return jsonify({'error': 'No autorizado'}), 401

    token = _token_actual()
    if not token:
        return jsonify({'error': 'Reconecta tu cuenta de Synchro'}), 409

    solo = request.args.get('id')

    try:
        r = _get(token, f'{API}/itwins/',
                 {'subClass': 'Project', 'status': 'Active', '$top': 50},
                 version='v1')
        if r.status_code != 200:
            return jsonify({'paso': 'itwins', 'estado': r.status_code}), 502

        salida = []
        for t in (r.json().get('iTwins') or []):
            rd = _get(token, f'{API}/forms/form-definitions',
                      {'iTwinId': t.get('id')}, version='v2')
            if rd.status_code != 200:
                continue

            for df in (rd.json().get('formDefinitions') or []):
                if solo and df.get('id') != solo:
                    continue
                rf = _get(token, f'{API}/forms/form-definitions/{df.get("id")}',
                          version='v2')
                if rf.status_code != 200:
                    continue
                full = (rf.json() or {}).get('formDefinition') or {}
                campos = campos_de_definicion(full)
                salida.append({
                    # Temporal: las rutas que la API declara, para poder
                    # contrastar las que no coincidieron.
                    '_rutas':     sorted(x.get('property') for x
                                         in (full.get('dataBindings') or [])
                                         if isinstance(x, dict)),
                    'proyecto':   t.get('displayName'),
                    'itwin_id':   t.get('id'),
                    'formulario': full.get('displayName'),
                    'id':         full.get('id'),
                    'tipo':       full.get('type'),
                    'campos':     campos,
                    'total':      len(campos),
                })

        # Conteo global: evita tener que leer la salida completa para
        # saber si el parser interpretó bien todas las definiciones.
        resumen = {'formularios': len(salida), 'campos': 0,
                   'no_enviables': [], 'origenes': {}}

        for f in salida:
            for c in f['campos']:
                resumen['campos'] += 1
                o = c['origen']
                resumen['origenes'][o] = resumen['origenes'].get(o, 0) + 1
                if c['origen'] == 'supuesta':
                    resumen.setdefault('supuestos', []).append({
                        'formulario': f['formulario'],
                        'binding':    c['binding'],
                        'clave':      c['clave'],
                        'candidata':  c['ruta'],
                        'declaradas': f['_rutas'],
                    })
                if not c['enviable']:
                    resumen['no_enviables'].append(
                        f"{f['formulario']} · {c['etiqueta']} ({c['control']})")

        return jsonify({'resumen': resumen, 'formularios': salida})

    except Exception as e:
        current_app.logger.exception('synchro ver_campos')
        return jsonify({'error': str(e)}), 500


# ═════════════════════════════════════════════════════════════════════
#  FORMULARIOS VINCULADOS
#
#  El usuario escoge qué formularios de Synchro quiere llenar desde
#  Bitácora. Al vincular se interpreta la definición una sola vez y se
#  guarda la lista de campos: armarla cuesta tres llamadas encadenadas
#  a Bentley (iTwins → definiciones → detalle) y las definiciones casi
#  nunca cambian.
# ═════════════════════════════════════════════════════════════════════

def _descargar_campos(token, definicion_id):
    """Trae una definición y la interpreta. Devuelve (full, campos)."""
    r = _get(token, f'{API}/forms/form-definitions/{definicion_id}',
             version='v2')
    if r.status_code != 200:
        current_app.logger.warning("[SYNCHRO] definicion %s: %s",
                                   r.status_code, r.text[:200])
        return None, None
    full = (r.json() or {}).get('formDefinition') or {}
    return full, campos_de_definicion(full)


def _fila(f):
    """Normaliza una fila de synchro_formularios para el frontend."""
    campos = f[5] or []
    return {
        'definicion_id': f[0],
        'nombre':        f[1],
        'tipo':          f[2],
        'proyecto':      f[3],
        'itwin_id':      f[4],
        'campos':        campos,
        'total':         len([c for c in campos if c.get('enviable')]),
        'actualizado':   f[6].isoformat() if f[6] else None,
    }


@synchro_bp.route('/api/integraciones/synchro/disponibles', methods=['GET'])
def formularios_disponibles():
    """Proyectos de Synchro con sus formularios, marcando los vinculados.

    No descarga el detalle de cada definición: para escoger basta el
    nombre. Los campos se interpretan al vincular, que es cuando de
    verdad se necesitan.
    """
    from app import db_connection

    if 'user_id' not in session:
        return jsonify({'error': 'No autorizado'}), 401

    token = _token_actual()
    if not token:
        return jsonify({'error': 'Reconecta tu cuenta de Synchro'}), 409

    try:
        with db_connection() as (conn, cursor):
            cursor.execute("""
                SELECT definicion_id FROM synchro_formularios
                WHERE user_id = %s
            """, (session['user_id'],))
            vinculados = {x[0] for x in cursor.fetchall()}

        r = _get(token, f'{API}/itwins/',
                 {'subClass': 'Project', 'status': 'Active', '$top': 50},
                 version='v1')
        if r.status_code != 200:
            return jsonify({'error': f'Bentley respondio {r.status_code}'}), 502

        proyectos = []
        for t in (r.json().get('iTwins') or []):
            # El parámetro 'status' vale 'Approved' por defecto: sin
            # 'any' no se ven las definiciones en borrador, en
            # mantenimiento ni archivadas.
            rd = _get(token, f'{API}/forms/form-definitions',
                      {'iTwinId': t.get('id'), 'status': 'any'}, version='v2')

            # Ningún proyecto se descarta: si no trae formularios, se
            # dice por qué. Descartarlos en silencio hace imposible
            # distinguir "no tienes permiso" de "no existe".
            entrada = {
                'itwin_id':    t.get('id'),
                'proyecto':    t.get('displayName'),
                'estado':      t.get('status'),
                'formularios': [],
            }

            if rd.status_code == 404:
                # Bentley usa 404 tanto si el proyecto no tiene Forms
                # habilitado como si tu cuenta no tiene permiso sobre él.
                entrada['aviso'] = 'Sin acceso a los formularios de este proyecto'

            elif rd.status_code != 200:
                entrada['aviso'] = f'Bentley respondio {rd.status_code}'

            else:
                entrada['formularios'] = [{
                    'definicion_id': d.get('id'),
                    'nombre':        d.get('displayName') or d.get('id'),
                    'tipo':          d.get('type') or '',
                    'estado_def':    d.get('status') or '',
                    'vinculado':     d.get('id') in vinculados,
                } for d in (rd.json().get('formDefinitions') or [])]

                if not entrada['formularios']:
                    entrada['aviso'] = 'Este proyecto no tiene formularios'

            proyectos.append(entrada)

        return jsonify({'proyectos': proyectos})

    except Exception as e:
        current_app.logger.exception('synchro disponibles')
        return jsonify({'error': str(e)}), 500


@synchro_bp.route('/api/integraciones/synchro/vinculados', methods=['GET'])
def listar_vinculados():
    """Los formularios ya vinculados. No toca Bentley: sale de la base."""
    from app import db_connection

    if 'user_id' not in session:
        return jsonify({'error': 'No autorizado'}), 401

    try:
        with db_connection() as (conn, cursor):
            cursor.execute("""
                SELECT definicion_id, nombre, tipo, proyecto, itwin_id,
                       campos, actualizado_en
                FROM synchro_formularios
                WHERE user_id = %s
                ORDER BY proyecto, nombre
            """, (session['user_id'],))
            filas = cursor.fetchall()

        return jsonify({'formularios': [_fila(f) for f in filas]})

    except Exception as e:
        current_app.logger.exception('synchro vinculados')
        return jsonify({'error': str(e)}), 500


@synchro_bp.route('/api/integraciones/synchro/vincular', methods=['POST'])
def vincular_formulario():
    """Interpreta una definición y la guarda para usarla desde Bitácora."""
    from app import db_connection

    if 'user_id' not in session:
        return jsonify({'error': 'No autorizado'}), 401

    datos         = request.get_json(silent=True) or {}
    itwin_id      = (datos.get('itwin_id') or '').strip()
    definicion_id = (datos.get('definicion_id') or '').strip()
    proyecto      = (datos.get('proyecto') or '').strip()

    if not itwin_id or not definicion_id:
        return jsonify({'error': 'Faltan itwin_id y definicion_id'}), 400

    token = _token_actual()
    if not token:
        return jsonify({'error': 'Reconecta tu cuenta de Synchro'}), 409

    try:
        full, campos = _descargar_campos(token, definicion_id)
        if full is None:
            return jsonify({'error': 'No se pudo leer la definicion en '
                                     'Synchro'}), 502

        enviables = [c for c in campos if c.get('enviable')]
        if not enviables:
            # Las plantillas de fábrica vienen sin controles: vincularlas
            # dejaría una pantalla vacía.
            return jsonify({'error': 'Ese formulario no tiene campos que '
                                     'se puedan llenar desde Bitacora'}), 422

        with db_connection() as (conn, cursor):
            cursor.execute("""
                INSERT INTO synchro_formularios
                    (empresa_id, user_id, itwin_id, proyecto,
                     definicion_id, nombre, tipo, campos)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                ON CONFLICT (user_id, definicion_id) DO UPDATE SET
                    itwin_id       = EXCLUDED.itwin_id,
                    proyecto       = EXCLUDED.proyecto,
                    nombre         = EXCLUDED.nombre,
                    tipo           = EXCLUDED.tipo,
                    campos         = EXCLUDED.campos,
                    actualizado_en = NOW()
            """, (session.get('empresa_id'), session['user_id'],
                  itwin_id, proyecto, definicion_id,
                  full.get('displayName') or definicion_id,
                  full.get('type') or '', json.dumps(campos)))
            conn.commit()

        return jsonify({'success': True,
                        'nombre': full.get('displayName'),
                        'total': len(enviables)})

    except Exception as e:
        current_app.logger.exception('synchro vincular')
        return jsonify({'error': str(e)}), 500


@synchro_bp.route('/api/integraciones/synchro/vincular/<definicion_id>',
                  methods=['DELETE'])
def desvincular_formulario(definicion_id):
    """Quita la vinculación. No borra nada en Synchro."""
    from app import db_connection

    if 'user_id' not in session:
        return jsonify({'error': 'No autorizado'}), 401

    try:
        with db_connection() as (conn, cursor):
            cursor.execute("""
                DELETE FROM synchro_formularios
                WHERE user_id = %s AND definicion_id = %s
            """, (session['user_id'], definicion_id))
            conn.commit()

        return jsonify({'success': True})

    except Exception as e:
        current_app.logger.exception('synchro desvincular')
        return jsonify({'error': str(e)}), 500


@synchro_bp.route('/api/integraciones/synchro/vinculados/<definicion_id>'
                  '/actualizar', methods=['POST'])
def actualizar_campos(definicion_id):
    """Vuelve a leer la definición por si cambió en Synchro."""
    from app import db_connection

    if 'user_id' not in session:
        return jsonify({'error': 'No autorizado'}), 401

    token = _token_actual()
    if not token:
        return jsonify({'error': 'Reconecta tu cuenta de Synchro'}), 409

    try:
        full, campos = _descargar_campos(token, definicion_id)
        if full is None:
            return jsonify({'error': 'No se pudo leer la definicion'}), 502

        with db_connection() as (conn, cursor):
            cursor.execute("""
                UPDATE synchro_formularios
                SET nombre = %s, tipo = %s, campos = %s::jsonb,
                    actualizado_en = NOW()
                WHERE user_id = %s AND definicion_id = %s
            """, (full.get('displayName') or definicion_id,
                  full.get('type') or '', json.dumps(campos),
                  session['user_id'], definicion_id))
            if cursor.rowcount == 0:
                return jsonify({'error': 'Ese formulario no esta '
                                         'vinculado'}), 404
            conn.commit()

        total = len([c for c in campos if c.get('enviable')])
        return jsonify({'success': True, 'total': total})

    except Exception as e:
        current_app.logger.exception('synchro actualizar campos')
        return jsonify({'error': str(e)}), 500


# ═════════════════════════════════════════════════════════════════════
#  REGISTROS
#
#  Un registro se guarda SIEMPRE en Bitácora antes de intentar subirlo
#  a Synchro. En obra hay mala señal, el token expira y Synchro a veces
#  rechaza: nadie debe perder el trabajo de campo porque el envío falló.
# ═════════════════════════════════════════════════════════════════════

def _vinculado(cursor, user_id, definicion_id):
    """La definición vinculada por este usuario, o None."""
    cursor.execute("""
        SELECT itwin_id, proyecto, nombre, campos
        FROM synchro_formularios
        WHERE user_id = %s AND definicion_id = %s
    """, (user_id, definicion_id))
    return cursor.fetchone()


def _vacio(valor):
    """Un campo obligatorio sin contestar."""
    return valor is None or valor == '' or valor == []


@synchro_bp.route('/api/integraciones/synchro/registros', methods=['POST'])
def guardar_registro():
    """Guarda un formulario lleno. No lo sube todavía: queda pendiente."""
    from app import db_connection

    if 'user_id' not in session:
        return jsonify({'error': 'No autorizado'}), 401

    datos         = request.get_json(silent=True) or {}
    definicion_id = (datos.get('definicion_id') or '').strip()
    entrantes     = datos.get('respuestas')

    if not definicion_id:
        return jsonify({'error': 'Falta definicion_id'}), 400
    if not isinstance(entrantes, dict):
        return jsonify({'error': 'respuestas debe ser un objeto'}), 400

    try:
        with db_connection() as (conn, cursor):
            fila = _vinculado(cursor, session['user_id'], definicion_id)
            if not fila:
                return jsonify({'error': 'Ese formulario no esta vinculado '
                                         'a tu cuenta'}), 404

            itwin_id, proyecto, nombre, campos = fila
            campos = campos or []

            # Solo se guarda lo que la definición declara enviable. Una
            # llave que no esté ahí viene de una pantalla desactualizada
            # o de una petición manipulada, y guardarla sería basura que
            # el envío rechazaría después.
            enviables  = {c['binding'] for c in campos if c.get('enviable')}
            respuestas = {k: v for k, v in entrantes.items() if k in enviables}
            ignoradas  = sorted(set(entrantes) - enviables)

            faltantes = [c.get('etiqueta') or c['binding'] for c in campos
                         if c.get('enviable') and c.get('requerido')
                         and _vacio(respuestas.get(c['binding']))]
            if faltantes:
                return jsonify({'error': 'Faltan campos obligatorios',
                                'campos': faltantes}), 422

            cursor.execute("""
                INSERT INTO synchro_registros
                    (empresa_id, user_id, itwin_id, proyecto,
                     definicion_id, formulario, respuestas)
                VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb)
                RETURNING id
            """, (session.get('empresa_id'), session['user_id'],
                  itwin_id, proyecto, definicion_id, nombre,
                  json.dumps(respuestas)))
            registro_id = cursor.fetchone()[0]
            conn.commit()

        return jsonify({'success': True, 'id': registro_id,
                        'estado': 'pendiente', 'ignoradas': ignoradas})

    except Exception as e:
        current_app.logger.exception('synchro guardar_registro')
        return jsonify({'error': str(e)}), 500


@synchro_bp.route('/api/integraciones/synchro/registros', methods=['GET'])
def listar_registros():
    """Historial del usuario. Con ?definicion_id= se acota a un formulario."""
    from app import db_connection

    if 'user_id' not in session:
        return jsonify({'error': 'No autorizado'}), 401

    definicion_id = request.args.get('definicion_id')

    try:
        with db_connection() as (conn, cursor):
            cursor.execute("""
                SELECT id, definicion_id, formulario, proyecto, respuestas,
                       estado, error, synchro_numero, enviado_en, creado_en
                FROM synchro_registros
                WHERE user_id = %s
                  AND (%s IS NULL OR definicion_id = %s)
                ORDER BY creado_en DESC
                LIMIT 200
            """, (session['user_id'], definicion_id, definicion_id))
            filas = cursor.fetchall()

        return jsonify({'registros': [{
            'id':             f[0],
            'definicion_id':  f[1],
            'formulario':     f[2],
            'proyecto':       f[3],
            'respuestas':     f[4] or {},
            'estado':         f[5],
            'error':          f[6],
            'synchro_numero': f[7],
            'enviado':        f[8].isoformat() if f[8] else None,
            'creado':         f[9].isoformat() if f[9] else None,
        } for f in filas]})

    except Exception as e:
        current_app.logger.exception('synchro listar_registros')
        return jsonify({'error': str(e)}), 500


# ═════════════════════════════════════════════════════════════════════
#  ENVÍO A SYNCHRO
#
#  El sobre sigue el formato documentado para la creación de issues,
#  que es la API de la que nacieron los formularios y con la que
#  comparte estructura: 'formId' obligatorio, los campos de sistema en
#  la raíz y los propios dentro de 'properties'.
# ═════════════════════════════════════════════════════════════════════

# La documentación de creación no es pública y el encabezado Accept
# cambió entre versiones. Se intentan en orden y se registra cuál sirvió.
VERSIONES_CREACION = ('v2', 'v1')


def _post(token, url, cuerpo, version='v2'):
    return requests.post(url, json=cuerpo, timeout=TIMEOUT, headers={
        'Authorization': f'Bearer {token}',
        'Accept':        f'application/vnd.bentley.itwin-platform.{version}+json',
        'Content-Type':  'application/json',
    })


def _valor_synchro(campo, valor):
    """Convierte lo capturado al tipo que espera Bentley."""
    tipo = campo.get('tipo')

    if tipo == 'booleano':
        return bool(valor)

    if tipo == 'numero':
        try:
            n = float(valor)
            return int(n) if n.is_integer() else n
        except (TypeError, ValueError):
            return None

    if tipo == 'fecha':
        # El navegador entrega 'AAAA-MM-DD'; la API espera ISO 8601
        # completo.
        s = str(valor).strip()
        return f'{s}T00:00:00Z' if len(s) == 10 else s

    if tipo == 'seleccion':
        # Multi-selección: valores separados por coma SIN espacios.
        if isinstance(valor, (list, tuple)):
            return ','.join(str(v).strip() for v in valor)
        return str(valor)

    return str(valor)


def _sobre(definicion_id, campos, respuestas):
    """Arma el cuerpo del POST a partir de la definición y lo capturado."""
    cuerpo = {'formId': definicion_id}
    propias = {}

    for c in campos:
        if not c.get('enviable'):
            continue

        binding = c.get('binding')
        if binding not in respuestas:
            continue

        valor = _valor_synchro(c, respuestas[binding])
        if valor is None or valor == '':
            continue

        ruta = c.get('ruta') or ''
        if ruta.startswith('properties.'):
            propias[ruta.split('.', 1)[1]] = valor
        else:
            # Campo de sistema: va en la raíz. Meterlo en 'properties'
            # produce un 422.
            cuerpo[ruta] = valor

    if propias:
        cuerpo['properties'] = propias

    return cuerpo


@synchro_bp.route('/api/integraciones/synchro/registros/<int:registro_id>/enviar',
                  methods=['POST'])
def enviar_registro(registro_id):
    """Sube a Synchro un registro ya guardado en Bitácora."""
    from app import db_connection

    if 'user_id' not in session:
        return jsonify({'error': 'No autorizado'}), 401

    token = _token_actual()
    if not token:
        return jsonify({'error': 'Reconecta tu cuenta de Synchro'}), 409

    try:
        with db_connection() as (conn, cursor):
            cursor.execute("""
                SELECT r.definicion_id, r.respuestas, r.estado, f.campos
                FROM synchro_registros r
                LEFT JOIN synchro_formularios f
                       ON f.user_id = r.user_id
                      AND f.definicion_id = r.definicion_id
                WHERE r.id = %s AND r.user_id = %s
            """, (registro_id, session['user_id']))
            fila = cursor.fetchone()

        if not fila:
            return jsonify({'error': 'Registro no encontrado'}), 404

        definicion_id, respuestas, estado, campos = fila

        if estado == 'enviado':
            return jsonify({'error': 'Ese registro ya esta en Synchro'}), 409
        if not campos:
            return jsonify({'error': 'El formulario ya no esta vinculado. '
                                     'Vinculalo de nuevo para poder enviarlo'}), 409

        cuerpo = _sobre(definicion_id, campos, respuestas or {})

        r = None
        for version in VERSIONES_CREACION:
            r = _post(token, f'{API}/forms/', cuerpo, version=version)
            current_app.logger.warning('[SYNCHRO] crear (%s) -> %s: %s',
                                       version, r.status_code, r.text[:500])
            # Sólo se reintenta cuando el fallo puede ser de versión.
            if r.status_code not in (404, 406, 415):
                break

        if r.status_code in (200, 201):
            datos  = r.json() or {}
            creado = datos.get('formData') or datos.get('issue') or datos
            nuevo  = creado.get('id')
            numero = creado.get('number') or creado.get('displayName')

            with db_connection() as (conn, cursor):
                cursor.execute("""
                    UPDATE synchro_registros
                    SET estado = 'enviado', synchro_form_id = %s,
                        synchro_numero = %s, enviado_en = NOW(),
                        error = NULL, intentos = intentos + 1,
                        actualizado_en = NOW()
                    WHERE id = %s
                """, (nuevo, str(numero) if numero else None, registro_id))
                conn.commit()

            return jsonify({'success': True, 'numero': numero,
                            'synchro_form_id': nuevo})

        # El mensaje de Bentley se guarda tal cual: es lo que dice qué
        # campo sobra, cuál falta o cuál tiene el tipo equivocado.
        detalle = (r.text or '')[:600]
        with db_connection() as (conn, cursor):
            cursor.execute("""
                UPDATE synchro_registros
                SET estado = 'error', error = %s,
                    intentos = intentos + 1, actualizado_en = NOW()
                WHERE id = %s
            """, (f'{r.status_code}: {detalle}', registro_id))
            conn.commit()

        return jsonify({'error': f'Synchro respondio {r.status_code}',
                        'detalle': detalle}), 502

    except Exception as e:
        current_app.logger.exception('synchro enviar_registro')
        return jsonify({'error': str(e)}), 500